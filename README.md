<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_bili_resolver?name=astrbot_plugin_bili_resolver&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_bili_resolver

_✨ bilibili小组件等转链的工具 ✨_

[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0.html)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-4.0%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-chufeng-blue)](https://github.com/chufeng)

</div>

AstrBot 插件 —— 自动解析群聊/私聊中的 B 站链接，返回视频信息摘要。

> 本插件主要是为了避免转链容易被 QQ 踢下线的情况，本人从插件发布到现在还未被踢下线。

## 效果示例

群里有人发了一个 B 站链接或小程序卡片，机器人自动回复：

```
https://www.bilibili.com/video/av114556558967080?p=1

标题："终于知道为什么听到某些歌，反派会愣住了。因为...这也是他们的童年啊..."
小标题：TG-2025-05-23-175551094
类型：XX | UP：一罐蠢乃酱 | https://space.bilibili.com/3546772907493433

播放：359.35万 | 弹幕：2350 | 收藏：12.97万
点赞：28.47万 | 硬币：4.07万 | 评论：2422

简介：-
```

同时附带视频封面图。

## 合并转发

默认情况下，解析回复会以 **QQ 合并转发卡片** 发送（配置项 `enable_forward`，默认开启）：

- 自动解析 B 站链接时：卡片包含「你发送的原文 + 解析结果」两个节点，像一条折叠的聊天记录；
- 使用 `/搜视频` 时：卡片包含解析结果节点；
- 结果节点默认是一张 **HTML 渲染的卡片图片**（见下方「卡片渲染」），附带可点击的原链接；渲染不可用/被关闭时退回文字摘要 + 封面图节点。

若当前 OneBot 客户端不支持合并转发（如缺少 `send_group_forward_msg`），插件会自动退回普通文本/图片回复，不影响使用。关闭 `enable_forward` 后完全恢复为普通直接回复。

## 卡片渲染

当 `enable_forward` 与 `enable_render`（均默认开启）开启时，解析结果会通过 AstrBot 的 `html_render`（t2i 渲染）服务渲染成一张精美的 HTML 卡片图，再作为合并转发的结果节点发送。

- 内置三套模板，可用 `/卡片样式` 指令随时查看/切换（也可用配置项 `renderer_template` 设置）：
  - `template_1` 经典风格
  - `template_2` B站粉风格（默认）
  - `simple` 简约风格
- **前置条件**：需要在 AstrBot 侧配置可用的 html_render / t2i 服务（公共接口或[自部署镜像](https://docs.astrbot.app/others/self-host-t2i.html)）。
- 未配置渲染服务、渲染失败或内容不可渲染时，自动回退为文字 + 封面图节点，不影响使用。
- 注意：合并转发节点内的图片依赖 OneBot 客户端（如 NapCat）对本地图片路径的支持；若客户端不支持会整条退回普通回复。

## 支持的链接格式

| 类型 | 示例 |
|------|------|
| 短链 | `https://b23.tv/xxx` |
| 视频 | `bilibili.com/video/av...` 或 `BV...` |
| 番剧 | `bilibili.com/bangumi/play/ep...` / `ss...` / `md...` |
| 专栏文章 | `bilibili.com/read/cv...` |
| 动态 | `bilibili.com/opus/...` 或 `t.bilibili.com/...` |
| QQ 小程序卡片 | 分享 B 站内容到 QQ 的卡片消息 |

## 指令

| 指令 | 说明 |
|------|------|
| `/搜视频 关键词` | 搜索 B 站视频，返回第一个结果的解析信息 |
| `/卡片样式` | 查看当前渲染卡片样式与可用样式 |
| `/卡片样式 <样式>` | 切换卡片样式，如 `/卡片样式 simple` 或 `/卡片样式 简约风格` |

## 安装

**推荐**：在 AstrBot WebUI 的插件管理页面，搜索 `astrbot_plugin_bili_resolver` 一键安装。

手动安装：将插件目录放入 AstrBot 的 `data/plugins/` 目录下，重启或热重载即可。

## 配置

安装后可在 AstrBot WebUI 插件管理面板中修改，无需编辑文件。

| 配置项 | 类型 | 默认值 | 说明 |
|-------|------|-------|------|
| `enable_auto_parse` | bool | `true` | 自动解析开关 |
| `enable_search` | bool | `true` | `/搜视频` 指令开关 |
| `enable_image` | bool | `true` | 回复中是否显示封面图 |
| `enable_forward` | bool | `true` | 解析结果以 QQ 合并转发卡片发送（客户端不支持时自动退回普通回复） |
| `enable_render` | bool | `true` | 解析结果渲染成 HTML 卡片图片作为合并转发节点（需配合 AstrBot t2i/html_render 服务） |
| `renderer_template` | string | `template_2` | 渲染卡片样式：`template_1` 经典 / `template_2` B站粉 / `simple` 简约，可用 `/卡片样式` 指令切换 |
| `group_whitelist_mode` | bool | `false` | 白名单模式（开启=仅列表中的群生效，关闭=黑名单模式） |
| `group_list` | list | `[]` | 群组 ID 列表 |
| `template_preset` | string | `简洁风格` | 视频解析排版风格，见下方说明 |
| `video_template` | text | `` | 自定义排版模板，仅在 `template_preset` 为 `自定义` 时生效 |

**白名单模式**：只有列表中的群触发，其他群忽略。
**黑名单模式**（默认）：列表中的群不触发，其他群正常。列表为空则所有群生效。

### 视频排版风格

通过 `template_preset` 选择视频解析的输出格式：

**原始格式**：插件内置的纯文字格式。

```
https://www.bilibili.com/video/av114556558967080

标题："终于知道为什么听到某些歌，反派会愣住了"
类型：综合 | UP：一罐蠢乃酱 | https://space.bilibili.com/xxx

播放：359.35万 | 弹幕：2350 | 收藏：12.97万
点赞：28.47万 | 硬币：4.07万 | 评论：2422

简介：-
```

**简洁风格**（默认）：带 Emoji 的卡片格式，同时附带封面图。

```
🎬 标题：终于知道为什么听到某些歌，反派会愣住了
👤 UP主：一罐蠢乃酱
📝 简介：-
[封面图]
👍 点赞：28.47万 🪙 投币：4.07万
❤️ 收藏：12.97万 🔄 转发：1234
👀 观看：359.35万 💬 弹幕：2350
```

**自定义**：在 `video_template` 文本框中填入自定义模板，使用变量占位符自由排版。

支持的变量：

| 变量 | 说明 |
|------|------|
| `${标题}` | 视频标题 |
| `${UP主}` | UP 主名称 |
| `${UP主链接}` | UP 主空间链接 |
| `${简介}` | 视频简介（最多 3 行） |
| `${封面}` | 封面图（渲染为图片，受「显示封面」开关控制） |
| `${点赞}` | 点赞数 |
| `${投币}` | 投币数 |
| `${收藏}` | 收藏数 |
| `${转发}` | 转发数 |
| `${观看}` | 播放数 |
| `${弹幕数量}` | 弹幕数 |
| `${评论}` | 评论数 |
| `${链接}` | 视频链接 |
| `${发布时间}` | 发布时间 |
| `${类型}` | 视频分区 |
| `${BV号}` | BV 号 |
| `${时长}` | 视频时长（格式：`m:ss` / `h:mm:ss`） |
| `${版权}` | 原创 / 转载 |

## 依赖

- Python >= 3.10
- AstrBot
- aiohttp
