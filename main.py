import re
import json
import urllib.parse
from pathlib import PurePosixPath
from typing import List, Optional, Set, Union

import aiohttp
from aiohttp import ClientSession, ClientTimeout
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

from . import analysis_bilibili
from .analysis_bilibili import b23_extract, bili_keyword, search_bili_by_title
from .card_renderer import CardRenderer

TEMPLATE_PRESET_EMOJI = (
    "🎬 标题：${标题}\n"
    "👤 UP主：${UP主}\n"
    "📝 简介：${简介}\n"
    "${封面}\n"
    "👍 点赞：${点赞} 🪙 投币：${投币}\n"
    "❤️ 收藏：${收藏} 🔄 转发：${转发}\n"
    "👀 观看：${观看} 💬 弹幕：${弹幕数量}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36 Edg/116.0.1938.69"
}

DEFAULT_TIMEOUT = ClientTimeout(total=15)

BILI_PATTERN = re.compile(
    r"(b23\.tv)|(bili(22|23|33|2233)\.cn)|(\.bilibili\.com)"
    r"|(\b(av|cv)(\d+))|\b(BV([a-zA-Z0-9]{10})+)"
    r"|(\[\[QQ小程序\]哔哩哔哩\])|(QQ小程序&amp;#93;哔哩哔哩)"
    r"|(QQ小程序&#93;哔哩哔哩)",
    re.I,
)

IMAGE_SUFFIXES: Set[str] = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".jfif", ".webp",
}

# 允许的 bilibili 相关域名后缀
_ALLOWED_DOMAINS = (
    "bilibili.com",
    "b23.tv",
    "bilivideo.com",
    "bilivideo.cn",
    "bilivideo.net",
    "hdslb.com",
    "bili2233.cn",
    "bili22.cn",
    "bili23.cn",
    "bili33.cn",
)


def _guess_kind(text: str) -> str:
    """根据链接文本猜测内容类别，供卡片角标使用"""
    low = (text or "").lower()
    if "bangumi" in low:
        return "番剧"
    if "live.bilibili" in low or "xlive" in low:
        return "直播"
    if "/read/" in low or re.search(r"(?<![a-z0-9])cv\d+", low):
        return "专栏"
    if "opus" in low or "/dynamic" in low or "t.bilibili" in low:
        return "动态"
    return "视频"


def _is_allowed_domain(url: str) -> bool:
    """检查 URL 的域名是否在 bilibili 白名单内"""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        host = host.lower().rstrip(".")
        return any(
            host == domain or host.endswith("." + domain)
            for domain in _ALLOWED_DOMAINS
        )
    except Exception:
        return False


def _find_qqdocurl(data: dict) -> str:
    """从已解析的 JSON dict 中查找 bilibili 相关的 qqdocurl"""
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return ""
    for _key, val in meta.items():
        if isinstance(val, dict):
            url = val.get("qqdocurl", "") or val.get("url", "")
            if url and _is_allowed_domain(url):
                return url
    return ""


def _try_parse_json(text: str) -> str:
    """尝试从 JSON 字符串中提取 bilibili URL"""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _find_qqdocurl(data)
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _extract_from_raw_message(raw) -> str:
    """从 raw_message 的各种可能格式中提取 QQ小程序 bilibili URL。

    raw_message 可能是:
    - dict: 已解析的 JSON 卡片
    - list: OneBot 消息段列表 [{"type":"json","data":{"data":"{...}"}}]
    - str: CQ码字符串 或 纯 JSON 字符串
    """
    if raw is None:
        return ""

    # 1) raw 本身是 dict（已解析的 JSON 卡片）
    if isinstance(raw, dict):
        url = _find_qqdocurl(raw)
        if url:
            return url
        # 可能是单个 OneBot 消息段: {"type":"json","data":{"data":"{...}"}}
        if raw.get("type") == "json":
            inner = raw.get("data", {})
            if isinstance(inner, dict):
                json_str = inner.get("data", "")
                if isinstance(json_str, str):
                    url = _try_parse_json(json_str)
                    if url:
                        return url

    # 2) raw 是 list（OneBot 消息段列表）
    if isinstance(raw, list):
        for seg in raw:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "json":
                inner = seg.get("data", {})
                if isinstance(inner, dict):
                    json_str = inner.get("data", "")
                    if isinstance(json_str, str):
                        url = _try_parse_json(json_str)
                        if url:
                            return url
                elif isinstance(inner, str):
                    url = _try_parse_json(inner)
                    if url:
                        return url

    # 3) raw 是 str
    if isinstance(raw, str):
        raw_str = raw.strip()
        # 3a) 纯 JSON 字符串
        if raw_str.startswith("{"):
            url = _try_parse_json(raw_str)
            if url:
                return url
        # 3b) CQ码: [CQ:json,data=...] — data 内容可能经过转义
        cq_match = re.search(r'\[CQ:json,data=(.*?)\]', raw_str, re.S)
        if cq_match:
            cq_data = cq_match.group(1)
            # CQ码中逗号等字符会被转义，&amp; 必须最先解码
            cq_data = (
                cq_data
                .replace("&amp;", "&")
                .replace("&#44;", ",")
                .replace("&#91;", "[")
                .replace("&#93;", "]")
            )
            url = _try_parse_json(cq_data)
            if url:
                return url

    return ""


