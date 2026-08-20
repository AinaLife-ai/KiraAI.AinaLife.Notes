# KiraAI.AinaLife.Notes — 温柔纸条（KiraAI 版）

> 由 [AinaLife.Notes](https://github.com/AinaLife-ai/AinaLife.Notes)（Alife 版）移植到 **KiraAI** 插件框架的**温柔便签插件**。

一个让 AI 帮你留纸条、看纸条、删纸条，还能**定时自动生成温柔便签**并渲染成**手写便条图片**发给你的插件。默认签名是「爱奈丽」哦～

![温柔纸条](icon.svg)

## ✨ 功能一览

| 功能 | 说明 |
|---|---|
| 📝 **留纸条** | 让 AI 留一张纸条（温柔话、提醒、心情、待办…），自动渲染成便签图片**发送到当前会话**，不重复念内容 |
| 📋 **看纸条** | 列出所有纸条（带时间），按序号展示 |
| 🗑️ **删纸条** | 按序号删除指定纸条 |
| 🧹 **清空纸条** | 一键清空所有纸条 |
| ⏰ **自动温柔纸条** | 按定时规则由 **AI 自主生成**一句暖心话并保存，不打断当前对话，可发送到**多个目标会话** |
| 🖼️ **手写便签图片** | 纸条自动渲染成「横线活页纸 + 红心 + 手写签名」的便签 PNG，**内置手写字体** |
| 💾 **持久化** | 纸条和下次触发时间存在插件数据目录，重启不丢失 |

## 📦 安装

### 方式一：直接放到插件目录（推荐）

1. 下载本仓库代码（`git clone` 或 Download ZIP），解压后得到文件夹 `KiraAI.AinaLife.Notes-master`，**重命名为 `ainalife_notes`**（目录名 = `manifest.json` 里的 `plugin_id`）
2. 把整个 `ainalife_notes` 文件夹复制到 KiraAI 的 `data/plugins/` 目录下：

```text
data/plugins/
  └── ainalife_notes/
      ├── main.py
      ├── manifest.json
      ├── schema.json
      ├── requirements.txt
      ├── icon.svg
      ├── icon-dark.svg
      ├── fonts/Yozai-Regular-subset.ttf   ← 内置悠哉手写字体（3.9MB）
      └── LICENSE
```

3. 重启 KiraAI（或热重载插件列表），插件会自动安装依赖（`pillow`、`croniter`）并加载
4. 在 WebUI「插件管理」中确认「温柔纸条」已启用（默认启用）

> 💡 目录名必须叫 `KiriAI.AinaLife.Notes` 对应的 `plugin_id`（`ainalife_notes`），否则框架会按目录名当 plugin_id 解析，配置会错位。

## ⚙️ 配置说明

在 WebUI「插件管理 → 温柔纸条 → 配置」中修改，所有配置**保存后即时生效**（插件自动重载）：

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` 启用插件 | 开关 | `true` | 关闭后工具与自动生成全部不生效 |
| `max_notes` 最大纸条数量 | 整数 | `100` | 最多保留多少张，超出自动丢弃最旧的；`0` 表示不限 |
| `auto_schedule` 自动纸条定时 | 文本 | `""` | 空 / `0` / `off` = 关闭；支持**随机间隔式**与 **cron 式**，见下文 |
| `auto_prompt` 自动纸条提示词 | 多行文本 | `""` | 留空使用内置默认（直接输出一句 **0-20 字**暖心话） |
| `gen_model` 生成纸条的模型 | 模型选择 | `""` | 留空 = 跟随 KiraAI 默认模型 |
| `signature` 便条签名 | 文本 | `爱奈丽` | 便签图片右下角的手写签名 |
| `font_family` 便签字体 | 枚举 | `悠哉手写（内置）` | **手写体下拉选择**，见下文字体说明 |
| `send_targets` 自动纸条发送目标会话 | 列表 | `[]` | 每行一个会话标识，见下文；留空 = 仅存档不发送 |

> 💡 **手动纸条**（`note_add`）**始终发送到当前会话**，与 `send_targets` 无关；`send_targets` 只控制**定时自动纸条**的发送去向。

### ✍️ 便签字体（`font_family`）

手写感从强到弱排序，WebUI 下拉直接选：

| 字体 | 说明 |
|---|---|
| **悠哉手写（内置）**（默认） | **Yozai 开源手写体（OFL-1.1）**，随插件分发，无需系统字体，开箱即用 |
| **华文行楷** | 系统字体，行书手写感强（如未安装自动回退） |
| **楷体** / **华文楷体** | 楷书，端正手写感 |
| **方正静蕾简体** | 手写体，女生手账感 |
| **方正喵呜体** | 可爱手写体 |
| **隶书** / **幼圆** | 艺术字体 |
| **微软雅黑** | 兜底印刷体 |

> - 内置的 **Yozai（悠哉）** 是来自 [lxgw/yozai-font](https://github.com/lxgw/yozai-font) 的开源手写字体（OFL-1.1 协议，免费可商用），经 GB2312 常用字子集化后约 3.9MB，随插件包直接分发，**任何系统上都能出真实手写效果，无需联网、无需安装字体**。
> - 选其他系统字体时，缺字体自动按手写感优先级回退（先静态路径查找，再扫描系统字体目录按文件名关键词匹配），最终兜底内置悠哉手写体。

### 📮 发送目标会话格式（`send_targets`）

每行一个**完整会话标识**，格式：`适配器名:类型:ID`

```text
qq:gm:123456789     # QQ 群聊
qq:dm:987654321     # QQ 私聊
telegram:gm:99999   # 其他适配器同理（gm=群聊，dm=私聊）
```

> 适配器名要与你 KiraAI 里配置的**实例名**一致（默认是 `qq`）。可在 WebUI 或会话列表里查看。

### 🕐 定时表达式（`auto_schedule`）

支持两种写法，**二选一**：

**1. 随机间隔式（带 ± 随机偏移）**

```
1h/30m    # 1 小时 ± 30 分钟（随机偏移范围 30 分钟）
2h        # 固定 2 小时
45m       # 固定 45 分钟
1d        # 固定 1 天
3h/90m    # 3 小时 ± 90 分钟
```

格式：`数值 + 单位(h/m/d)`，可选 `/偏移分钟数`。每次触发后都会**重新随机掷下一次时间**。

**2. cron 式（标准 5 字段）**

```
0 9 * * *       # 每天 09:00
*/30 * * * *    # 每 30 分钟
0 8,20 * * *    # 每天 8 点和 20 点
0 10 * * 1-5    # 工作日 10:00
```

> 依赖 `croniter`，安装插件时自动装入。无效表达式会被忽略（相当于关闭自动纸条），日志里会有警告。

## 🤖 AI 可用工具

插件注册后，KiraAI 的 LLM 自动获得以下工具：

| 工具 | 说明 |
|---|---|
| `note_add(content)` | 留一张纸条，**自动渲染成手写便签图片并发送到当前会话**；返回文案不重复纸条内容，避免复读 |
| `note_list()` | 查看所有纸条 |
| `note_delete(index)` | 删除第 N 张纸条（从 1 开始） |
| `note_clear()` | 清空所有纸条 |

> 你可以直接在对话里说「给我留个纸条：记得喝水」，AI 会自己调用 `note_add` 并把便签图片发到这个对话里。

## 🖼️ 便签图片长什么样

- **背景**：米色横线活页纸（浅蓝横线 + 左侧打孔）
- **标题**：自动纸条写「温柔小纸条」，手动纸条写「小纸条」，标题右侧有小爱心
- **正文**：内置悠哉手写体（默认），随选字体；自动换行，最多 4 行 + 省略号
- **右下角**：红色大爱心 + 蓝色手写签名（默认「爱奈丽」，可配置）

图片渲染使用 **Pillow**（自动安装）。

## 🗂️ 数据存储

- 状态文件：`data/plugin_data/ainalife_notes/notes_state.json`（纸条列表 + 下次触发时间戳）
- 便签图片：`data/plugin_data/ainalife_notes/images/*.png`

纸条写入使用 JSON 原子写入（临时文件 + 替换），断电/崩溃也不会损坏。

## 🧠 工作原理

- **定时器**：插件启动后后台每 30 秒检查一次；到点后**先掷出下一次时间并落盘**（防止生成过程中重复触发），再调用 LLM 生成纸条内容，全程**不经过对话流水线**，不打断正在进行的对话
- **LLM 生成**：直接调用 `ctx.get_default_llm_client()`（或你指定的 `gen_model`），System 提示词为「你是一个温柔的数字生命」，默认要求输出 **0-20 字**的暖心话
- **手动纸条发送**：`note_add` 用工具事件携带的 `event.sid` 把便签图片发回**当前会话**
- **自动纸条发送**：遍历 `send_targets` 列表逐一向每个目标会话发送图片
- **防复读**：`note_add` 工具返回值不包含纸条内容本体，避免 Bot 再念一遍便签文字造成复读浪费

## 🔄 与 Alife 原版的差异

| 项目 | Alife 版（AinaLife.Notes） | KiraAI 版（本插件） |
|---|---|---|
| 框架 | Alife 4.2.x / C# | KiraAI ≥ 2.29.7 / Python |
| 定时 | 随机间隔（小时范围） | 随机间隔**或 cron** 两种 |
| 发送目标 | 单目标（类型+ID） | **多会话列表**（可同时发多个群/私聊） |
| 手动纸条 | 配置了目标才发 | **总是发到当前会话**，且不复述内容 |
| 字体 | SkiaSharp 固定楷体 | **内置悠哉手写体默认** + 系统字体可选手写 enum |
| 渲染 | SkiaSharp | Pillow |
| 存储 | Alife StorageSystem | 插件数据目录 JSON |
| 函数 | XmlFunctionCaller | KiraAI 工具注册 |

## 🛠️ 开发 / 调试

```bash
# 语法检查
python -m py_compile main.py
```

日志前缀：`[温柔纸条]`（`core.plugin.logger`）。

## 📄 开源协议

- 插件本体：[GNU Affero General Public License v3.0](LICENSE)（网络服务使用本插件也需开放源码）
- 内置字体：Yozai（悠长）© lxgw，SIL Open Font License 1.1，可自由使用、修改与再分发（不可单独售卖字体文件）

```
Copyright (C) 2026 AinaLife-ai（爱奈丽）
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

## 🙏 致谢

- [Alife](https://github.com/BDFFZI/Alife) 框架与原作者「半点星光」
- [KiraAI](https://github.com/xxynet/KiraAI) 插件框架
- [lxgw/yozai-font](https://github.com/lxgw/yozai-font) 悠长开源手写字体（OFL-1.1）
