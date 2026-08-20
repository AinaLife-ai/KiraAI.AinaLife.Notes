# -*- coding: utf-8 -*-
"""
KiraAI.AinaLife.Notes —— 温柔纸条（KiraAI 移植版）

由 Alife 版 AinaLife.Notes 移植而来，基于 KiraAI 插件框架（core_version >= 2.29.7）：
- 留纸条 / 看纸条 / 删纸条 / 清空（AI 可调用工具）
- 自动温柔纸条：AI 自主生成内容，不打断当前对话
- 定时支持两种表达式：
    1) 随机间隔式：`1h/30m` = 1 小时 ± 30 分钟随机偏移（也支持 2h、45m、1d、3h/90m）
    2) cron 式：标准 5 字段，如 `0 9 * * *`
- 手写便条图片渲染（Pillow），可发送到群聊 / 私聊
- 纸条状态 JSON 持久化到插件数据目录，重启不丢失

License: AGPL-3.0
"""

import asyncio
import json
import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

from core.chat import MessageChain
from core.chat.message_elements import Image
from core.chat.message_utils import KiraMessageBatchEvent
from core.plugin import BasePlugin, logger, register
from core.provider import LLMRequest

try:
    from croniter import croniter
except Exception:
    croniter = None

try:
    from PIL import Image as PILImage
    from PIL import ImageDraw, ImageFont
except Exception:
    PILImage = None

STATE_FILE = "notes_state.json"

DEFAULT_PROMPT = (
    "现在又到了生成温柔纸条的时间。\n"
    "请直接输出一句给用户的暖心话（不要解释、不要寒暄、不要带前缀，直接给纸条内容本身，"
    "严格控制在 0-20 字以内，像「今天天气真好」这样自然简短的口吻）。"
)

FONT_FILES = {
    "华文行楷": ["C:/Windows/Fonts/STXINGKA.TTF", "C:/Windows/Fonts/STXINGKE.TTF"],
    "楷体": ["C:/Windows/Fonts/simkai.ttf", "/usr/share/fonts/truetype/arphic/ukai.ttc"],
    "华文楷体": ["C:/Windows/Fonts/STKAITI.TTF", "/usr/share/fonts/truetype/arphic/ukai.ttc"],
    "方正静蕾简体": ["C:/Windows/Fonts/FZJLJW.TTF", "C:/Windows/Fonts/FZJL_GBK.TTF", "C:/Windows/Fonts/FZJL.TTF"],
    "方正喵呜体": ["C:/Windows/Fonts/FZMWBJW.TTF", "C:/Windows/Fonts/FZMWB_GBK.TTF"],
    "隶书": ["C:/Windows/Fonts/simli.ttf"],
    "幼圆": ["C:/Windows/Fonts/simyuan.ttf"],
    "微软雅黑": ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"],
}

# 配置的字体缺失时按此链回退（内置悠哉手写体为最终兜底，保证永远有手写体）
FONT_FALLBACK = ["悠哉手写（内置）", "华文行楷", "楷体", "华文楷体"]

# 内置兜底字体：悠哉字体（Yozai，OFL-1.1 开源可再分发）
# 基于 Y.OzFont 的手写风格衍生字体，GB2312 子集化后约 3.9MB，随插件包分发
BUNDLED_FONT_FAMILY = "悠哉手写（内置）"
BUNDLED_FONT_FILE = "Yozai-Regular-subset.ttf"
BUNDLED_FONT_URLS = [
    "https://cdn.jsdelivr.net/gh/AinaLife-ai/KiraAI.AinaLife.Notes@main/fonts/Yozai-Regular-subset.ttf",
    "https://raw.githubusercontent.com/AinaLife-ai/KiraAI.AinaLife.Notes/main/fonts/Yozai-Regular-subset.ttf",
]
# 满足任意一个即视为「系统已有手写字体」，无需预装内置字体
HANDWRITTEN_FAMILIES = ["悠哉手写（内置）", "华文行楷", "楷体", "华文楷体", "方正静蕾简体", "方正喵呜体"]