def _is_image(msg: str) -> bool:
    """判断字符串是否为图片 URL"""
    if not isinstance(msg, str) or not msg:
        return False
    try:
        parsed = urllib.parse.urlparse(msg)
        suffix = PurePosixPath(parsed.path).suffix.lower()
        return suffix in IMAGE_SUFFIXES
    except Exception:
        return False


def _flatten(container):
    """递归展开嵌套列表"""
    for i in container:
        if isinstance(i, (list, tuple)):
            yield from _flatten(i)
        else:
            yield i


# ---------- 合并转发（OneBot forward）辅助 ----------

FORWARD_NICKNAME = "B站解析"


def _normalize_image_url(url: str) -> str:
    """把图片地址规范为可直接访问的 http(s) 地址"""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return f"https:{url}"
    return f"https://{url}"


def _make_node(user_id: str, nickname: str, content: list) -> dict:
    """构造一条 OneBot 合并转发 node（重建式：指定发送者与消息段）"""
    return {
        "type": "node",
        "data": {
            "user_id": str(user_id or "0"),
            "nickname": nickname or "未知用户",
            "content": content,
        },
    }


def _segments_from_parsed(msg) -> list:
    """把解析结果（文本与图片 URL 混合的列表）转为 OneBot 消息段数组"""
    segments: list = []
    text_buffer = ""
    for item in _flatten([msg] if isinstance(msg, str) else msg):
        if not item:
            continue
        if _is_image(item):
            if text_buffer:
                segments.append({"type": "text", "data": {"text": text_buffer}})
                text_buffer = ""
            segments.append(
                {"type": "image", "data": {"file": _normalize_image_url(item)}}
            )
        else:
            text_buffer += str(item)
    if text_buffer:
        segments.append({"type": "text", "data": {"text": text_buffer}})
    return segments


def _format_msg(msg_list: List[Union[List[str], str]]) -> list:
    """将消息列表转换为 AstrBot 消息链"""
    flatten_msg_list = list(_flatten(msg_list))
    chain = []
    text_buffer = ""
    for i in flatten_msg_list:
        if not i:
            continue
        if _is_image(i):
            if text_buffer:
                chain.append(Comp.Plain(text_buffer))
                text_buffer = ""
            if i.startswith("http"):
                url = i
            elif i.startswith("//"):
                url = f"https:{i}"
            else:
                url = f"https://{i}"
            chain.append(Comp.Image.fromURL(url))
        else:
            text_buffer += str(i)
    if text_buffer:
        chain.append(Comp.Plain(text_buffer))
    return chain


