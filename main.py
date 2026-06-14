import asyncio
import calendar
import json
import random
import struct
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from astrbot.api.event import filter, AstrMessageEvent

try:
    from astrbot.api.event import MessageChain
except ImportError:
    from astrbot.core.message.message_event_result import MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.star import StarTools
from astrbot.api import AstrBotConfig  # 配置管理


class AsyncRcon:  # 异步RCON类
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.reader = None
        self.writer = None

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        await self._send_packet(0, 3, self.password)  # 登录
        req_id, _, _ = await self._recv_packet()
        if req_id == -1:
            raise PermissionError("RCON 登录失败，请检查密码配置。")

    async def send_cmd(self, command: str, *, extra_recv_deadline_sec: float = 1.45) -> str:
        await self._send_packet(1, 2, command)
        req_id, _, body = await self._recv_packet()
        chunks = [body] if body else []

        # 部分服务端会把输出拆成多包；Spark health 等可能晚于首包才返回正文。
        deadline = time.monotonic() + max(0.05, extra_recv_deadline_sec)
        idle = 0.12
        while time.monotonic() < deadline:
            try:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                next_req_id, _, next_body = await asyncio.wait_for(
                    self._recv_packet(), timeout=min(idle, left)
                )
            except asyncio.TimeoutError:
                if "".join(chunks).strip():
                    break
                continue

            if next_req_id != req_id:
                continue
            if next_body:
                chunks.append(next_body)

        return "".join(chunks)

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def _send_packet(self, req_id: int, ptype: int, payload: str):
        data = struct.pack("<ii", req_id, ptype) + payload.encode() + b"\x00\x00"
        length = struct.pack("<i", len(data))
        self.writer.write(length + data)
        await self.writer.drain()

    async def _recv_packet(self):
        length_bytes = await self.reader.readexactly(4)
        length = struct.unpack("<i", length_bytes)[0]
        data = await self.reader.readexactly(length)
        req_id, ptype = struct.unpack("<ii", data[:8])
        body = data[8:].rstrip(b"\x00").decode(errors="ignore")
        return req_id, ptype, body


def strip_mc_color(text: str) -> str:
    return re.sub(r"§.", "", text)


_SIGN_CAL_FONT_CACHE: dict[int, "ImageFont.FreeTypeFont | ImageFont.ImageFont"] = {}


def _search_system_cjk_font() -> str | None:
    """在系统字体目录中搜索支持中文的 TTF/TTC 字体，返回路径或 None。"""
    search_dirs = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        "/usr/local/share/fonts/truetype",
        "C:/Windows/Fonts",
        "/System/Library/Fonts",
        "/Library/Fonts",
    ]
    preferred = [
        "NotoSansCJK", "NotoSansSC", "WenQuanYi", "wqy",
        "SourceHanSans", "DroidSansFallback", "SimHei", "msyh",
        "PingFang", "Hiragino",
    ]
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for name in files:
                low = name.lower()
                if not (low.endswith(".ttf") or low.endswith(".ttc") or low.endswith(".otf")):
                    continue
                if not any(kw.lower() in low for kw in preferred):
                    continue
                fp = os.path.join(root, name)
                try:
                    ImageFont.truetype(fp, 12)
                    return fp
                except Exception:
                    continue
    return None


def _download_cjk_font(cache_dir: str) -> str | None:
    """下载 CJK 字体到缓存目录，返回路径或 None。"""
    font_path = os.path.join(cache_dir, "NotoSansSC-Regular.ttf")
    if os.path.isfile(font_path):
        return font_path
    import urllib.request
    sources = [
        "https://cdn.jsdelivr.net/gh/AstraThreshold/fonts-cjk/NotoSansSC-Regular.otf",
        "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Regular.otf",
    ]
    for url in sources:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            urllib.request.urlretrieve(url, font_path)
            if os.path.getsize(font_path) > 100_000:
                return font_path
            os.remove(font_path)
        except Exception:
            if os.path.isfile(font_path):
                try:
                    os.remove(font_path)
                except OSError:
                    pass
    return None


def _get_sign_cal_font(size: int, font_cache_dir: str = ""):
    """获取支持中文的字体：系统搜索 → 自动下载 → 默认字体（ASCII only）。"""
    if not _HAS_PIL:
        return None
    if size in _SIGN_CAL_FONT_CACHE:
        return _SIGN_CAL_FONT_CACHE[size]
    # 1) 系统字体
    sys_font = _search_system_cjk_font()
    if sys_font:
        try:
            f = ImageFont.truetype(sys_font, size)
            _SIGN_CAL_FONT_CACHE[size] = f
            return f
        except Exception:
            pass
    # 2) 自动下载
    if font_cache_dir:
        dl_path = _download_cjk_font(font_cache_dir)
        if dl_path:
            try:
                f = ImageFont.truetype(dl_path, size)
                _SIGN_CAL_FONT_CACHE[size] = f
                return f
            except Exception:
                pass
    # 3) 兜底
    logger.warning("[mcsigncal] 未找到 CJK 字体，日历中文将显示为方框。请安装字体或在 Docker 中运行: apt install fonts-noto-cjk")
    f = ImageFont.load_default()
    _SIGN_CAL_FONT_CACHE[size] = f
    return f


