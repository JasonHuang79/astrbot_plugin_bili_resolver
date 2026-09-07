"""B 站解析结果卡片渲染器（基于 AstrBot html_render / t2i 服务）。

把解析得到的「文本行 + 图片 URL」混合结果组装成 HTML 卡片数据，
交给 AstrBot 的 ``html_render`` 渲染成一张 JPEG 图片，供合并转发节点
作为图片发送。渲染不可用时调用方应回退到普通文本节点。

模板（HTML + Jinja2）位于本插件 assets/ 下，可运行期热切换。
"""
import asyncio
import base64
import html
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from astrbot.api import logger
from astrbot.api.star import Star

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# 模板注册表：样式 id -> 展示名 / 文件名
CARD_TEMPLATES: Dict[str, Dict[str, str]] = {
    "template_1": {"name": "经典风格", "file": "template_1.html"},
    "template_2": {"name": "B站粉风格", "file": "template_2.html"},
    "simple": {"name": "简约风格", "file": "template_simple.html"},
}
DEFAULT_TEMPLATE = "template_2"

LOGO_FILE = "Astrbot.png"
BANNER_FILE = "banner.png"

MAX_ATTEMPTS = 3
RETRY_DELAY = 2.0

# 图片 URL 后缀（与 main.py 中 IMAGE_SUFFIXES 保持一致）
_IMAGE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".jfif", ".webp",
)

# html_render 渲染参数（与 astrbot_plugin_bilibili 的 renderer 保持一致）
_RENDER_OPTIONS = {
    "full_page": True,
    "viewport_height": 1,
    "type": "jpeg",
    "quality": 95,
    "scale": "device",
    "device_scale_factor_level": "ultra",
}


def _flatten(container):
    """递归展开嵌套列表"""
    for i in container:
        if isinstance(i, (list, tuple)):
            yield from _flatten(i)
        else:
            yield i


def _looks_image_url(value: str) -> bool:
    """判断字符串是否为图片 URL"""
    if not isinstance(value, str) or not value:
        return False
    try:
        from urllib.parse import urlparse
        from pathlib import PurePosixPath
        suffix = PurePosixPath(urlparse(value).path).suffix.lower()
        return suffix in _IMAGE_SUFFIXES
    except Exception:
        return False


def _normalize_image_url(url: str) -> str:
    """把图片地址规范为可直接访问的 http(s) 地址"""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return f"https:{url}"
    return f"https://{url}"


def _clean_quotes(text: str) -> str:
    """去掉标题首尾的引号"""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"“" and text[-1] in "\"”":
        text = text[1:-1].strip()
    return text