@register(
    "astrbot_plugin_bili_resolver",
    "chufeng",
    "bilibili小组件等转链的工具,方便PC查看链接,"
    "因为之前用其他的转链总是被踢下线,所以自己写了个简单版的,"
    "从发布以来还没被踢下线",
    "1.0.5",
    "https://github.com/chufeng/astrbot_plugin_bili_resolver",
)
class BilibiliAnalysis(Star):

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.trust_env = False
        self._session: Optional[ClientSession] = None

        # 功能开关
        self.enable_auto_parse = config.get("enable_auto_parse", True)
        self.enable_search = config.get("enable_search", True)
        self.enable_forward = config.get("enable_forward", True)
        self.enable_render = config.get("enable_render", True)

        # 卡片渲染器（合并转发节点以渲染图片发送）
        self.card_renderer = CardRenderer(
            self,
            style=config.get("renderer_template", "template_2"),
        )

        # 图片开关，同步到 analysis_bilibili 模块
        analysis_bilibili.analysis_display_image = config.get(
            "enable_image", True
        )

        # 视频排版模板（根据预设选择）
        preset = config.get("template_preset", "原始格式")
        if preset == "原始格式":
            analysis_bilibili.analysis_video_template = ""
        elif preset == "简洁风格":
            analysis_bilibili.analysis_video_template = TEMPLATE_PRESET_EMOJI
        else:  # 自定义
            analysis_bilibili.analysis_video_template = config.get(
                "video_template", ""
            )

        # 群组白名单/黑名单
        self.group_whitelist_mode = config.get("group_whitelist_mode", False)
        self.group_list = [str(g) for g in config.get("group_list", [])]

    async def _get_session(self) -> ClientSession:
        """懒初始化并复用 ClientSession"""
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                trust_env=self.trust_env,
                headers=HEADERS,
                timeout=DEFAULT_TIMEOUT,
            )
        return self._session

    def _check_group(self, group_id: str) -> bool:
        """检查群组是否允许使用。返回 True 表示允许。"""
        if not group_id or not self.group_list:
            return True
        if self.group_whitelist_mode:
            return group_id in self.group_list
        else:
            return group_id not in self.group_list

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """自动解析消息中的 Bilibili 链接"""
        if not self.enable_auto_parse:
            return

        # 群组白名单/黑名单检查
        group_id = (
            event.message_obj.group_id if event.message_obj else None
        )
        if not self._check_group(group_id):
            return

        text = event.message_str.strip()

        # 尝试从 QQ小程序 JSON 卡片中提取 URL
        json_url = ""
        if event.message_obj:
            json_url = _extract_from_raw_message(
                event.message_obj.raw_message
            )
            if not json_url and event.message_obj.message:
                for comp in event.message_obj.message:
                    raw = getattr(comp, "raw", None) or getattr(
                        comp, "data", None
                    )
                    if raw:
                        json_url = _extract_from_raw_message(raw)
                        if json_url:
                            break

        # message_str 本身可能就是 JSON
        if not json_url and text.startswith("{"):
            json_url = _try_parse_json(text)

        if json_url:
            logger.info(f"从 JSON 卡片提取到 URL: {json_url}")
            text = json_url
        elif not text or not BILI_PATTERN.search(text):
            return

        try:
            session = await self._get_session()
            if re.search(
                r"(b23\.tv)|(bili(22|23|33|2233)\.cn)", text, re.I
            ):
                text = await b23_extract(text, session=session)

            msg = await bili_keyword(group_id, text, session=session)
        except Exception as e:
            logger.error(f"Bilibili 解析出错: {e!r}", exc_info=True)
            return

        if not msg:
            return

        # 只在有结果后才阻断
        event.stop_event()

        # 合并转发：发送者原文 + 解析结果（渲染卡片图优先）；失败时退回普通回复
        if self.enable_forward:
            result_segments = await self._result_segments(
                msg, _guess_kind(text)
            )
            nodes = self._build_forward_nodes(event, text, result_segments)
            if await self._send_forward(event, group_id, nodes):
                return
            logger.warning("合并转发发送失败，退回普通回复")

        if isinstance(msg, str):
            if msg:
                yield event.plain_result(msg)
            return

        chain = _format_msg(msg)
        if chain:
            yield event.chain_result(chain)

    @filter.command("搜视频")
    async def search_video(self, event: AstrMessageEvent):
        """通过关键词搜索 Bilibili 视频"""
        if not self.enable_search:
            return

        group_id = (
            event.message_obj.group_id if event.message_obj else None
        )
        if not self._check_group(group_id):
            return

        text = event.message_str.strip()
        # 去除指令前缀
        for prefix in ["/搜视频", "搜视频"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        if not text:
            yield event.plain_result("请输入搜索关键词，例如：/搜视频 猫咪")
            return

        event.stop_event()

        try:
            session = await self._get_session()
            search_url = await search_bili_by_title(text, session=session)
            if not search_url:
                yield event.plain_result("未找到相关视频")
                return

            msg = await bili_keyword(group_id, search_url, session=session)
        except Exception as e:
            logger.error(f"Bilibili 搜索出错: {e!r}", exc_info=True)
            yield event.plain_result("搜索出错，请稍后再试")
            return

        if not msg:
            yield event.plain_result("解析失败")
            return

        # 合并转发：仅解析结果节点（渲染卡片图优先）；失败时退回普通回复
        if self.enable_forward:
            result_segments = await self._result_segments(
                msg, _guess_kind(search_url)
            )
            nodes = self._build_forward_nodes(event, "", result_segments)
            if await self._send_forward(event, group_id, nodes):
                return
            logger.warning("合并转发发送失败，退回普通回复")

        if isinstance(msg, str):
            if msg:
                yield event.plain_result(msg)
            return

        chain = _format_msg(msg)
        if chain:
            yield event.chain_result(chain)

    @filter.command("卡片样式")
    async def card_style(self, event: AstrMessageEvent):
        """查看/切换合并转发卡片渲染样式：卡片样式 [样式id或名称]"""
        text = event.message_str.strip()
        for prefix in ["/卡片样式", "卡片样式"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break

        display = {
            tid: self.card_renderer.template_display(tid)
            for tid in self.card_renderer.template_names()
        }
        current = self.card_renderer.get_style()

        if not text:
            lines = [
                f"当前样式：{current}（{display.get(current, '')}）",
                "可用样式：",
            ]
            for tid, name in display.items():
                mark = " ← 当前" if tid == current else ""
                lines.append(f"• {tid} - {name}{mark}")
            yield event.plain_result("\n".join(lines))
            return

        target = None
        for tid, name in display.items():
            if text == tid or text == name:
                target = tid
                break
        if not target:
            yield event.plain_result(
                f"未知样式：{text}。可用：{'、'.join(display.values())}"
            )
            return
        if target == current:
            yield event.plain_result(
                f"当前已是「{display[target]}」样式（{target}）"
            )
            return

        self.card_renderer.set_style(target)
        # 尽力写回配置（部分 AstrBot 版本会在面板保存时持久化）
        try:
            self.config["renderer_template"] = target
        except Exception:
            pass
        yield event.plain_result(
            f"✅ 已切换卡片样式为：{display[target]}（{target}）"
        )

    # ---------- 合并转发 ----------

    @staticmethod
    def _safe_str(value) -> str:
        """把任意值安全转为字符串"""
        try:
            return str(value) if value is not None else ""
        except Exception:
            return ""

    def _build_forward_nodes(
        self, event: AstrMessageEvent, origin_text: str, result_segments: list
    ) -> list:
        """构造合并转发节点列表。

        自动解析场景含两个节点：发送者原文 + 解析结果（文本或渲染卡片图）；
        origin_text 为空（如 /搜视频）时仅含解析结果节点。
        """
        sender_id = self._safe_str(event.get_sender_id())
        sender_name = self._safe_str(event.get_sender_name()) or "群友"
        self_id = self._safe_str(
            getattr(getattr(event, "message_obj", None), "self_id", None)
        )

        nodes = []
        origin_text = (origin_text or "").strip()
        if origin_text:
            nodes.append(
                _make_node(
                    sender_id,
                    sender_name,
                    [{"type": "text", "data": {"text": origin_text}}],
                )
            )
        if result_segments:
            nodes.append(_make_node(self_id, FORWARD_NICKNAME, result_segments))
        return nodes

    async def _result_segments(self, msg, kind: str = "") -> list:
        """构造合并转发「结果节点」的内容段。

        ``enable_render`` 开启且渲染成功时以卡片图片（+原链接）作为节点
        内容；渲染失败或关闭时退回文本 + 封面图段。
        """
        if self.enable_render:
            try:
                img_path, payload = await self.card_renderer.render(msg, kind)
            except Exception as e:
                logger.error(f"卡片渲染异常: {e!r}", exc_info=True)
                img_path, payload = None, None
            if img_path:
                segments = [
                    {"type": "image", "data": {"file": img_path}}
                ]
                url = (payload or {}).get("url")
                if url:
                    segments.append(
                        {"type": "text", "data": {"text": url}}
                    )
                return segments
            logger.warning("卡片渲染不可用，回退文本节点")
        return _segments_from_parsed(msg)

    async def _send_forward(
        self, event: AstrMessageEvent, group_id, nodes: list
    ) -> bool:
        """发送合并转发卡片，成功返回 True；失败（客户端不支持/网络异常等）返回 False。"""
        if not nodes:
            return False
        bot = getattr(event, "bot", None)
        if bot is None:
            return False

        call_action = getattr(bot, "call_action", None)
        if not callable(call_action):
            api = getattr(bot, "api", None)
            call_action = getattr(api, "call_action", None) if api else None
        if not callable(call_action):
            return False

        # 多 OneBot 客户端共享一个适配器时按 self_id 路由
        routing = {}
        self_id = self._safe_str(
            getattr(getattr(event, "message_obj", None), "self_id", None)
        )
        if self_id:
            routing["self_id"] = self_id

        try:
            if group_id:
                await call_action(
                    action="send_group_forward_msg",
                    group_id=str(group_id),
                    messages=nodes,
                    **routing,
                )
            else:
                sender_id = self._safe_str(event.get_sender_id()).strip()
                if not sender_id.isdigit():
                    return False
                await call_action(
                    action="send_private_forward_msg",
                    user_id=sender_id,
                    messages=nodes,
                    **routing,
                )
            return True
        except Exception as e:
            logger.error(f"发送合并转发失败: {e!r}", exc_info=True)
            return False

    async def terminate(self):
        """插件被卸载/停用时调用，关闭持久化 session"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