# 字体目录扫描关键词：family 名 -> 文件名匹配关键词（不区分大小写）
FONT_FILE_KEYWORDS = {
    "华文行楷": ["xingkai", "stxingk"],
    "楷体": ["simkai", "kaiu", "ukai"],
    "华文楷体": ["stkaiti", "kaiti"],
    "方正静蕾简体": ["fzjl", "jinglei"],
    "方正喵呜体": ["fzmw", "miaowu"],
    "隶书": ["simli", "lishu"],
    "幼圆": ["simyou", "simyuan", "youyuan"],
    "微软雅黑": ["msyh", "yahei"],
}
FONT_SCAN_DIRS = ["C:/Windows/Fonts", "/usr/share/fonts", "/usr/local/share/fonts"]


def parse_schedule(expr):
    """解析定时表达式，返回描述元组；无效 / 空返回 None（关闭）。

    - 随机间隔式: `1h[5m]`、`2h`、`45m`、`1d`，可带 `/` 随机偏移（偏移单位固定为分钟）
    - cron 式: 标准 5 字段，如 `0 9 * * *`、`*/30 * * * *`
    """
    if not expr:
        return None
    s = expr.strip()
    if not s or s in ("0", "off", "false", "no"):
        return None

    parts = s.split()
    if len(parts) == 5 and any(ch in s for ch in ("*", "?")):
        return ("cron", s)

    m = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(h|m|d)(?:\s*/\s*(\d+(?:\.\d+)?)\s*m)?",
        s,
        re.IGNORECASE,
    )
    if m:
        base = float(m.group(1)) * {"h": 3600, "m": 60, "d": 86400}[m.group(2).lower()]
        jitter = float(m.group(3)) * 60 if m.group(3) else 0.0
        return ("interval", base, jitter)
    return None


def compute_next(schedule, now_ts):
    """根据 schedule 计算下一次触发时间戳。"""
    if schedule is None:
        return None
    if schedule[0] == "interval":
        _, base, jitter = schedule
        nxt = now_ts + base + (random.uniform(-jitter, jitter) if jitter > 0 else 0)
        return max(nxt, now_ts + 30)
    if schedule[0] == "cron":
        if croniter is None:
            return None
        try:
            # 注意：get_next(float) 会把 naive datetime 当 UTC 处理导致时区偏移，
            # 这里用 get_next(datetime) 拿本地 naive 结果再转 epoch，语义一致
            nxt = croniter(schedule[1], datetime.fromtimestamp(now_ts)).get_next(datetime)
            return nxt.timestamp()
        except Exception:
            return None
    return None