class CardRenderer:
    """卡片渲染器：解析结果 -> HTML 卡片 -> 图片文件路径"""

    def __init__(self, star: Star, style: str = DEFAULT_TEMPLATE):
        self.star = star
        self.style = style if style in CARD_TEMPLATES else DEFAULT_TEMPLATE
        self._templates: Dict[str, str] = {}
        self._logo: Optional[str] = None
        self._banner: Optional[str] = None
        self.reload()

    # ---------------- 模板管理 ----------------

    def reload(self) -> None:
        """（重新）加载全部模板文件"""
        self._templates.clear()
        for template_id, meta in CARD_TEMPLATES.items():
            try:
                path = ASSETS_DIR / meta["file"]
                self._templates[template_id] = path.read_text(
                    encoding="utf-8"
                )
            except OSError as e:
                logger.warning(f"加载模板 {template_id} 失败: {e}")

    def set_style(self, style: str) -> bool:
        """切换当前渲染样式，成功返回 True"""
        if style not in CARD_TEMPLATES:
            return False
        self.style = style
        return True

    def get_style(self) -> str:
        return self.style

    @classmethod
    def template_names(cls) -> List[str]:
        return list(CARD_TEMPLATES.keys())

    def template_display(self, template_id: str) -> str:
        meta = CARD_TEMPLATES.get(template_id)
        return meta["name"] if meta else template_id

    # ---------------- 图片素材（base64 内联） ----------------

    def _load_asset_base64(self, filename: str) -> str:
        try:
            raw = (ASSETS_DIR / filename).read_bytes()
        except OSError:
            return ""
        encoded = base64.b64encode(raw).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    def _logo_data(self) -> str:
        if self._logo is None:
            self._logo = self._load_asset_base64(LOGO_FILE)
        return self._logo

    def _banner_data(self) -> str:
        if self._banner is None:
            self._banner = self._load_asset_base64(BANNER_FILE)
        return self._banner

    # ---------------- 解析结果 -> 渲染数据 ----------------

    @staticmethod
    def _extract_title(line: str) -> Optional[str]:
        """从文本行中提取卡片标题；非标题行返回 None"""
        # [直播中]标题：xxx / [未开播]标题：xxx
        m = re.match(r"^(\[[^\]]+\])\s*标题[:：]\s*(.+)$", line)
        if m:
            return f"{m.group(1)} {_clean_quotes(m.group(2))}"
        # 🎬 标题：xxx / 标题：xxx / 标题："xxx"
        m = re.match(r"^(?:🎬\s*)?标题[:：]\s*(.+)$", line)
        if m:
            return _clean_quotes(m.group(1))
        # 番剧：xxx
        m = re.match(r"^番剧[:：]\s*(.+)$", line)
        if m:
            return _clean_quotes(m.group(1))
        return None

    @staticmethod
    def _extract_name(lines: List[str]) -> str:
        """尝试从文本行中提取 UP 主 / 作者 / 主播名"""
        for line in lines:
            m = re.search(
                r"(?:UP主|UP|主播|作者)[:：]\s*"
                r"(.+?)(?=\s*\|\s*|https?://|\s*$)",
                line,
            )
            if m:
                name = m.group(1).strip().strip("|｜ \t")
                if name and "：" not in name:
                    return name
        return ""

    def build_payload(
        self, msg, kind: str = ""
    ) -> Optional[Dict[str, Any]]:
        """把解析结果拆成模板可用的卡片数据。

        返回 None 表示不渲染（错误文本/空内容）。title 取首个标题行，
        纯链接行单独作为 ``url``（便于节点内附带可点击文本）。
        """
        if isinstance(msg, str) or not msg:
            return None

        text_parts: List[str] = []
        image_urls: List[str] = []
        for item in _flatten(msg):
            if not item:
                continue
            value = str(item)
            if _looks_image_url(value):
                image_urls.append(_normalize_image_url(value))
            else:
                text_parts.append(value)

        if not text_parts:
            return None

        # 展开为独立行
        lines: List[str] = []
        for part in text_parts:
            lines.extend(part.splitlines())

        title = ""
        url = ""
        body: List[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 纯链接行 → 记录为 url，不再进入正文
            if not url and re.fullmatch(r"(?:https?://|//)\S+", line):
                url = line
                continue
            # 首个标题行 → title
            if not title:
                extracted = self._extract_title(line)
                if extracted is not None:
                    title = extracted
                    continue
            body.append(line)

        if not body and not title:
            return None

        name = self._extract_name(body)
        text_html = html.escape("\n".join(body)).replace("\n", "<br>")

        return {
            "banner": self._banner_data(),
            "avatar": self._logo_data(),
            "name": name or "B站解析",
            "badge": kind or "视频解析",
            "kind": kind or "动态更新",
            "title": title,
            "text": text_html,
            "image_urls": image_urls,
            "url": url,
        }

    # ---------------- 渲染 ----------------

    @staticmethod
    def _validate_image(img_path: str) -> bool:
        """校验渲染产物是有效图片（Pillow 可用时）"""
        try:
            from PIL import Image

            with Image.open(img_path) as img:
                img.verify()
            return True
        except ImportError:
            return True  # 无 Pillow 时信任文件大小校验
        except Exception as e:
            logger.warning(f"图片校验失败 {img_path}: {e}")
            return False

    async def render(self, msg, kind: str = ""):
        """渲染解析结果为卡片图片。

        返回 ``(图片路径, payload)``；失败或不可渲染时图片路径为 None。
        """
        payload = self.build_payload(msg, kind)
        if not payload:
            return None, None

        tmpl = self._templates.get(self.style) or self._templates.get(
            DEFAULT_TEMPLATE
        )
        if not tmpl:
            logger.error("卡片渲染失败：没有可用的模板")
            return None, payload

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                output = await self.star.html_render(
                    tmpl=tmpl,
                    data=payload,
                    return_url=False,
                    options=_RENDER_OPTIONS,
                )
                if (
                    output
                    and os.path.exists(output)
                    and os.path.getsize(output) > 4096
                    and self._validate_image(output)
                ):
                    return output, payload
            except Exception as e:
                logger.error(
                    f"卡片渲染失败 (第 {attempt} 次尝试): {e!r}"
                )

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY)

        return None, payload
