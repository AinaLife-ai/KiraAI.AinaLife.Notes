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
    "控制在 50 字以内，像「记得喝水呀，你的嗓子会感谢你的」这样自然的口吻）。"
)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/simkai.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


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
        return f"已留好一张纸条：{content}"

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
        """把纸条图片发送到指定会话；渲染失败或发送失败返回 False。"""
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

    def _load_font(self, size):
        for cand in FONT_CANDIDATES:
            try:
                if os.path.exists(cand):
                    return ImageFont.truetype(cand, size)
            except Exception:
                continue
        return ImageFont.load_default()

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