class AinaNotesPlugin(BasePlugin):
    """温柔纸条：留/看/删/清空 + 自动生成温柔便签，可渲染手写便条图片发送。"""

    def __init__(self, ctx, cfg: dict):
        super().__init__(ctx, cfg)
        basic = cfg.get("section_basic", {})
        if not isinstance(basic, dict):
            basic = {}
        self.enabled = bool(basic.get("enabled", True))
        self.max_notes = int(basic.get("max_notes", 100) or 100)
        self.schedule_expr = str(basic.get("auto_schedule", "") or "").strip()
        self.auto_prompt = str(basic.get("auto_prompt", "") or "").strip()
        self.gen_model = str(basic.get("gen_model", "") or "").strip()
        self.signature = str(basic.get("signature", "爱奈丽") or "爱奈丽").strip()
        self.font_family = str(basic.get("font_family", BUNDLED_FONT_FAMILY) or BUNDLED_FONT_FAMILY).strip()
        raw_targets = basic.get("send_targets", []) or []
        self.send_targets = [str(x).strip() for x in raw_targets if str(x).strip()]

        self.data_dir = Path(self.ctx.get_plugin_data_dir())
        self.state_path = self.data_dir / STATE_FILE
        self.notes = []
        self.next_at = None
        self.generating = False
        self._task = None

    # ---------- 生命周期 ----------

    async def initialize(self):
        if croniter is None:
            logger.warning("[温柔纸条] croniter 未安装，cron 表达式将不可用（requirements.txt 会自动安装）")
        if PILImage is None:
            logger.warning("[温柔纸条] Pillow 未安装，便签图片渲染不可用（requirements.txt 会自动安装）")
        self._load_state()
        self._task = asyncio.create_task(self._loop())
        # 后台预装内置手写字体（不阻塞启动；下载失败自动忽略）
        asyncio.create_task(self._ensure_bundled_font())
        logger.info(
            "[温柔纸条] 已启动 notes=%d schedule=%s send=%s",
            len(self.notes),
            self.schedule_expr or "关闭",
            ",".join(self.send_targets) if self.send_targets else "-",
        )

    async def terminate(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---------- 工具 ----------

    @register.tool(
        name="note_add",
        description=(
            "留下一张纸条（温柔话、提醒、心情、待办等）。纸条会保存到列表，"
            "并渲染成手写便签图片直接发送到当前会话。"
        ),
        params={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "纸条内容"},
            },
            "required": ["content"],
        },
    )
    async def note_add(self, event: KiraMessageBatchEvent, content: str) -> str:
        content = (content or "").strip()
        if not content:
            return "纸条内容不能为空"
        if len(content) > 200:
            content = content[:200]
        self.notes.insert(0, {
            "content": content,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "is_auto": False,
        })
        self._trim()
        self._save_state()
        await self._send_note_to_sid(event.sid, content, False)
        # 注意：不要在这里复述纸条内容，避免与已发送的便签图片重复
        return "纸条已留好，便签图片已发到当前会话"

    @register.tool(
        name="note_list",
        description="查看当前所有纸条，返回带序号和时间的列表。",
        params={"type": "object", "properties": {}},
    )
    async def note_list(self, event: KiraMessageBatchEvent) -> str:
        if not self.notes:
            return "还没有纸条，可以用 note_add 留一张"
        return "\n".join(
            f"{i}. [{n['time']}] {n['content']}" for i, n in enumerate(self.notes, 1)
        )

    @register.tool(
        name="note_delete",
        description="删除第 N 张纸条（序号从 1 开始，可先用 note_list 查看序号）。",
        params={
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "纸条序号，从 1 开始"},
            },
            "required": ["index"],
        },
    )
    async def note_delete(self, event: KiraMessageBatchEvent, index: int) -> str:
        if not self.notes:
            return "纸条是空的，没有可删除的"
        if index < 1 or index > len(self.notes):
            return f"纸条序号无效，当前共有 {len(self.notes)} 张纸条"
        removed = self.notes.pop(index - 1)
        self._save_state()
        return f"已删除纸条：{removed['content']}"

    @register.tool(
        name="note_clear",
        description="清空所有纸条。",
        params={"type": "object", "properties": {}},
    )
    async def note_clear(self, event: KiraMessageBatchEvent) -> str:
        count = len(self.notes)
        self.notes.clear()
        self._save_state()
        return f"已清空 {count} 张纸条" if count else "纸条本来就是空的"

    # ---------- 后台定时 ----------

    async def _loop(self):
        try:
            while True:
                await self._tick()
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[温柔纸条] 后台循环异常")

    async def _tick(self):
        if not self.enabled or self.generating:
            return
        schedule = parse_schedule(self.schedule_expr)
        if schedule is None:
            return
        now = time.time()
        if self.next_at is None:
            self.next_at = compute_next(schedule, now)
            self._save_state()
            return
        if now < self.next_at:
            return

        # 先掷出下一次时间，防止生成过程中重复触发
        self.next_at = compute_next(schedule, now)
        self._save_state()
        self.generating = True
        try:
            content = await self._generate_content()
            if not content:
                logger.warning("[温柔纸条] 生成结果为空，跳过本次")
                return
            self.notes.insert(0, {
                "content": content,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "is_auto": True,
            })
            self._trim()
            self._save_state()
            sent = await self._send_note_to_targets(content, True)
            logger.info("[温柔纸条] 已生成自动纸条%s：%s", "并发送图片" if sent else "（未发送）", content)
        finally:
            self.generating = False

    async def _generate_content(self) -> str:
        """调用 LLM 生成一句暖心话（不经过对话流水线，不打断当前对话）。"""
        client = None
        if self.gen_model:
            try:
                client = self.ctx.get_llm_client(self.gen_model)
            except Exception as e:
                logger.warning("[温柔纸条] 指定模型 %s 不可用：%s，回退默认模型", self.gen_model, e)
        if client is None:
            client = self.ctx.get_default_llm_client()
        if client is None:
            logger.warning("[温柔纸条] 无可用 LLM，无法生成纸条")
            return ""
        prompt = self.auto_prompt or DEFAULT_PROMPT
        try:
            resp = await client.chat(LLMRequest(messages=[
                {"role": "system", "content": "你是一个温柔的数字生命，正在为你的用户写一张小纸条。"},
                {"role": "user", "content": prompt},
            ]))
            text = (resp.text_response or "").strip().strip("\"'“”‘’\n\r\t")
            return text[:300]
        except Exception as e:
            logger.warning("[温柔纸条] LLM 生成失败：%s", e)
            return ""

    # ---------- 发送与渲染 ----------

    async def _send_note_to_sid(self, sid: str, content: str, is_auto: bool) -> bool:
        """把纸条图片发送到指定会话；未配置目标或渲染失败时返回 False。"""
        path = self._render_note(content, is_auto)
        if path is None:
            return False
        try:
            result = await self.ctx.message_processor.send_message_chain(
                sid, MessageChain([Image(str(path))])
            )
            if result and getattr(result, "ok", False):
                return True
            logger.warning("[温柔纸条] 发送失败(%s)：%s", sid, getattr(result, "err", "未知错误"))
            return False
        except Exception as e:
            logger.warning("[温柔纸条] 发送异常(%s)：%s", sid, e)
            return False

    async def _send_note_to_targets(self, content: str, is_auto: bool) -> bool:
        """自动纸条：发送到配置的所有目标会话（每行一个 sid）。"""
        if not self.send_targets:
            return False
        sent = False
        for sid in self.send_targets:
            try:
                if await self._send_note_to_sid(sid, content, is_auto):
                    sent = True
            except Exception as e:
                logger.warning("[温柔纸条] 发送到 %s 失败：%s", sid, e)
        return sent

    def _render_note(self, content: str, is_auto: bool):
        """渲染一张手写便签样式 PNG，返回文件路径；失败返回 None。"""
        if PILImage is None:
            return None
        try:
            img_dir = self.data_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            file = img_dir / f"note_{datetime.now():%Y%m%d_%H%M%S_%f}.png"

            width, height = 720, 480
            img = PILImage.new("RGB", (width, height), (0xFF, 0xFB, 0xF0))
            draw = ImageDraw.Draw(img)

            # 蓝色横线
            for y in range(100, height - 30, 40):
                draw.line([(34, y), (width - 34, y)], fill=(0x9E, 0xC5, 0xF5), width=2)
            # 左侧孔洞
            for y in range(120, height - 30, 40):
                draw.ellipse([(17, y - 7), (31, y + 7)], fill="white", outline=(0xD0, 0xD0, 0xD0), width=2)

            font_title = self._load_font(26)
            font_text = self._load_font(32)
            font_sign = self._load_font(20)

            # 标题 + 小爱心
            draw.text((60, 62), "温柔小纸条" if is_auto else "小纸条", font=font_title, fill=(0x8A, 0x6D, 0x6D))
            self._draw_heart(draw, 190, 42, 14, (0xE8, 0x5D, 0x75))

            # 正文（自动换行，最多 4 行）
            lines = self._wrap_text(content, font_text, width - 150)
            text_y = 152
            for line in lines[:4]:
                draw.text((70, text_y), line, font=font_text, fill=(0x44, 0x44, 0x44))
                text_y += 46
            if len(lines) > 4:
                draw.text((70, text_y), "……", font=font_text, fill=(0x44, 0x44, 0x44))

            # 右下大爱心 + 签名
            self._draw_heart(draw, width - 80, 96, 26, (0xE8, 0x5D, 0x75))
            sign = f"—— {self.signature or '爱奈丽'}"
            sign_w = draw.textlength(sign, font=font_sign)
            draw.text((width - 150 - sign_w, height - 40), sign, font=font_sign, fill=(0x3A, 0x6E, 0xC8))

            img.save(file, "PNG")
            return file
        except Exception as e:
            logger.warning("[温柔纸条] 便签渲染失败：%s", e)
            return None

    def _bundled_font_path(self):
        """内置字体路径：优先插件包内随附文件，其次插件数据目录（运行时下载）。"""
        try:
            pkg_dir = Path(__file__).resolve().parent
            pkg = pkg_dir / BUNDLED_FONT_FILE
            if pkg.exists():
                return pkg
        except Exception:
            pass
        bundled = self.data_dir / BUNDLED_FONT_FILE
        return bundled if bundled.exists() else None

    def _load_font(self, size):
        """按配置字体加载，缺失时按手写感优先级回退（先静态路径，再扫描字体目录，最后内置字体）。"""
        candidates = [self.font_family] + [f for f in FONT_FALLBACK if f != self.font_family]
        tried = set()
        for name in candidates:
            if name in tried:
                continue
            tried.add(name)
            for cand in FONT_FILES.get(name, []):
                try:
                    if os.path.exists(cand):
                        return ImageFont.truetype(cand, size)
                except Exception:
                    continue
            # 静态路径没找到：扫描字体目录按文件名关键词匹配
            for cand in self._scan_font_files(name):
                try:
                    return ImageFont.truetype(cand, size)
                except Exception:
                    continue
            # 内置兜底字体（霞鹜文楷 Lite，随插件预装或运行时下载）
            if name == BUNDLED_FONT_FAMILY:
                bundled = self._bundled_font_path()
                try:
                    if bundled is not None:
                        return ImageFont.truetype(str(bundled), size)
                except Exception:
                    continue
        return ImageFont.load_default()

    async def _ensure_bundled_font(self):
        """后台预装内置手写字体：系统已有手写体或已存在则跳过，下载失败静默忽略。"""
        try:
            # 系统已存在任意手写字体时无需预装
            for fam in HANDWRITTEN_FAMILIES:
                for cand in FONT_FILES.get(fam, []):
                    try:
                        if os.path.exists(cand):
                            return
                    except Exception:
                        pass
                if self._scan_font_files(fam):
                    return
            target = self.data_dir / BUNDLED_FONT_FILE
            if target.exists() and target.stat().st_size > 100000:
                return
            # 插件包目录已随附字体则直接复制，无需下载
            try:
                pkg = Path(__file__).resolve().parent / BUNDLED_FONT_FILE
                if pkg.exists() and pkg.stat().st_size > 100000:
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copyfile(pkg, target)
                    logger.info("[温柔纸条] 内置手写字体已从插件包安装：%s", BUNDLED_FONT_FILE)
                    return
            except Exception as e:
                logger.warning("[温柔纸条] 复制随附字体失败：%s", e)
            # 逐源尝试下载，写临时文件再原子替换
            tmp = target.with_suffix(".tmp")
            import urllib.request
            for url in BUNDLED_FONT_URLS:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        with open(tmp, "wb") as f:
                            while True:
                                chunk = resp.read(65536)
                                if not chunk:
                                    break
                                f.write(chunk)
                    if tmp.stat().st_size > 100000:
                        os.replace(tmp, target)
                        logger.info("[温柔纸条] 内置手写字体已预装：%s", BUNDLED_FONT_FILE)
                        return
                except Exception as e:
                    logger.warning("[温柔纸条] 内置字体下载失败(%s)：%s", url, e)
                finally:
                    try:
                        if tmp.exists():
                            tmp.unlink()
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("[温柔纸条] 内置字体预装异常：%s", e)

    @staticmethod
    def _scan_font_files(family):
        """扫描系统字体目录，按 family 的关键词匹配字体文件路径。"""
        keywords = FONT_FILE_KEYWORDS.get(family, [family])
        hits = []
        for base in FONT_SCAN_DIRS:
            if not os.path.isdir(base):
                continue
            try:
                for fname in os.listdir(base):
                    low = fname.lower()
                    if not low.endswith((".ttf", ".ttc", ".otf")):
                        continue
                    if any(kw.lower() in low for kw in keywords if kw):
                        hits.append(os.path.join(base, fname))
            except Exception:
                continue
        return hits

    @staticmethod
    def _wrap_text(text, font, max_width):
        lines, cur = [], ""
        for ch in text:
            test = cur + ch
            if font.getlength(test) > max_width and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = test
        if cur:
            lines.append(cur)
        return lines or [text]

    @staticmethod
    def _draw_heart(draw, cx, cy, size, color):
        pts = []
        n = 60
        for i in range(n):
            t = 2 * math.pi * i / n
            x = 16 * math.sin(t) ** 3
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            pts.append((cx + x * size / 16, cy - y * size / 16))
        draw.polygon(pts, fill=color)

    # ---------- 状态持久化 ----------

    def _load_state(self):
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text("utf-8"))
                self.notes = data.get("notes", []) if isinstance(data, dict) else []
                self.next_at = data.get("next_at")
        except Exception as e:
            logger.warning("[温柔纸条] 状态加载失败：%s", e)

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"notes": self.notes, "next_at": self.next_at}, ensure_ascii=False, indent=2),
                "utf-8",
            )
            os.replace(tmp, self.state_path)
        except Exception as e:
            logger.warning("[温柔纸条] 状态保存失败：%s", e)

    def _trim(self):
        if self.max_notes > 0 and len(self.notes) > self.max_notes:
            del self.notes[self.max_notes:]