async def mc_server_list_ping(host: str, port: int, timeout: float = 3.0) -> dict:
    """通过 Server List Ping 协议获取 MC 服务端状态（含完整玩家列表）。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        def _write_varint(val: int) -> bytes:
            val = val & 0xFFFFFFFF  # 转为无符号 32 位
            out = b""
            while True:
                b = val & 0x7F
                val >>= 7
                if val:
                    out += bytes([b | 0x80])
                else:
                    out += bytes([b])
                    break
            return out

        def _write_string(s: str) -> bytes:
            raw = s.encode("utf-8")
            return _write_varint(len(raw)) + raw

        # Handshake: packet id 0x00, protocol -1, next state 1 (status)
        handshake = _write_varint(0x00) + _write_varint(-1) + _write_string(host) + struct.pack(">H", port) + _write_varint(1)
        writer.write(_write_varint(len(handshake)) + handshake)

        # Status request: packet id 0x00
        writer.write(b"\x01\x00")
        await writer.drain()

        async def _read_varint() -> int:
            result = 0
            shift = 0
            while True:
                b = await asyncio.wait_for(reader.readexactly(1), timeout=timeout)
                byte = b[0]
                result |= (byte & 0x7F) << shift
                if not (byte & 0x80):
                    break
                shift += 7
                if shift > 35:
                    raise ValueError("Varint too big")
            return result

        # Read response
        await _read_varint()  # pkt_len
        await _read_varint()  # pkt_id
        json_len = await _read_varint()
        json_data = await asyncio.wait_for(reader.readexactly(json_len), timeout=timeout)
        return json.loads(json_data.decode("utf-8"))
    finally:
        writer.close()
        await writer.wait_closed()


# 服务端 latest.log 里常见玩家聊天形态：<Steve> hi 或 [Not Secure] <Steve> hi
_MC_LOG_CHAT_RE = re.compile(
    r"(?:\[Not Secure\]\s*)?<(?P<player>[^>]+)>\s*(?P<body>.*?)\s*$",
    re.IGNORECASE,
)


# /mcrun 默认禁止的首个命令词（不含命名空间前缀，比较时会去掉 xxx:）
_MCRUN_DEFAULT_BLOCKED_FIRST = frozenset({"stop", "restart", "op", "deop", "reload"})


async def rcon_command(
    host: str,
    port: int,
    password: str,
    command: str,
    *,
    extra_recv_deadline_sec: float = 0.45,
) -> str:  # 执行rcon命令
    """统一执行任意 RCON 命令"""
    rcon = AsyncRcon(host, port, password)
    await rcon.connect()
    try:
        return await rcon.send_cmd(
            command, extra_recv_deadline_sec=extra_recv_deadline_sec
        )
    finally:
        await rcon.close()


@register(
    "PLG_CAT3", "Dragonrster", "MC服务器管理插件：白名单、封禁、聊天转发、签到经济、RCON命令", "1.1.0"
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.whitelist_command = self.config.get("whitelist_command", "whitelist")
        self.admin_qqs = set(self.config.get("admin_qqs", []))
        self.rcon_host = self.config.get("rcon_host")
        self.rcon_port = self.config.get("rcon_port")
        self.rcon_password = self.config.get("rcon_password")
        self.mc_server_port = int(self.config.get("mc_server_port", 25565))
        # 申请白名单功能
        self.enable_apply_whitelist = self.config.get("enable_apply_whitelist", False)
        self.whitelist_verify_mode = str(self.config.get("whitelist_verify_mode", "code")).strip().lower()
        # 待验证队列 {qqid: {"mcname": str, "code": str, "expire": float}}
        self._wl_pending: dict[str, dict] = {}
        self.plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_PLGCAT3")
        self.apply_file = os.path.join(self.plugin_data_dir, "apply_whitelist.json")
        self.apply_data = self._load_apply_data()
        # 签到功能
        self.enable_sign = self.config.get("enable_sign", False)
        self.sign_money_min = int(self.config.get("sign_money_min", 10))
        self.sign_money_max = int(self.config.get("sign_money_max", 100))
        self.sign_money_command = str(self.config.get("sign_money_command", "d money add {name} {amount}"))
        self.money_command_prefix = str(self.config.get("money_command_prefix", "d money"))
        self.sign_backfill_cost_per_day = int(self.config.get("sign_backfill_cost_per_day", 50))
        self.sign_cal_font_cache_dir = os.path.join(self.plugin_data_dir, "fonts")
        self.sign_file = os.path.join(self.plugin_data_dir, "sign_data.json")
        self.sign_data = self._load_sign_data()
        self.transfer_log_file = os.path.join(self.plugin_data_dir, "transfer_log.jsonl")
        self.mcrun_blocked_first = set(_MCRUN_DEFAULT_BLOCKED_FIRST)
        extra = self.config.get("mcrun_blocked_extra", [])
        if isinstance(extra, list):
            self.mcrun_blocked_first.update(
                str(x).strip().lower() for x in extra if str(x).strip()
            )
        # 游戏内「.mcsay xxx」→ QQ：依赖可读 latest.log + 主动发消息（RCON 收不到聊天）
        self.mc_chat_log_to_qq_enabled = bool(
            self.config.get("mc_chat_log_to_qq_enabled", False)
        )
        self.mc_chat_source = str(self.config.get("mc_chat_source", "file") or "file").strip().lower()
        self.mc_chat_log_path = str(self.config.get("mc_chat_log_path", "") or "").strip()
        # MCSM 配置
        self.mcsm_panel_url = str(self.config.get("mcsm_panel_url", "") or "").strip().rstrip("/")
        self.mcsm_api_key = str(self.config.get("mcsm_api_key", "") or "").strip()
        self.mcsm_instance_uuid = str(self.config.get("mcsm_instance_uuid", "") or "").strip()
        self.mcsm_daemon_uuid = str(self.config.get("mcsm_daemon_uuid", "") or "").strip()
        logger.info(f"[mcman] mc_chat_source={self.mc_chat_source}  mcsm_panel_url={self.mcsm_panel_url}")
        self.mc_chat_trigger_prefix = str(
            self.config.get("mc_chat_trigger_prefix", ".mcsay ") or ".mcsay "
        )
        self.mc_chat_qq_platform = str(
            self.config.get("mc_chat_qq_platform", "aiocqhttp") or "aiocqhttp"
        ).strip()
        self.mc_chat_qq_group_id = str(
            self.config.get("mc_chat_qq_group_id", "") or ""
        ).strip()
        self.mc_chat_unified_msg_origin = str(
            self.config.get("mc_chat_unified_msg_origin", "") or ""
        ).strip()
        self.mc_chat_log_tail_from_end = bool(
            self.config.get("mc_chat_log_tail_from_end", True)
        )
        self.mc_chat_target_file = os.path.join(
            str(self.plugin_data_dir), "mc_chat_qq_target.json"
        )
        self._load_mc_chat_target_from_file()
        self._mc_chat_task = None
        self._mc_chat_stop = None
        self._mc_chat_file_pos = [0]
        self._mc_chat_first_boot = [True]
        self._mc_chat_rate: defaultdict[str, deque[float]] = defaultdict(deque)

    def _load_mc_chat_target_from_file(self):
        if not os.path.isfile(self.mc_chat_target_file):
            return
        try:
            with open(self.mc_chat_target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            u = data.get("unified_msg_origin")
            if isinstance(u, str) and u.strip():
                self.mc_chat_unified_msg_origin = u.strip()
        except Exception as e:
            logger.warning(f"读取 mc_chat_qq_target.json 失败: {e}")

    def _save_mc_chat_target_to_file(self, umo: str):
        os.makedirs(str(self.plugin_data_dir), exist_ok=True)
        with open(self.mc_chat_target_file, "w", encoding="utf-8") as f:
            json.dump({"unified_msg_origin": umo}, f, ensure_ascii=False, indent=2)

    def _load_apply_data(self):
        if os.path.exists(self.apply_file):
            with open(self.apply_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 兼容旧格式 {qqid: mcname} → {qqid: [mcname]}
            migrated = {}
            for k, v in data.items():
                if isinstance(v, str):
                    migrated[k] = [v]
                elif isinstance(v, list):
                    migrated[k] = v
            return migrated
        return {}

    def _save_apply_data(self):
        with open(self.apply_file, "w", encoding="utf-8") as f:
            json.dump(self.apply_data, f, ensure_ascii=False, indent=2)

    def _load_sign_data(self):
        if os.path.exists(self.sign_file):
            with open(self.sign_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data:
                return {}
            sample = list(data.values())[0]
            # 旧格式1: {qqid: "date"}
            if isinstance(sample, str):
                migrated = {}
                for qqid, date_str in data.items():
                    if date_str not in migrated:
                        migrated[date_str] = []
                    migrated[date_str].append(qqid)
                return migrated
            # 旧格式2: {date: {"signers": [...], "pool": int}}
            if isinstance(sample, dict):
                return {d: v.get("signers", []) for d, v in data.items()}
            # 新格式: {date: [qqid, ...]}
            return data
        return {}

    def _save_sign_data(self):
        with open(self.sign_file, "w", encoding="utf-8") as f:
            json.dump(self.sign_data, f, ensure_ascii=False, indent=2)

    def _sign_consecutive_days(self, qqid: str) -> int:
        """计算用户连续签到天数（从今天往回数）"""
        today = datetime.now().date()
        streak = 0
        d = today
        while True:
            ds = d.strftime("%Y-%m-%d")
            signers = self.sign_data.get(ds, [])
            if qqid in signers:
                streak += 1
                d -= timedelta(days=1)
            else:
                break
        return streak

    def _generate_sign_calendar(self, qqid: str, user_name: str, year: int = None, month: int = None) -> bytes | None:
        """生成签到日历图片，返回 PNG bytes。需要 Pillow。"""
        if not _HAS_PIL:
            return None
        now = datetime.now()
        year = year or now.year
        month = month or now.month
        today_day = now.day if (year == now.year and month == now.month) else -1

        # 统计本月签到天数
        cal = calendar.monthcalendar(year, month)
        total_days_in_month = calendar.monthrange(year, month)[1]
        signed_days = set()
        for d in range(1, total_days_in_month + 1):
            ds = f"{year}-{month:02d}-{d:02d}"
            if qqid in self.sign_data.get(ds, []):
                signed_days.add(d)

        # 字体
        font = _get_sign_cal_font(14, self.sign_cal_font_cache_dir)
        font_title = _get_sign_cal_font(24, self.sign_cal_font_cache_dir)
        font_day = _get_sign_cal_font(20, self.sign_cal_font_cache_dir)

        cell_w, cell_h = 64, 56
        cols = 7
        rows = len(cal)
        header_h = 60
        legend_h = 30
        pad_x, pad_y = 20, 10
        img_w = pad_x * 2 + cell_w * cols
        img_h = pad_y * 2 + header_h + 30 + cell_h * rows + legend_h

        img = Image.new("RGB", (img_w, img_h), "#FFFFFF")
        draw = ImageDraw.Draw(img)

        # 标题
        title = f"{year} 年 {month} 月  签到日历"
        draw.text((pad_x, pad_y), title, fill="#333333", font=font_title)

        # 副标题
        sub = f"{user_name}  |  本月签到 {len(signed_days)}/{total_days_in_month} 天  |  连续 {self._sign_consecutive_days(qqid)} 天"
        draw.text((pad_x, pad_y + 30), sub, fill="#666666", font=font)

        # 星期头
        week_names = ["一", "二", "三", "四", "五", "六", "日"]
        week_colors = ["#333333"] * 5 + ["#4A90D9"] * 2
        y_start = pad_y + header_h
        for col, (name, color) in enumerate(zip(week_names, week_colors)):
            x = pad_x + col * cell_w + cell_w // 2 - 6
            draw.text((x, y_start), name, fill=color, font=font_day)

        # 日历格子
        grid_top = y_start + 30
        for row_idx, week in enumerate(cal):
            for col_idx, day in enumerate(week):
                x = pad_x + col_idx * cell_w
                y = grid_top + row_idx * cell_h
                if day == 0:
                    continue
                # 背景色
                if day in signed_days:
                    bg = "#E8F5E9"
                    border = "#4CAF50"
                    text_fill = "#2E7D32"
                elif day == today_day:
                    bg = "#FFF8E1"
                    border = "#FF9800"
                    text_fill = "#E65100"
                else:
                    bg = "#F8F9FA"
                    border = "#E0E0E0"
                    text_fill = "#999999"
                draw.rectangle([x + 1, y + 1, x + cell_w - 2, y + cell_h - 2], fill=bg, outline=border, width=2)
                # 日期数字
                day_str = str(day)
                bbox = draw.textbbox((0, 0), day_str, font=font_day)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                draw.text((x + cell_w // 2 - tw // 2, y + 8), day_str, fill=text_fill, font=font_day)
                # 签到勾号
                if day in signed_days:
                    check = "✓"
                    draw.text((x + cell_w // 2 - 5, y + cell_h - 18), check, fill="#4CAF50", font=font)

        # 图例
        ly = grid_top + rows * cell_h + 6
        draw.rectangle([pad_x, ly, pad_x + 14, ly + 14], fill="#E8F5E9", outline="#4CAF50")
        draw.text((pad_x + 18, ly - 1), "已签到", fill="#333333", font=font)
        draw.rectangle([pad_x + 80, ly, pad_x + 94, ly + 14], fill="#FFF8E1", outline="#FF9800")
        draw.text((pad_x + 98, ly - 1), "今日", fill="#333333", font=font)
        draw.rectangle([pad_x + 146, ly, pad_x + 160, ly + 14], fill="#F8F9FA", outline="#E0E0E0")
        draw.text((pad_x + 164, ly - 1), "未签到", fill="#999999", font=font)

        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sender_qq": sender_qq,
            "sender_mc": sender_mc,
            "receiver_qq": receiver_qq,
            "receiver_mc": receiver_mc,
            "amount": amount,
            "balance_after": balance_after,
        }
        try:
            with open(self.transfer_log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"写入转账日志失败: {e}")

    async def initialize(self):
        logger.info("mcman plugin by kdj")
        await self._start_mc_chat_watcher_if_configured()

    async def _stop_mc_chat_watcher_if_running(self):
        if self._mc_chat_stop is not None:
            self._mc_chat_stop.set()
        t = self._mc_chat_task
        self._mc_chat_task = None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _start_mc_chat_watcher_if_configured(self):
        await self._stop_mc_chat_watcher_if_running()
        self._mc_chat_stop = asyncio.Event()
        self._mc_chat_file_pos = [0]
        self._mc_chat_first_boot = [True]
        if not self.mc_chat_log_to_qq_enabled:
            return
        if not (self.mc_chat_unified_msg_origin or self.mc_chat_qq_group_id):
            logger.warning(
                "mc_chat_log_to_qq_enabled 已开启但未配置 QQ 目标（mc_chat_unified_msg_origin 或 mc_chat_qq_group_id），跳过"
            )
            return
        if self.mc_chat_source == "mcsm":
            if not (self.mcsm_panel_url and self.mcsm_api_key and self.mcsm_instance_uuid and self.mcsm_daemon_uuid):
                logger.warning("MCSM 模式缺少必要配置（panel_url/api_key/instance_uuid/daemon_uuid），跳过")
                return
            self._mc_chat_task = asyncio.create_task(
                self._mcsm_chat_loop(), name="mcman_mcsm_chat"
            )
            logger.info("MC 聊天监听已启动（MCSM WebSocket 模式）")
        else:
            if not self.mc_chat_log_path:
                logger.warning("mc_chat_log_to_qq_enabled 已开启但未配置 mc_chat_log_path，跳过日志监听")
                return
            self._mc_chat_task = asyncio.create_task(
                self._mc_chat_log_tail_loop(), name="mcman_mc_chat_tail"
            )
            logger.info("MC 聊天监听已启动（日志文件模式）")

    def _mc_chat_read_new_lines_sync(self) -> list[str]:
        path = self.mc_chat_log_path
        if not path or not os.path.isfile(path):
            return []
        lines_out: list[str] = []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            size = os.path.getsize(path)
            if size < self._mc_chat_file_pos[0]:
                self._mc_chat_file_pos[0] = 0
            if (
                self._mc_chat_file_pos[0] == 0
                and self._mc_chat_first_boot[0]
                and self.mc_chat_log_tail_from_end
            ):
                f.seek(0, os.SEEK_END)
                self._mc_chat_file_pos[0] = f.tell()
                self._mc_chat_first_boot[0] = False
                return lines_out
            f.seek(self._mc_chat_file_pos[0])
            chunk = f.read()
            self._mc_chat_file_pos[0] = f.tell()
        return chunk.splitlines()

    def _parse_mc_chat_log_line(self, line: str) -> tuple[str, str] | None:
        # 去除所有 ANSI 转义序列（颜色、清行等）
        line = re.sub(r"\x1b\[.*?[A-Za-z]", "", line).strip()
        if "<" not in line:
            return None
        m = _MC_LOG_CHAT_RE.search(line)
        if not m:
            return None
        player = m.group("player").strip()
        body = m.group("body").strip()
        prefix = self.mc_chat_trigger_prefix
        if not body.startswith(prefix):
            return None
        payload = body[len(prefix) :].strip()
        if not payload:
            return None
        return player, payload

    def _mc_chat_rate_ok(self, player: str) -> bool:
        now = time.monotonic()
        dq = self._mc_chat_rate[player]
        while dq and now - dq[0] > 60.0:
            dq.popleft()
        if len(dq) >= 12:
            return False
        dq.append(now)
        return True

    async def _mc_chat_send_to_qq(self, player: str, text: str):
        body = f"{player}：{text}"
        chain = MessageChain().message(body)
        try:
            if self.mc_chat_unified_msg_origin:
                await self.context.send_message(self.mc_chat_unified_msg_origin, chain)
            elif self.mc_chat_qq_group_id:
                await StarTools.send_message_by_id(
                    "GroupMessage",
                    self.mc_chat_qq_group_id,
                    chain,
                    self.mc_chat_qq_platform,
                )
        except Exception as e:
            logger.error(f"MC 游戏聊天转发到 QQ 失败: {e}")

    async def _mc_chat_log_tail_loop(self):
        try:
            while self._mc_chat_stop and not self._mc_chat_stop.is_set():
                await asyncio.sleep(0.85)
                try:
                    lines = await asyncio.to_thread(self._mc_chat_read_new_lines_sync)
                except Exception as e:
                    logger.error(f"读取 MC 日志失败: {e}")
                    continue
                for line in lines:
                    parsed = self._parse_mc_chat_log_line(line)
                    if not parsed:
                        continue
                    player, payload = parsed
                    if not self._mc_chat_rate_ok(player):
                        continue
                    await self._mc_chat_send_to_qq(player, payload)
        except asyncio.CancelledError:
            raise

    async def _mcsm_chat_loop(self):
        """通过 MCSM 面板 HTTP API 轮询服务端控制台输出，解析聊天转发到 QQ。"""
        import urllib.request
        base_url = (
            f"{self.mcsm_panel_url}/api/protected_instance/outputlog"
            f"?apikey={self.mcsm_api_key}"
            f"&uuid={self.mcsm_instance_uuid}"
            f"&daemonId={self.mcsm_daemon_uuid}"
            f"&size=512"
        )
        seen_tail = set()
        first_poll = True
        logger.info("[MCSM] 日志轮询已启动")
        while self._mc_chat_stop and not self._mc_chat_stop.is_set():
            await asyncio.sleep(1.5)
            try:
                req = urllib.request.Request(base_url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                log_text = data.get("data", "")
                if not log_text:
                    continue
                for line in log_text.splitlines():
                    line_hash = hash(line)
                    if line_hash in seen_tail:
                        continue
                    seen_tail.add(line_hash)
                    if len(seen_tail) > 5000:
                        seen_tail.clear()
                    if first_poll:
                        continue
                    # 直接关键字匹配：.wantwl
                    if ".wantwl" in line:
                        m = re.search(r"<(\S+)>", line)
                        wm = re.search(r"\.wantwl\s+(\S+)", line)
                        player = m.group(1) if m else None
                        input_code = wm.group(1).upper() if wm else None
                        pending_count = len(self._wl_pending)
                        logger.info(f"[MCSM] .wantwl行: player={player} code={input_code} pending={pending_count}")
                        if player and input_code:
                            now = time.time()
                            matched = False
                            for qqid, pending in list(self._wl_pending.items()):
                                expire_left = pending["expire"] - now
                                if expire_left <= 0:
                                    logger.info(f"[MCSM] pending过期: qq={qqid} mc={pending['mcname']}")
                                    del self._wl_pending[qqid]
                                    continue
                                logger.info(f"[MCSM] 匹配: player={player} vs mc={pending['mcname']} code={input_code} vs {pending['code']} 剩余{int(expire_left)}秒")
                                if player == pending["mcname"] and input_code == pending["code"]:
                                    del self._wl_pending[qqid]
                                    await self._do_bind_mc_notify(qqid, player)
                                    logger.info(f"[MCSM] 绑定成功: {player} -> qq={qqid}")
                                    matched = True
                                    break
                            if not matched and pending_count > 0:
                                logger.info(f"[MCSM] 未匹配到pending: player={player} code={input_code}")
                        continue
                    # 普通聊天转发（清理 ANSI 后解析）
                    clean_line = re.sub(r"\x1b\[.*?[A-Za-z]", "", line)
                    parsed = self._parse_mc_chat_log_line(clean_line)
                    if not parsed:
                        continue
                    player, payload = parsed
                    if not self._mc_chat_rate_ok(player):
                        continue
                    await self._mc_chat_send_to_qq(player, payload)
                first_poll = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[MCSM] 日志获取失败: {e}")

    async def _do_bind_mc_notify(self, qqid: str, mcname: str):
        """验证码验证通过后执行绑定并通知QQ。"""
        command = f"{self.whitelist_command} add {mcname}"
        try:
            await rcon_command(
                self.rcon_host, self.rcon_port, self.rcon_password, command
            )
            if qqid not in self.apply_data:
                self.apply_data[qqid] = []
            self.apply_data[qqid].append(mcname)
            self._save_apply_data()
            count = len(self.apply_data[qqid])
            body = f"玩家 {mcname} 绑定成功！已加入白名单（已绑定 {count} 个账号）"
            logger.info(f"[wantwl] {mcname} 绑定成功 (qq={qqid})")
        except Exception as e:
            body = f"玩家 {mcname} 绑定失败：{e}"
            logger.error(f"[wantwl] {mcname} 绑定失败: {e}")
        chain = MessageChain().message(body)
        try:
            if self.mc_chat_unified_msg_origin:
                await self.context.send_message(self.mc_chat_unified_msg_origin, chain)
            elif self.mc_chat_qq_group_id:
                await StarTools.send_message_by_id(
                    "GroupMessage", self.mc_chat_qq_group_id, chain, self.mc_chat_qq_platform
                )
        except Exception as e:
            logger.error(f"[wantwl] 通知QQ失败: {e}")

    def is_admin(self, qqid: str) -> bool:
        return qqid in self.admin_qqs

    def _event_message_str(self, event: AstrMessageEvent) -> str:
        raw = getattr(event, "message_str", None)
        if (raw is None or not str(raw).strip()) and hasattr(event, "get_message_str"):
            try:
                raw = event.get_message_str()
            except Exception:
                raw = ""
        return (str(raw) if raw is not None else "").strip()

    def _tail_after_command_names(self, event: AstrMessageEvent, *names: str) -> str:
        """从整段纯文本里截取「命令名 + 空格」之后的全部内容，避免多词只吃到第一个参数。"""
        raw = self._event_message_str(event)
        if not names:
            return ""
        alt = "|".join(re.escape(n) for n in names)
        m = re.match(rf"^/?(?:{alt})\s+(?P<payload>.+)$", raw, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group("payload").strip()
        return ""

    def _parse_mcrun_payload(self, event: AstrMessageEvent) -> str:
        return self._tail_after_command_names(event, "mcrun", "mcexec")

    @staticmethod
    def _mcrun_first_token_lower(console_cmd: str) -> str:
        parts = console_cmd.strip().split(maxsplit=1)
        if not parts:
            return ""
        t = parts[0].lower()
        if ":" in t:
            t = t.split(":")[-1]
        return t

    def _mcrun_blocked_reason(self, console_cmd: str) -> str | None:
        if not console_cmd.strip():
            return "请在 /mcrun 后输入要发送到服务端的命令。"
        first = self._mcrun_first_token_lower(console_cmd)
        if first in self.mcrun_blocked_first:
            return (
                f"出于安全考虑，已禁止以 {first} 开头的控制台命令。"
                "可在插件配置 mcrun_blocked_extra 中追加更多首词；"
                "默认拦截：stop、restart、op、deop、reload。"
            )
        return None

    async def _build_empty_response_hint(self, command: str) -> str:
        """空响应时，针对关键命令给出可验证的反馈。"""
        low_cmd = command.lower().strip()
        parts = low_cmd.split()
        if not parts:
            return "(空响应) 命令可能已执行成功，但服务器未返回文本。"

        if parts[0] == "ban" and len(parts) >= 2:
            target = parts[1]
            banlist_resp = await rcon_command(
                self.rcon_host,
                self.rcon_port,
                self.rcon_password,
                "banlist players",
            )
            banlist_text = strip_mc_color(banlist_resp).lower()
            if target in banlist_text:
                return f"(空响应) 已通过 banlist 校验：玩家 {target} 当前在黑名单中。"
            return (
                f"(空响应) 未在 banlist 中检索到 {target}，"
                "请确认玩家名大小写或查看服务端日志。"
            )

        if parts[0] == "pardon" and len(parts) >= 2:
            target = parts[1]
            banlist_resp = await rcon_command(
                self.rcon_host,
                self.rcon_port,
                self.rcon_password,
                "banlist players",
            )
            banlist_text = strip_mc_color(banlist_resp).lower()
            if target not in banlist_text:
                return f"(空响应) 已通过 banlist 校验：玩家 {target} 不在黑名单中。"
            return (
                f"(空响应) banlist 里仍能看到 {target}，"
                "请确认是否有同名记录或稍后重试。"
            )

        if low_cmd.startswith("spark "):
            return (
                "(空响应) Spark 的 health 等命令常把报告发到服务端控制台或游戏内管理员聊天，"
                "RCON 不一定能收到正文。请直接看服务器控制台；轻量数据可试 /mctps 或游戏内执行 spark health。"
            )

        return (
            "(空响应) 命令可能已执行成功，但服务器未返回文本。"
            "可在服务端执行 gamerule sendCommandFeedback true 观察反馈。"
        )

    async def execute_and_reply(
        self,
        event: AstrMessageEvent,
        command: str,
        desc: str,
        *,
        rcon_extra_recv_deadline_sec: float | None = None,
    ):
        """通用执行 + 回复逻辑"""
        user_name = event.get_sender_name()
        sender_qq = str(event.get_sender_id())
        named = f"{user_name}({sender_qq})"

        try:
            kw = {}
            if rcon_extra_recv_deadline_sec is not None:
                kw["extra_recv_deadline_sec"] = rcon_extra_recv_deadline_sec
            resp = await rcon_command(
                self.rcon_host, self.rcon_port, self.rcon_password, command, **kw
            )
            cresp = strip_mc_color(resp).strip()
            if not cresp:
                cresp = await self._build_empty_response_hint(command)
            logger.info(f"RCON 执行结果: {resp}")
            yield event.plain_result(
                f"已尝试执行 {command} ({desc})\n\n服务器返回：\n{cresp}"
            )
        except Exception as e:
            logger.error(f"RCON 执行失败: {e}")
            yield event.plain_result(f"操作失败：{e}")

    @filter.command("mcmanhelp", desc="MC 管理插件帮助", alias={"mchelp", "mch"})
    async def mcmanhelp(self, event: AstrMessageEvent):
        want = "已开启" if self.enable_apply_whitelist else "未开启"
        sign = "已开启" if self.enable_sign else "未开启"
        text = "\n".join(
            [
                "═══ MC 管理插件 ═══",
                "[管] = 需管理员权限  |  别名写在括号内",
                "",
                "【白名单】",
                "  /mcwl <add|remove|list> [MC名]  白名单管理（mcwhitelist）",
                "  /wantwl <MC名>  申请绑定（私聊）",
                "  /wantwllist  查看绑定（wantwll）",
                "  /wantwlunbind <MC名>  解绑（wantwlu）",
                "",
                "【封禁】",
                "  [管] /mcban <MC名> [原因]  封禁",
                "  [管] /mcunban <MC名>  解封（mcpardon）",
                "  /mcbanlist  封禁列表（mcbl）",
                "  [管] /mckick <MC名> [原因]  踢人（mck）",
                "  [管] /mctempban <MC名> <时长> [原因]  临时封禁（mctb）",
                "",
                "【服务器】",
                "  /mclist  在线玩家（mcl）",
                "  /mcplugins  插件列表",
                "  [管] /mctps  TPS",
                "  [管] /mcsparkhealth  Spark 健康（mcspark）",
                "  [管] /mcping [目标]  ping（mcp）",
                "  [管] /mcentitylist [选择器] [世界]  实体列表（mcel）",
                "",
                "【聊天】",
                "  /mcsay <内容>  游戏内说话（mcs）",
                "  [管] /mcbroadcast <内容>  广播（mcb）",
                "",
                "【经济】",
                f"  /mcsign  每日签到（{sign}，mcqd）",
                "  /mcsigncal  签到日历（mcsigncalendar）",
                "  /mcsignback [日期]  补签（mcbq）",
                "  /mcmoney  查询铜钱（mcqian）",
                "  /mctransfer <MC名> <数量>  转账（mczz）",
                "",
                "【其他】",
                "  [管] /mckill <MC名>  击杀",
                "  [管] /mcrun <命令>  RCON 透传（mcexec）",
                "  [管] /mcauthunregister <MC名>  AuthMe 注销",
                "  [管] /mcbindchat  绑定聊天转发",
                f"  白名单申请：{want}",
                "",
                "帮助：/mcmanhelp（mchelp）",
            ]
        )
        yield event.plain_result(text)

    @filter.command("mcwl", desc="MC 白名单管理", alias={"mcwhitelist"})
    async def mcwl(self, event: AstrMessageEvent, o: str, mcname: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"{self.whitelist_command} {o} {mcname}".strip()
        async for msg in self.execute_and_reply(event, command, "白名单管理"):
            yield msg

    @filter.command("mcban", desc="MC 黑名单添加")
    async def mcban(self, event: AstrMessageEvent, mcname: str = "", reason: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        if not mcname:
            yield event.plain_result("请输入要封禁的玩家名，例如：/mcban Steve [原因]")
            return
        command = f"ban {mcname} {reason}".strip()
        async for msg in self.execute_and_reply(event, command, "黑名单添加"):
            yield msg

    @filter.command("mcunban", desc="MC 黑名单移除", alias={"mcpardon"})
    async def mcunban(self, event: AstrMessageEvent, mcname: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"unban {mcname}".strip()
        async for msg in self.execute_and_reply(event, command, "黑名单移除"):
            yield msg

    @filter.command("mcbanlist", desc="MC 黑名单查看", alias={"mcbl"})
    async def mcbl(self, event: AstrMessageEvent):
        async for msg in self.execute_and_reply(
            event, "banlist players", "查看黑名单"
        ):
            yield msg

    @filter.command("mclist", desc="MC 查看在线玩家", alias={"mcl"})
    async def mclist(self, event: AstrMessageEvent):
        try:
            # 用 Server List Ping 获取完整玩家列表（不受 RCON 4096 字节限制）
            status = await mc_server_list_ping(self.rcon_host, self.mc_server_port)
            players_info = status.get("players", {})
            online = players_info.get("online", 0)
            max_players = players_info.get("max", 0)
            sample = players_info.get("sample", []) or []
            logger.info(f"[mclist] Ping 返回: online={online}, sample 数量={len(sample)}")
            if sample:
                player_names = sorted(p["name"] for p in sample if "name" in p)
                player_list = ", ".join(player_names)
                yield event.plain_result(
                    f"在线玩家 ({online}/{max_players})：\n{player_list}"
                )
            else:
                yield event.plain_result(
                    f"在线人数：{online}/{max_players}\n（服务端未返回玩家名列表）"
                )
        except Exception as e:
            logger.error(f"Server List Ping 失败: {e}")
            # 回退到 RCON list 命令
            async for msg in self.execute_and_reply(event, "list", "查看在线玩家"):
                yield msg

    @filter.command("mckick", desc="MC 踢出指定玩家", alias={"mck"})
    async def mckick(self, event: AstrMessageEvent, mcname: str = "", reason: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"kick {mcname} {reason}".strip()
        async for msg in self.execute_and_reply(event, command, "踢出玩家"):
            yield msg

    @filter.command("mctempban", desc="MC 临时黑名单", alias={"mctb"})
    async def mctempban(
        self,
        event: AstrMessageEvent,
        mcname: str = "",
        time: str = "",
        reason: str = "",
    ):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"tempban {mcname} {time} {reason}".strip()
        async for msg in self.execute_and_reply(event, command, "临时封禁"):
            yield msg

    @filter.command("mcsay", desc="MC 说话", alias={"mcs"})
    async def mcsay(self, event: AstrMessageEvent):
        user_name = event.get_sender_name()
        sender_qq = str(event.get_sender_id())
        named = f"{user_name}({sender_qq})"

        text = self._tail_after_command_names(event, "mcsay", "mcs")
        if not text:
            yield event.plain_result("请输入信息，例如：/mcsay 你好 世界")
            return

        message = [
            {"text": f"(QQ消息) ", "color": "aqua"},
            {"text": f"<{named}>", "color": "green", "underlined": True},
            {"text": " 说: ", "color": "white"},
            {"text": text, "color": "yellow"},
        ]
        command = f"minecraft:tellraw @a {json.dumps(message, ensure_ascii=False)}"
        try:
            await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, command)
        except Exception as e:
            yield event.plain_result(f"发送失败：{e}")
            return
        yield event.plain_result(f"{named}：{text}")

    @filter.command("mcbroadcast", desc="MC 广播消息", alias={"mcb", "mcbc"})
    async def mcbroadcast(self, event: AstrMessageEvent):
        user_name = event.get_sender_name()
        sender_qq = str(event.get_sender_id())
        named = f"{user_name}({sender_qq})"
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        text = self._tail_after_command_names(event, "mcbroadcast", "mcb", "mcbc")
        if not text:
            yield event.plain_result("请输入广播内容，例如：/mcbroadcast 维护通知 今晚")
            return

        message = [
            {"text": f"<管理员广播消息>", "color": "green", "underlined": True},
            {"text": " ", "color": "white", "underlined": False},
            {"text": text, "color": "yellow", "underlined": False},
        ]
        command = f"minecraft:tellraw @a {json.dumps(message, ensure_ascii=False)}"
        async for msg in self.execute_and_reply(event, command, "广播消息"):
            yield msg

    @filter.command("wantwl", desc="申请MC白名单")
    async def wantwl(self, event: AstrMessageEvent, mcname: str = ""):
        if not self.enable_apply_whitelist:
            yield event.plain_result("抱歉，白名单申请功能未开启。")
            return
        # 只允许私聊绑定
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if "GroupMessage" in umo:
            yield event.plain_result("白名单绑定请私聊发送，不要在群聊内操作。")
            return
        if not mcname:
            yield event.plain_result("请输入要绑定的MC用户名，例如：/wantwl Steve")
            return

        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if mcname in bound:
            yield event.plain_result(f"你已经绑定过MC账号 {mcname} 了。")
            return
        # 检查 MC 名是否已被其他人绑定
        for q, names in self.apply_data.items():
            if q != qqid and mcname in names:
                yield event.plain_result(f"MC账号 {mcname} 已被其他QQ绑定。")
                return

        if self.whitelist_verify_mode == "code":
            # 验证码模式：用户在游戏内发送 .wantwl 验证码
            code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
            self._wl_pending[qqid] = {"mcname": mcname, "code": code, "expire": time.time() + 300}
            yield event.plain_result(
                f"请在游戏内聊天发送以下内容完成绑定：\n.wantwl {code}\n（5分钟内有效）"
            )
            return

        if self.whitelist_verify_mode == "online":
            # 在线验证：要求玩家当前在线
            try:
                list_resp = await rcon_command(
                    self.rcon_host, self.rcon_port, self.rcon_password, "list"
                )
                if mcname not in strip_mc_color(list_resp):
                    yield event.plain_result(f"玩家 {mcname} 当前不在线，请先登录服务器后再申请。")
                    return
            except Exception:
                pass

        # 无需验证或在线验证通过，直接绑定
        async for msg in self._do_bind_mc(event, qqid, mcname):
            yield msg

    async def _do_bind_mc(self, event: AstrMessageEvent, qqid: str, mcname: str):
        """执行实际的白名单绑定"""
        command = f"{self.whitelist_command} add {mcname}"
        try:
            resp = await rcon_command(
                self.rcon_host, self.rcon_port, self.rcon_password, command
            )
            if qqid not in self.apply_data:
                self.apply_data[qqid] = []
            self.apply_data[qqid].append(mcname)
            self._save_apply_data()
            count = len(self.apply_data[qqid])
            yield event.plain_result(
                f"成功绑定MC账号 {mcname} 并加入白名单！（已绑定 {count} 个账号）\n服务器返回：{strip_mc_color(resp)}"
            )
        except Exception as e:
            yield event.plain_result(f"申请失败：{e}")

    @filter.command("wantwllist", desc="查看已绑定的MC白名单", alias={"wantwll"})
    async def wantwllist(self, event: AstrMessageEvent):
        if not self.enable_apply_whitelist:
            yield event.plain_result("抱歉，白名单申请功能未开启。")
            return
        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if not bound:
            yield event.plain_result("你还没有绑定任何MC账号，使用 /wantwl <MC名> 申请。")
            return
        lines = [f"你已绑定 {len(bound)} 个MC账号："]
        for i, name in enumerate(bound, 1):
            lines.append(f"  {i}. {name}")
        lines.append("\n使用 /wantwlunbind <MC名> 解绑指定账号。")
        yield event.plain_result("\n".join(lines))

    @filter.command("wantwlunbind", desc="解绑MC白名单账号", alias={"wantwlu"})
    async def wantwlunbind(self, event: AstrMessageEvent, mcname: str = ""):
        if not self.enable_apply_whitelist:
            yield event.plain_result("抱歉，白名单申请功能未开启。")
            return
        if not mcname:
            yield event.plain_result("请输入要解绑的MC用户名，例如：/wantwlunbind Steve")
            return
        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if mcname not in bound:
            yield event.plain_result(f"你没有绑定过MC账号 {mcname}。")
            return
        # 调用RCON移除白名单
        command = f"{self.whitelist_command} remove {mcname}"
        try:
            resp = await rcon_command(
                self.rcon_host, self.rcon_port, self.rcon_password, command
            )
            bound.remove(mcname)
            if not bound:
                del self.apply_data[qqid]
            self._save_apply_data()
            yield event.plain_result(
                f"已解绑MC账号 {mcname} 并移除白名单。\n服务器返回：{strip_mc_color(resp)}"
            )
        except Exception as e:
            yield event.plain_result(f"解绑失败：{e}")

    @filter.command("mcsign", desc="每日签到领铜钱", alias={"mcqd"})
    async def mcsign(self, event: AstrMessageEvent):
        if not self.enable_sign:
            yield event.plain_result("抱歉，签到功能未开启。")
            return
        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if not bound:
            yield event.plain_result("你还没有绑定MC账号，请先使用 /wantwl <MC名> 绑定。")
            return
        today = time.strftime("%Y-%m-%d")
        yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
        today_signers = self.sign_data.get(today, [])
        if qqid in today_signers:
            yield event.plain_result("你今天已经签到过了，明天再来吧！")
            return
        mcname = bound[0]
        today_signers.append(qqid)
        self.sign_data[today] = today_signers
        self._save_sign_data()
        today_count = len(today_signers)
        yesterday_signers = self.sign_data.get(yesterday, [])
        yesterday_count = len(yesterday_signers)
        streak = self._sign_consecutive_days(qqid)
        if yesterday_count > 0:
            pool = random.randint(self.sign_money_min, self.sign_money_max)
            reward = pool // yesterday_count
            if reward > 0:
                cmd = self.sign_money_command.replace("{name}", mcname).replace("{amount}", str(reward))
                try:
                    await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, cmd)
                except Exception as e:
                    yield event.plain_result(f"签到失败：{e}")
                    return
                yield event.plain_result(
                    f"签到成功！{mcname} +{reward} 铜钱\n"
                    f"奖池 {pool} / 昨日 {yesterday_count} 人 = 每人 {reward}\n"
                    f"今日已签到 {today_count} 人  |  连续签到 {streak} 天"
                )
            else:
                yield event.plain_result(
                    f"签到成功！昨日 {yesterday_count} 人签到，奖池不足以平分\n"
                    f"今日已签到 {today_count} 人  |  连续签到 {streak} 天"
                )
        else:
            yield event.plain_result(
                f"签到成功！昨日无人签到，今日无奖励\n"
                f"今日已签到 {today_count} 人  |  连续签到 {streak} 天"
            )

    @filter.command("mcsignreset", desc="重置签到记录（管理员）", alias={"mcsignr"})
    async def mcsignreset(self, event: AstrMessageEvent, target: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        if not target:
            yield event.plain_result("用法：/mcsignreset all（重置所有人）或 /mcsignreset <QQ号>（重置指定人）")
            return
        if target == "all":
            self.sign_data = {}
            self._save_sign_data()
            yield event.plain_result("已重置所有签到记录。")
        else:
            removed = 0
            for date_str in self.sign_data:
                signers = self.sign_data[date_str]
                if target in signers:
                    signers.remove(target)
                    removed += 1
            self._save_sign_data()
            if removed:
                yield event.plain_result(f"已从 {removed} 天的签到记录中移除 QQ {target}。")
            else:
                yield event.plain_result(f"QQ {target} 没有签到记录。")

    @filter.command("mcsigncal", desc="查看签到日历", alias={"mcsigncalendar", "mcqc"})
    async def mcsigncal(self, event: AstrMessageEvent):
        if not self.enable_sign:
            yield event.plain_result("抱歉，签到功能未开启。")
            return
        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if not bound:
            yield event.plain_result("你还没有绑定MC账号，请先使用 /wantwl <MC名> 绑定。")
            return
        user_name = bound[0]
        # 支持查看指定月份：/mcsigncal 2026-06
        raw = self._tail_after_command_names(event, "mcsigncal", "mcsigncalendar", "mcqc")
        year, month = None, None
        if raw:
            m = re.match(r"^(\d{4})-(\d{1,2})$", raw.strip())
            if m:
                year, month = int(m.group(1)), int(m.group(2))
                if not (1 <= month <= 12):
                    yield event.plain_result("月份无效，请用格式 /mcsigncal 2026-06")
                    return
        img_bytes = self._generate_sign_calendar(qqid, user_name, year, month)
        if img_bytes is None:
            # 无 Pillow，退化为文本日历
            now = datetime.now()
            y = year or now.year
            m = month or now.month
            total = calendar.monthrange(y, m)[1]
            signed = []
            for d in range(1, total + 1):
                ds = f"{y}-{m:02d}-{d:02d}"
                if qqid in self.sign_data.get(ds, []):
                    signed.append(d)
            streak = self._sign_consecutive_days(qqid)
            lines = [f"📅 {y} 年 {m} 月签到日历（{user_name}）", f"签到 {len(signed)}/{total} 天  |  连续 {streak} 天", ""]
            week = "日 一 二 三 四 五 六"
            lines.append(week)
            first_weekday, _ = calendar.monthrange(y, m)
            row = "   " * first_weekday
            for d in range(1, total + 1):
                tag = f"{'✅' if d in signed else d:2}"
                row += f"{tag} "
                if (first_weekday + d) % 7 == 0:
                    lines.append(row.rstrip())
                    row = ""
            if row.strip():
                lines.append(row.rstrip())
            yield event.plain_result("\n".join(lines))
            return
        try:
            import base64
            b64 = base64.b64encode(img_bytes).decode()
            chain = MessageChain().base64_image(b64)
            yield event.chain_result(chain)
        except Exception as e:
            logger.warning(f"发送签到日历图片失败: {e}")
            yield event.plain_result(f"生成日历图片失败：{e}")

    @filter.command("mcsignback", desc="补签（花铜钱补往日签到）", alias={"mcsignbackfill", "mcbq"})
    async def mcsignback(self, event: AstrMessageEvent):
        if not self.enable_sign:
            yield event.plain_result("抱歉，签到功能未开启。")
            return
        qqid = str(event.get_sender_id())
        bound = self.apply_data.get(qqid, [])
        if not bound:
            yield event.plain_result("你还没有绑定MC账号，请先使用 /wantwl <MC名> 绑定。")
            return
        mcname = bound[0]
        # /mcsignback 2026-06-13  或  /mcsignback 13（默认本月）
        raw = self._tail_after_command_names(event, "mcsignback", "mcsignbackfill", "mcbq")
        if not raw.strip():
            cost = self.sign_backfill_cost_per_day
            # 列出本月可补签的日期
            now = datetime.now()
            year, month = now.year, now.month
            total = calendar.monthrange(year, month)[1]
            today_d = now.day
            missed = []
            for d in range(1, today_d):
                ds = f"{year}-{month:02d}-{d:02d}"
                if qqid not in self.sign_data.get(ds, []):
                    missed.append(ds)
            if not missed:
                yield event.plain_result("本月至今没有漏签，太棒了！")
                return
            streak = self._sign_consecutive_days(qqid)
            lines = [
                "═══ 补签 ═══",
                f"补签费用： 基础费用{cost} × 2^(天数-1) /天",
                f"当前连续签到：{streak} 天",
                "",
                f"可补签日期（{len(missed)} 天）：",
            ]
            for ds in missed:
                d = int(ds.split("-")[2])
                days_back = today_d - d
                day_cost = cost * (2 ** (days_back - 1))
                lines.append(f"  {ds}  费用 {day_cost} 铜钱")
            lines.append("")
            lines.append("用法：/mcsignback 2026-06-10 或 /mcsignback 10")
            yield event.plain_result("\n".join(lines))
            return
        # 解析目标日期
        target_str = raw.strip()
        now = datetime.now()
        m = re.match(r"^(\d{1,2})$", target_str)
        if m:
            day = int(m.group(1))
            target_date = datetime(now.year, now.month, day).date()
        else:
            m2 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", target_str)
            if m2:
                target_date = datetime(int(m2.group(1)), int(m2.group(2)), int(m2.group(3))).date()
            else:
                yield event.plain_result("日期格式错误，请用 /mcsignback 13 或 /mcsignback 2026-06-13")
                return
        today = now.date()
        if target_date >= today:
            yield event.plain_result("只能补签过去的日期。今天的签到请用 /mcsign")
            return
        if target_date.month != today.month or target_date.year != today.year:
            yield event.plain_result("只能补签本月的日期。")
            return
        ds = target_date.strftime("%Y-%m-%d")
        if qqid in self.sign_data.get(ds, []):
            yield event.plain_result(f"{ds} 已经签过到了，不需要补签。")
            return
        days_back = (today - target_date).days
        cost = self.sign_backfill_cost_per_day * (2 ** (days_back - 1))
        # 查询余额
        get_cmd = f"{self.money_command_prefix} get {mcname}"
        try:
            resp = await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, get_cmd)
            resp_clean = strip_mc_color(resp).strip()
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", resp_clean)
            if not nums:
                yield event.plain_result(f"无法解析铜钱余额：{resp_clean}")
                return
            balance = int(float(nums[-1]))
        except Exception as e:
            yield event.plain_result(f"查询余额失败：{e}")
            return
        if balance < cost:
            yield event.plain_result(f"余额不足！补签 {ds} 需要 {cost} 铜钱，你当前有 {balance} 铜钱。")
            return
        # 扣款
        sub_cmd = f"{self.money_command_prefix} sub {mcname} {cost}"
        try:
            await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, sub_cmd)
        except Exception as e:
            yield event.plain_result(f"扣款失败：{e}")
            return
        # 发放补签奖励
        yesterday_count = len(self.sign_data.get(
            (target_date + timedelta(days=1)).strftime("%Y-%m-%d"), []
        ))
        reward = 0
        if yesterday_count > 0:
            pool = random.randint(self.sign_money_min, self.sign_money_max)
            reward = pool // yesterday_count
            if reward > 0:
                add_cmd = self.sign_money_command.replace("{name}", mcname).replace("{amount}", str(reward))
                try:
                    await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, add_cmd)
                except Exception as e:
                    # 奖励发放失败，回滚扣款
                    rollback = f"{self.money_command_prefix} add {mcname} {cost}"
                    try:
                        await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, rollback)
                    except Exception:
                        pass
                    yield event.plain_result(f"发放奖励失败，已回滚扣款：{e}")
                    return
        # 记录补签
        signers = self.sign_data.get(ds, [])
        signers.append(qqid)
        self.sign_data[ds] = signers
        self._save_sign_data()
        streak = self._sign_consecutive_days(qqid)
        net = reward - cost
        yield event.plain_result(
            f"补签成功！{ds} 已记录签到\n"
            f"扣款 -{cost} 铜钱  |  奖励 +{reward} 铜钱  |  净变动 {net:+d}\n"
            f"当前连续签到 {streak} 天"
        )

    @filter.command("mcmoney", desc="查询铜钱余额", alias={"mcqian", "mcq"})
    async def mcmoney(self, event: AstrMessageEvent, mcname: str = ""):
        qqid = str(event.get_sender_id())
        if mcname:
            # 管理员查询指定玩家
            if not self.is_admin(qqid):
                yield event.plain_result("只有管理员可以查询他人的铜钱余额。")
                return
            targets = [mcname]
        else:
            bound = self.apply_data.get(qqid, [])
            if not bound:
                yield event.plain_result("你还没有绑定MC账号，请先使用 /wantwl <MC名> 绑定。")
                return
            targets = bound
        lines = []
        for name in targets:
            cmd = f"{self.money_command_prefix} get {name}"
            try:
                resp = await rcon_command(
                    self.rcon_host, self.rcon_port, self.rcon_password, cmd
                )
                cresp = strip_mc_color(resp).strip()
                lines.append(f"  {name}: {cresp if cresp else '(空响应)'}")
            except Exception as e:
                lines.append(f"  {name}: 查询失败 - {e}")
        yield event.plain_result("铜钱余额：\n" + "\n".join(lines))

    @filter.command("mctransfer", desc="转账铜钱给其他玩家", alias={"mctrans", "mczz"})
    async def mctransfer(self, event: AstrMessageEvent):
        qqid = str(event.get_sender_id())
        raw = self._event_message_str(event)
        m = re.match(r"^/?mctrans(?:fer)?\s+(\S+)\s+(\d+)\s*$", raw, re.IGNORECASE)
        if not m:
            yield event.plain_result("用法：/mctransfer <MC名> <数量>\n例如：/mctransfer Steve 100")
            return
        receiver_mc = m.group(1).lstrip("@")
        amount = int(m.group(2))
        if amount <= 0:
            yield event.plain_result("金额必须大于 0。")
            return
        # 检查发送方绑定
        sender_bound = self.apply_data.get(qqid, [])
        if not sender_bound:
            yield event.plain_result("你还没有绑定MC账号，请先使用 /wantwl <MC名> 绑定。")
            return
        sender_mc = sender_bound[0]
        if sender_mc == receiver_mc:
            yield event.plain_result("不能给自己转账。")
            return
        # 查询发送方余额
        get_cmd = f"{self.money_command_prefix} get {sender_mc}"
        try:
            resp = await rcon_command(
                self.rcon_host, self.rcon_port, self.rcon_password, get_cmd
            )
            resp_clean = strip_mc_color(resp).strip()
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", resp_clean)
            if not nums:
                yield event.plain_result(f"无法解析你的铜钱余额：{resp_clean}")
                return
            balance = int(float(nums[-1]))
            if balance < amount:
                yield event.plain_result(f"余额不足！你当前有 {balance} 铜钱，需要 {amount}。")
                return
        except Exception as e:
            yield event.plain_result(f"查询余额失败：{e}")
            return
        # 执行转账
        sub_cmd = f"{self.money_command_prefix} sub {sender_mc} {amount}"
        add_cmd = f"{self.money_command_prefix} add {receiver_mc} {amount}"
        try:
            await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, sub_cmd)
        except Exception as e:
            yield event.plain_result(f"扣款失败：{e}")
            return
        try:
            await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, add_cmd)
        except Exception as e:
            rollback_cmd = f"{self.money_command_prefix} add {sender_mc} {amount}"
            try:
                await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, rollback_cmd)
            except Exception:
                pass
            yield event.plain_result(f"加款失败，已尝试回滚：{e}")
            return
        # 查询转账后余额
        new_balance_str = ""
        new_balance = None
        try:
            resp2 = await rcon_command(self.rcon_host, self.rcon_port, self.rcon_password, get_cmd)
            resp2_clean = strip_mc_color(resp2).strip()
            nums2 = re.findall(r"[-+]?\d+(?:\.\d+)?", resp2_clean)
            if nums2:
                new_balance = int(float(nums2[-1]))
                new_balance_str = f"\n当前余额：{new_balance} 铜钱"
        except Exception:
            pass
        self._log_transfer(qqid, sender_mc, "", receiver_mc, amount, new_balance)
        yield event.plain_result(
            f"{sender_mc} → {receiver_mc}：{amount} 铜钱{new_balance_str}"
        )

    @filter.command("mckill", desc="MC kill人")
    async def mckill(self, event: AstrMessageEvent, mcname: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"kill {mcname}".strip()
        async for msg in self.execute_and_reply(event, command, "kill"):
            yield msg

    @filter.command("mcplugins", desc="MC 插件列表")
    async def mcplugins(
        self,
        event: AstrMessageEvent,
    ):
        command = f"plugins".strip()
        async for msg in self.execute_and_reply(event, command, "插件列表"):
            yield msg

    @filter.command("mcentitylist", desc="Paper 实体列表", alias={"mcel"})
    async def mcentitylist(
        self,
        event: AstrMessageEvent,
        selector: str = "*",
        world: str = "world",
    ):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"paper entity list {selector} {world}".strip()
        async for msg in self.execute_and_reply(event, command, "Paper 实体列表"):
            yield msg

    @filter.command("mcping", desc="MC ping", alias={"mcp"})
    async def mcping(self, event: AstrMessageEvent, target: str = "@a"):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        command = f"ping {target}".strip()
        async for msg in self.execute_and_reply(event, command, "ping"):
            yield msg

    @filter.command("mcsparkhealth", desc="Spark 健康报告", alias={"mcspark", "mcsh"})
    async def mcsparkhealth(self, event: AstrMessageEvent):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        async for msg in self.execute_and_reply(
            event,
            "spark health",
            "Spark 健康",
            rcon_extra_recv_deadline_sec=8.0,
        ):
            yield msg

    @filter.command("mctps", desc="MC 查看 TPS")
    async def mctps(self, event: AstrMessageEvent):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        async for msg in self.execute_and_reply(event, "tps", "TPS"):
            yield msg

    @filter.command("mcauthunregister", desc="AuthMe 注销玩家", alias={"mcauthunreg"})
    async def mcauthunregister(self, event: AstrMessageEvent, mcname: str = ""):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        if not mcname:
            yield event.plain_result(
                "请输入要注销的 MC 用户名，例如：/mcauthunregister Steve"
            )
            return
        command = f"authme unregister {mcname}".strip()
        async for msg in self.execute_and_reply(event, command, "AuthMe 注销"):
            yield msg

    @filter.command("mcrun", desc="RCON 透传控制台命令", alias={"mcexec"})
    async def mcrun(self, event: AstrMessageEvent):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        payload = self._parse_mcrun_payload(event)
        if not payload:
            yield event.plain_result(
                "用法：/mcrun <任意控制台命令>\n"
                "示例：/mcrun save-all\n"
                "说明：从整段消息里截取命令，支持空格与参数；"
                "首个词在拦截列表中则拒绝（默认：stop、restart、op、deop、reload）。"
            )
            return
        blocked = self._mcrun_blocked_reason(payload)
        if blocked:
            yield event.plain_result(blocked)
            return
        extra_deadline = (
            8.0 if payload.lower().lstrip().startswith("spark ") else None
        )
        async for msg in self.execute_and_reply(
            event,
            payload,
            "RCON 透传",
            rcon_extra_recv_deadline_sec=extra_deadline,
        ):
            yield msg

    @filter.command("mcbindchat", desc="绑定当前会话为 MC 游戏聊天转发目标（管理员）")
    async def mcbindchat(self, event: AstrMessageEvent):
        if not self.is_admin(str(event.get_sender_id())):
            yield event.plain_result("抱歉，你没有权限执行此操作。")
            return
        umo = getattr(event, "unified_msg_origin", "") or ""
        if not str(umo).strip():
            yield event.plain_result(
                "无法获取当前会话 unified_msg_origin，请改用配置项 mc_chat_unified_msg_origin 或 mc_chat_qq_group_id。"
            )
            return
        self.mc_chat_unified_msg_origin = str(umo).strip()
        self._save_mc_chat_target_to_file(self.mc_chat_unified_msg_origin)
        await self._start_mc_chat_watcher_if_configured()
        yield event.plain_result(
            "已写入转发目标（mc_chat_qq_target.json）。\n"
            "请确认：1) 已开启 mc_chat_log_to_qq_enabled；2) mc_chat_log_path 指向该服 latest.log（与 AstrBot 能读到的路径一致）。\n"
            "游戏内发：<触发前缀>内容>，默认触发前缀为 .mcsay （见 mc_chat_trigger_prefix）。"
        )

    async def terminate(self):
        logger.info("mcman plugin stopped")
        await self._stop_mc_chat_watcher_if_running()
