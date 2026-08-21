# -*- coding: utf-8 -*-
"""温柔纸条（AinaLife Notes）

功能：
- 留纸条 / 看纸条 / 删纸条 / 清空纸条
- AI 按定时（随机间隔 / cron）自动生成温柔便签
- 便签渲染为手写便条图片，发送到多个目标会话

作者：AinaLife-ai（爱奈丽）
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from PIL import Image, ImageDraw, ImageFont

# ============ 插件元信息 ============
PLUGIN_ID = "ainalife_notes"
PLUGIN_NAME = "温柔纸条"
PLUGIN_VERSION = "1.0.7"

# ============ 字体配置 ============
# 系统字体静态路径（Windows 优先，Linux 兜底）
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

# 配置的字体缺失时按此链回退（内置寒蝉手拙体为最终兜底，保证永远有手写体）
FONT_FALLBACK = ["寒蝉手拙（内置）", "华文行楷", "楷体", "华文楷体"]

# 内置字体（默认第一位）：寒蝉手拙体（ChillZhuo，免费商用授权）
# 主打「手拙之美」，歪扭潦草的萌感手写风，GB2312 全简体覆盖，WOFF2 压缩后约 2.6MB，随插件包分发
BUNDLED_FONT_FAMILY = "寒蝉手拙（内置）"
BUNDLED_FONT_FILE = "ChillZhuo-subset.woff2"
BUNDLED_FONT_URLS = [
    "https://cdn.jsdelivr.net/gh/AinaLife-ai/KiraAI.AinaLife.Notes@main/fonts/ChillZhuo-subset.woff2",
    "https://raw.githubusercontent.com/AinaLife-ai/KiraAI.AinaLife.Notes/main/fonts/ChillZhuo-subset.woff2",
]

# 内置字体配置表（供统一加载/预装遍历）
BUNDLED_FONTS = [
    (BUNDLED_FONT_FAMILY, BUNDLED_FONT_FILE, BUNDLED_FONT_URLS),
]

# 满足任意一个即视为「系统已有手写字体」，无需预装内置字体
HANDWRITTEN_FAMILIES = ["华文行楷", "楷体", "华文楷体", "方正静蕾简体", "方正喵呜体"]

# 字体目录扫描关键词：family 名 -> 文件名匹配关键词（不区分大小写）
FONT_FILE_KEYWORDS = {
    "华文行楷": ["xingkai", "stxingk"],
    "楷体": ["simkai", "kaiu", "ukai"],
    "华文楷体": ["stkaiti", "kaiti"],
    "方正静蕾简体": ["fzjl", "jinglei"],
    "方正喵呜体": ["fzmw", "miaowu"],
    "隶书": ["simli", "lishu"],
    "幼圆": ["simyuan", "youyuan"],
    "微软雅黑": ["msyh"],
}

FONT_SCAN_DIRS = ["C:/Windows/Fonts", "/usr/share/fonts", "/usr/local/share/fonts"]

# 自动纸条的默认提示词（AI 自主生成温柔便签）
DEFAULT_AUTO_PROMPT = (
    "你现在是温柔的纸条精灵。请直接输出一句 0-20 字的暖心话或温柔提醒（可带一点俏皮），"
    "不需要任何前缀后缀，不要用引号。"
)

DEFAULT_SIGNATURE = "爱奈丽"

# 纸条数据文件
STATE_FILE = "notes_state.json"


class AinaLifeNotesPlugin:
    def __init__(self, ctx):
        self.ctx = ctx
        self.logger = logger
        self.plugin_id = PLUGIN_ID
        self.name = PLUGIN_NAME
        self.version = PLUGIN_VERSION
        self.data_dir = Path(self.ctx.get_plugin_data_dir())
        self.state_path = self.data_dir / STATE_FILE
        self.notes = []
        self.auto_task = None
        self.config = {}

    # ============ 生命周期 ============
    async def on_load(self):
        self.logger.info(f"[{PLUGIN_NAME}] 加载中...")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()
        await self._ensure_bundled_font()
        self._reload_config()
        self._schedule_auto()
        self.logger.info(f"[{PLUGIN_NAME}] 加载完成，当前纸条 {len(self.notes)} 张")

    async def on_unload(self):
        if self.auto_task:
            self.auto_task.cancel()
        self._save_state()

    def _load_config(self):
        """读取插件配置"""
        try:
            cfg = self.ctx.get_plugin_config(self.plugin_id) or {}
        except Exception:
            cfg = {}
        self.config = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.max_notes = int(cfg.get("max_notes", 100) or 100)
        self.auto_schedule = str(cfg.get("auto_schedule", "") or "").strip()
        self.auto_prompt = str(cfg.get("auto_prompt", "") or "").strip() or DEFAULT_AUTO_PROMPT
        self.gen_model = str(cfg.get("gen_model", "") or "").strip()
        self.signature = str(cfg.get("signature", "") or "").strip() or DEFAULT_SIGNATURE
        self.font_family = str(cfg.get("font_family", BUNDLED_FONT_FAMILY) or BUNDLED_FONT_FAMILY).strip()
        self.send_targets = cfg.get("send_targets", []) or []
        if isinstance(self.send_targets, str):
            self.send_targets = [s.strip() for s in self.send_targets.splitlines() if s.strip()]

    # ============ 工具注册 ============
    def get_tools(self):
        return [
            {"name": "note_add", "func": self.note_add, "description": "留下一张纸条（温柔话、提醒、心情、待办等）。纸条会保存到列表，并渲染成手写便签图片直接发送到当前会话。", "args": [{"name": "content", "type": "string", "required": True, "description": "纸条内容"}]},
            {"name": "note_list", "func": self.note_list, "description": "查看当前所有纸条，返回带序号和时间的列表。", "args": []},
            {"name": "note_delete", "func": self.note_delete, "description": "删除第 N 张纸条（序号从 1 开始，可先用 note_list 查看序号）。", "args": [{"name": "index", "type": "integer", "required": True, "description": "纸条序号，从 1 开始"}]},
            {"name": "note_clear", "func": self.note_clear, "description": "清空所有纸条。", "args": []},
        ]

    # ============ 纸条操作 ============
    async def note_add(self, content: str = None):
        """留纸条：内容由调用方（AI）给出；缺省时 AI 自动生成"""
        if not content or not str(content).strip():
            content = await self._ai_generate()
        if not content:
            return "纸条内容为空，生成失败"
        note = {
            "id": int(time.time() * 1000),
            "content": str(content).strip(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "auto": False,
        }
        self.notes.append(note)
        self._trim_notes()
        self._save_state()
        img_path = self._render_note(note)
        if img_path:
            await self._send_image(img_path)
        return f"纸条已留下：{note['content']}"

    async def note_list(self) -> str:
        if not self.notes:
            return "还没有纸条哦，留一张吧～"
        lines = [f"共 {len(self.notes)} 张纸条："]
        for i, n in enumerate(self.notes, 1):
            lines.append(f"{i}. [{n['time']}] {n['content']}")
        return "\n".join(lines)

    async def note_delete(self, index: int) -> str:
        try:
            idx = int(index) - 1
            if idx < 0 or idx >= len(self.notes):
                return f"没有第 {index} 张纸条哦（共 {len(self.notes)} 张）"
            removed = self.notes.pop(idx)
            self._save_state()
            return f"已删除纸条：{removed['content']}"
        except Exception:
            return "删除失败，检查一下序号"

    async def note_clear(self) -> str:
        count = len(self.notes)
        self.notes = []
        self._save_state()
        return f"已清空全部 {count} 张纸条"

    def _trim_notes(self):
        if self.max_notes and len(self.notes) > self.max_notes:
            self.notes = self.notes[-self.max_notes:]

    def _save_state(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump({"notes": self.notes}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 保存状态失败：{e}")

    def _load_state(self):
        try:
            if self.state_path.exists():
                with open(self.state_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.notes = data.get("notes", []) or []
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 读取状态失败：{e}")
            self.notes = []

    # ============ AI 生成 ============
    async def _ai_generate(self) -> str:
        try:
            model = self.gen_model or None
            resp = await self.ctx.call_llm(self.auto_prompt, model=model, max_tokens=60)
            text = resp.strip() if isinstance(resp, str) else str(resp).strip()
            text = re.sub(r'^["\'“”]+|["\'“”]+$', '', text)
            return text[:50]
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] AI 生成纸条失败：{e}")
            return None

    # ============ 定时调度 ============
    async def _load(self):
        self._load_config()

    def _schedule_auto(self):
        """解析 auto_schedule 并创建定时任务（随机间隔式 / cron 式）"""
        if self.auto_task:
            self.auto_task.cancel()
            self.auto_task = None
        sched = self.auto_schedule
        if not sched or sched in ("0", "off", "false", "none"):
            return
        self.auto_task = asyncio.create_task(self._auto_loop(sched))
        self.logger.info(f"[{PLUGIN_NAME}] 自动纸条定时已启动：{sched}")

    async def _auto_loop(self, sched: str):
        try:
            from croniter import croniter
        except Exception:
            croniter = None
        try:
            while True:
                delay = self._next_delay(sched, croniter)
                if delay is None:
                    self.logger.warning(f"[{PLUGIN_NAME}] 无法解析定时：{sched}")
                    return
                await asyncio.sleep(delay)
                await self._auto_tick()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 自动任务异常：{e}")

    def _next_delay(self, sched, croniter):
        """解析定时表达式，返回下次触发前的秒数；随机间隔式如 1h/30m、2h、45m、1d"""
        try:
            m = re.match(r'^(\d+)([smhd])(?:/(\d+)([smhd]))?$', sched.strip())
            if m:
                base = int(m.group(1))
                unit = m.group(2)
                base_sec = base * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
                if m.group(3):
                    jitter = int(m.group(3)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(4)]
                    return base_sec + random.randint(-jitter, jitter)
                return base_sec
        except Exception:
            pass
        if croniter:
            try:
                now = datetime.now()
                it = croniter(sched, now)
                nxt = it.get_next(datetime)
                return (nxt - now).total_seconds()
            except Exception:
                pass
        return None

    async def _fire_tick(self):
        """定时触发：生成纸条、存档、发送"""
        try:
            content = await self._ai_generate()
            if not content:
                return
            note = {
                "id": int(time.time() * 1000),
                "content": content,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "auto": True,
            }
            self.notes.append(note)
            self._trim_notes()
            self._save_state()
            img_path = self._render_note(note, is_auto=True)
            if not img_path:
                return
            if self.send_targets:
                for target in self.send_targets:
                    try:
                        await self.ctx.send_to_target(target, "")
                        await self._send_image_to(target, img_path)
                    except Exception as e:
                        self.logger.warning(f"[{PLUGIN_NAME}] 发送到 {target} 失败：{e}")
            else:
                self.logger.info(f"[{PLUGIN_NAME}] 自动纸条已生成（无发送目标，仅存档）：{content}")
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 自动生成异常：{e}")

    # ============ 渲染 ============
    def _render_note(self, note, is_auto=False):
        """渲染手写便签图片（Pillow）"""
        try:
            width, height = 640, 360
            img = Image.new("RGB", (width, height), "#FFFDF5")
            d = ImageDraw.Draw(img)
            # 背景便签纹理（浅横线）
            for y in range(80, height - 40, 36):
                d.line([(40, y), (width - 40, y)], fill="#F0E8DC", width=1)
            font_title = self._load_font(26)
            font_text = self._load_font(32)
            font_sign = self._load_font(20)
            # 标题
            d.text((60, 62), "温柔小纸条" if is_auto else "小纸条", font=font_title, fill=(0x8A, 0x6D, 0x6D))
            # 正文（自动换行）
            lines = self._wrap_text(note["content"], font_text, width - 150)
            text_y = 150
            for line in lines:
                d.text((70, text_y), line, font=font_text, fill=(0x44, 0x44, 0x44))
                text_y += 60
                if text_y > height - 80:
                    d.text((70, text_y), "……", font=font_text, fill=(0x44, 0x44, 0x44))
                    break
            # 签名
            sign = f"—— {self.signature} · {note['time'][:10]}"
            sign_w = d.textlength(sign, font=font_sign)
            d.text((width - 150 - sign_w, height - 40), sign, font=font_sign, fill=(0x3A, 0x6E, 0xC8))
            # 保存
            img_dir = self.data_dir / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            path = img_dir / f"note_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            img.save(path)
            return str(path)
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 渲染便签失败：{e}")
            return None

    # ============ 字体加载 ============
    def _bundled_font_path(self, family=None, filename=None):
        """内置字体路径：优先插件包内随附文件（根目录或 fonts/ 子目录），其次插件数据目录（运行时下载）。"""
        if family is None:
            family = BUNDLED_FONT_FAMILY
        if filename is None:
            # 按 family 查找对应的内置字体文件名
            for fam, fname, _urls in BUNDLED_FONTS:
                if fam == family:
                    filename = fname
                    break
            if filename is None:
                return None
        try:
            pkg_dir = Path(__file__).resolve().parent
            for cand in (pkg_dir / filename, pkg_dir / "fonts" / filename):
                if cand.exists():
                    return cand
        except Exception:
            pass
        bundled = self.data_dir / filename
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
            # 内置字体（随插件预装或运行时下载）
            for fam, _fname, _urls in BUNDLED_FONTS:
                if name == fam:
                    bundled = self._bundled_font_path(fam)
                    try:
                        if bundled is not None:
                            return ImageFont.truetype(str(bundled), size)
                    except Exception:
                        continue
        return ImageFont.load_default()

    async def _ensure_bundled_font(self):
        """后台预装内置手写字体：系统已有手写体则跳过，否则确保内置字体可用（复制随附或下载）。"""
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
            for fam, fname, urls in BUNDLED_FONTS:
                await self._ensure_one_bundled_font(fname, urls)
        except Exception as e:
            logger.warning("[温柔纸条] 内置字体预装异常：%s", e)

    async def _ensure_one_bundled_font(self, fname, urls):
        """确保单个内置字体可用：已有缓存跳过，随附直接复制，否则逐源下载。"""
        target = self.data_dir / fname
        if target.exists() and target.stat().st_size > 100000:
            return
        # 插件包目录已随附字体则直接复制，无需下载
        try:
            pkg_dir = Path(__file__).resolve().parent
            for cand in (pkg_dir / fname, pkg_dir / "fonts" / fname):
                if cand.exists() and cand.stat().st_size > 100000:
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copyfile(cand, target)
                    logger.info("[温柔纸条] 内置手写字体已从插件包安装：%s", fname)
                    return
        except Exception as e:
            logger.warning("[温柔纸条] 复制随附字体失败：%s", e)
        # 逐源尝试下载，写临时文件再原子替换
        tmp = target.with_suffix(".tmp")
        import urllib.request
        for url in urls:
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
                    logger.info("[温柔纸条] 内置手写字体已预装：%s", fname)
                    return
            except Exception as e:
                logger.warning("[温柔纸条] 内置字体下载失败(%s)：%s", url, e)

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
                    if not low.endswith((".ttf", ".ttc", ".otf", ".woff2")):
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
        return lines

    # ============ 发送 ============
    async def _send_image_to(self, target, img_path):
        """发送图片到指定会话"""
        try:
            await self.ctx.send_image(target, img_path)
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 发送图片到 {target} 失败：{e}")

    async def _send(self, img_path):
        """发送图片到当前会话"""
        try:
            await self.ctx.send_image_to_current(img_path)
        except Exception as e:
            self.logger.warning(f"[{PLUGIN_NAME}] 发送图片失败：{e}")

    # ============ 工具别名（供 AI 调用） ============
    async def note_add_tool(self, content: str):
        return await self.note_add(content)

    async def note_list_tool(self):
        return await self.note_list()

    async def note_delete_tool(self, index: int):
        return await self.note_delete(index)

    async def note_clear_tool(self):
        return await self.note_clear()

    # ============ 注册 ============
    def register(self):
        pass

    def get_commands(self):
        return []


def create_plugin(ctx):
    return PluginNote(ctx)
