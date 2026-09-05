# -*- coding: utf-8 -*-
"""小智联网查询MCP 容器主服务。

职责：
1. 在 8765 端口提供配置网页与 /api/config 读写接口（不把密钥回显给浏览器）；
2. 把配置持久化到 /config/.xiaozhi_mcp_config.json（宿主机 @appdata 目录）；
3. 监督 mcp_pipe 子进程：配置保存后自动重启，进程崩溃后自动拉起。

所有密钥/令牌不会写入日志。
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# 优先使用东八区，避免容器默认 UTC 导致日志时间差 8 小时
if os.name == "posix" and hasattr(time, "tzset"):
    os.environ.setdefault("TZ", "Asia/Shanghai")
    time.tzset()

APP_DIR = Path(__file__).resolve().parent
WWW_DIR = APP_DIR / "www"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config_manager import CONFIG_PATH, load_config, save_config  # noqa: E402


# 容器内默认就是 UTF-8；此设置便于在 Windows 本地调试时日志不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CONFIG_PORT = int(os.environ.get("WEBVOX_PORT", "8765"))
LOG_DIR = CONFIG_PATH.parent / "logs"
BRIDGE_LOG_MAX_BYTES = 256 * 1024
REDACTED_TOKEN = "__WEBVOX_REDACTED__"
SECRET_QUERY_KEYS = {"token", "key", "apikey", "api_key", "access_token", "secret"}

# 供“运行日志”页面读取的内存环形缓冲（不包含密钥/令牌内容）
LOG_BUFFER = deque(maxlen=800)


def log(msg):
    entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    LOG_BUFFER.append(entry)
    try:
        print(entry, flush=True)
    except Exception:  # noqa: BLE001 - 日志失败不能影响配置保存
        pass


# ---------------------------------------------------------------------------
# 桥接进程（mcp_pipe）管理
# ---------------------------------------------------------------------------


class BridgeManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.connected = False
        self.started_at = None
        self.last_error = ""
        self._reader = None

    @property
    def alive(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def _append_log(self, line):
        entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [bridge] {line}"
        LOG_BUFFER.append(entry)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            bridge_log = LOG_DIR / "bridge.log"
            # 超过 256KB 滚动为 bridge.log.old，避免 @appdata 日志无限增长
            try:
                if (
                    bridge_log.exists()
                    and bridge_log.stat().st_size > BRIDGE_LOG_MAX_BYTES
                ):
                    backup = LOG_DIR / "bridge.log.old"
                    backup.unlink(missing_ok=True)
                    os.replace(bridge_log, backup)
            except OSError:
                pass
            with open(bridge_log, "a", encoding="utf-8") as fh:
                fh.write(entry + "\n")
        except OSError:
            pass
        # 同步输出到 docker logs
        try:
            print(entry, flush=True)
        except Exception:  # noqa: BLE001
            pass
        self._update_connected(line)

    def _update_connected(self, line):
        connected_markers = (
            "Successfully connected to WebSocket server",
            "已连接",
            "Successfully connected",
        )
        disconnected_markers = (
            "Connection closed",
            "连接已断开",
            "WebSocket connection closed",
            "Connection error",
        )
        with self.lock:
            if any(m in line for m in connected_markers):
                self.connected = True
                self.last_error = ""
            elif any(m in line for m in disconnected_markers):
                self.connected = False
                self.last_error = line.strip()

    def set_error(self, message):
        with self.lock:
            self.last_error = message

    def _read_pipe(self):
        proc = self.proc
        if proc is None:
            return
        for raw in iter(proc.stdout.readline, ""):
            line = raw.rstrip("\n")
            if line:
                self._append_log(line)

    def start(self):
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                return
            try:
                self.proc = subprocess.Popen(
                    [sys.executable, str(APP_DIR / "mcp_pipe.py"), str(APP_DIR / "联网查询.py")],
                    cwd=str(APP_DIR),
                    env=dict(os.environ),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    start_new_session=True,
                    bufsize=1,
                )
            except OSError as exc:
                self.last_error = f"启动 mcp_pipe 失败: {exc}"
                log(self.last_error)
                self.proc = None
                return False
            self.connected = False
            self.started_at = time.time()
            self.last_error = ""
            self._reader = threading.Thread(target=self._read_pipe, daemon=True)
            self._reader.start()
            log(f"已启动 MCP 桥接进程（pid={self.proc.pid}）")
            return True

    def stop(self, reason=""):
        with self.lock:
            proc = self.proc
            self.proc = None
            self.connected = False
        if proc is None:
            return
        pid = proc.pid
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        if reason:
            log(f"MCP 桥接进程已停止（{reason}）")
        else:
            log("MCP 桥接进程已停止")

    def status(self):
        with self.lock:
            alive = self.proc is not None and self.proc.poll() is None
            return {
                "running": alive,
                "connected": bool(alive and self.connected),
                "startedAt": self.started_at,
                "lastError": self.last_error,
            }


# ---------------------------------------------------------------------------
# 配置读写与脱敏
# ---------------------------------------------------------------------------


def _redact_endpoint(endpoint):
    """把 URL 查询参数中的常见密钥值替换为占位符后再返回给页面。"""
    if not endpoint:
        return ""
    try:
        parts = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return endpoint
    if not parts.query:
        return endpoint
    redacted = []
    for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SECRET_QUERY_KEYS and value:
            value = REDACTED_TOKEN
        redacted.append((key, value))
    return urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urllib.parse.urlencode(redacted),
            parts.fragment,
        )
    )


def _restore_redacted_endpoint(old_endpoint, new_endpoint):
    """用户提交页面上的脱敏 URL 时，把占位符恢复成原令牌。"""
    old_endpoint = (old_endpoint or "").strip()
    new_endpoint = (new_endpoint or "").strip()
    if not new_endpoint:
        return ""
    if REDACTED_TOKEN not in new_endpoint:
        return new_endpoint
    try:
        old_parts = urllib.parse.urlsplit(old_endpoint)
        new_parts = urllib.parse.urlsplit(new_endpoint)
        old_query = dict(
            urllib.parse.parse_qsl(old_parts.query, keep_blank_values=True)
        )
    except ValueError:
        return new_endpoint.replace(REDACTED_TOKEN, "")
    merged = []
    changed = False
    for key, value in urllib.parse.parse_qsl(
        new_parts.query, keep_blank_values=True
    ):
        if value == REDACTED_TOKEN:
            restored = old_query.get(key, "")
            merged.append((key, restored))
            changed = True
        else:
            merged.append((key, value))
    result = urllib.parse.urlunsplit(
        (
            new_parts.scheme,
            new_parts.netloc,
            new_parts.path,
            urllib.parse.urlencode(merged) if merged else "",
            new_parts.fragment,
        )
    )
    return result if not changed else result


def _public_state(bridge):
    cfg = load_config()
    return {
        "configured": bool(cfg.get("MCP_ENDPOINT")),
        "endpoint": _redact_endpoint(cfg.get("MCP_ENDPOINT", "")),
        "provider": cfg.get("PROVIDER", "bailian-mcp"),
        "dashscopeMcpUrl": cfg.get("DASHSCOPE_MCP_URL", ""),
        "resultCount": int(cfg.get("RESULT_COUNT", 8)),
        "zhipuKeySet": bool(cfg.get("ZHIPU_API_KEY")),
        "dashscopeKeySet": bool(cfg.get("DASHSCOPE_API_KEY")),
        "bridge": bridge.status(),
    }


# ---------------------------------------------------------------------------
# HTTP 配置服务
# ---------------------------------------------------------------------------


WRITE_WINDOW_SECONDS = 60
WRITE_MAX_PER_WINDOW = 20
_write_history = {}
_write_history_lock = threading.Lock()


def _rate_limited(ip):
    now = time.time()
    with _write_history_lock:
        hits = [t for t in _write_history.get(ip, []) if now - t < WRITE_WINDOW_SECONDS]
        if len(hits) >= WRITE_MAX_PER_WINDOW:
            _write_history[ip] = hits
            return True
        hits.append(now)
        _write_history[ip] = hits
        return False


def _clean_mcp_url(value):
    url = str(value or "").strip()
    if not url:
        return "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    if not url.startswith(("http://", "https://")) or len(url) > 400:
        return ""
    return url


def _apply_save(payload, bridge):
    current = load_config()
    old_endpoint = current.get("MCP_ENDPOINT", "")
    new_cfg = dict(current)

    # 端点：页面传回的是脱敏 URL，恢复原令牌
    if "MCP_ENDPOINT" in payload:
        new_endpoint = _restore_redacted_endpoint(
            old_endpoint, str(payload.get("MCP_ENDPOINT") or "")
        )
        if new_endpoint and not new_endpoint.startswith(("ws://", "wss://")):
            return None, "MCP 服务地址必须以 ws:// 或 wss:// 开头"
        new_cfg["MCP_ENDPOINT"] = new_endpoint

    if "PROVIDER" in payload:
        provider = str(payload.get("PROVIDER") or "bailian-mcp").strip().lower()
        # 兼容旧页面仍提交 dashscope：原生搜索已下线，统一落到百炼 MCP
        if provider == "dashscope":
            provider = "bailian-mcp"
        if provider not in ("zhipu", "bailian-mcp"):
            return None, "请选择有效的联网搜索服务商"
        new_cfg["PROVIDER"] = provider

    if "DASHSCOPE_MCP_URL" in payload:
        mcp_url = _clean_mcp_url(payload.get("DASHSCOPE_MCP_URL"))
        if not mcp_url:
            return None, "百炼 MCP 服务地址必须以 http:// 或 https:// 开头"
        new_cfg["DASHSCOPE_MCP_URL"] = mcp_url

    if "RESULT_COUNT" in payload:
        try:
            new_cfg["RESULT_COUNT"] = max(1, min(50, int(payload.get("RESULT_COUNT"))))
        except (TypeError, ValueError):
            return None, "返回条数必须是 1-50 的整数"

    # 密钥：输入框留空表示保留原值；显式清除时删除
    if payload.get("CLEAR_ZHIPU_KEY") is True:
        new_cfg["ZHIPU_API_KEY"] = ""
    elif payload.get("ZHIPU_API_KEY"):
        new_cfg["ZHIPU_API_KEY"] = str(payload["ZHIPU_API_KEY"]).strip()

    if payload.get("CLEAR_DASHSCOPE_KEY") is True:
        new_cfg["DASHSCOPE_API_KEY"] = ""
    elif payload.get("DASHSCOPE_API_KEY"):
        new_cfg["DASHSCOPE_API_KEY"] = str(payload["DASHSCOPE_API_KEY"]).strip()

    try:
        save_config(new_cfg)
    except OSError as exc:
        return None, f"写入配置文件失败: {exc}"
    return new_cfg, None


def _log_save_summary():
    cfg = load_config()
    provider = cfg.get("PROVIDER", "bailian-mcp")
    if provider == "zhipu":
        key_ready = bool(cfg.get("ZHIPU_API_KEY"))
    else:
        key_ready = bool(cfg.get("DASHSCOPE_API_KEY"))
    log(
        "配置已保存：服务商="
        + provider
        + "，API Key="
        + ("已配置" if key_ready else "未配置（当前服务商搜索会被拒绝）")
    )


class ConfigHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WebVoxMCP/1.0"

    def log_message(self, fmt, *args):
        # 只记录路径，不带查询参数，避免把令牌写进日志
        return

    def _send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, Authorization"
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")

    def _send_file(self, rel_path):
        root = WWW_DIR.resolve()
        candidate = (root / rel_path.lstrip("/")).resolve()
        if root != candidate and root not in candidate.parents:
            self._send_json(403, {"error": "Forbidden"})
            return
        if not candidate.is_file():
            self._send_json(404, {"error": "Not Found"})
            return
        suffix = candidate.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".json": "application/json; charset=utf-8",
        }.get(suffix, "application/octet-stream")
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_json(500, {"error": "Read Error"})
            return
        self.send_response(200)
        self._send_cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_logs(self):
        try:
            raw_limit = int(
                urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query
                ).get("lines", ["300"])[0]
            )
        except (ValueError, IndexError):
            raw_limit = 300
        limit = max(1, min(raw_limit, 800))
        self._send_json(200, {"lines": list(LOG_BUFFER)[-limit:]})

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send_file("/index.html")
            elif path in ("/logs", "/logs/"):
                self._send_file("/logs.html")
            elif path == "/api/config":
                self._send_json(200, _public_state(BRIDGE))
            elif path == "/api/logs":
                self._send_logs()
            elif path == "/favicon.ico":
                self.send_response(204)
                self._send_cors()
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_file(path)
        except Exception as exc:  # noqa: BLE001 - 保证浏览器一定能收到响应
            log(f"GET 请求处理异常: {exc}")
            try:
                self._send_json(500, {"error": "服务器内部错误，请查看容器日志"})
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self):
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path != "/api/config":
                self._send_json(404, {"error": "Not Found"})
                return
            if _rate_limited(self.client_address[0]):
                self._send_json(429, {"error": "写入过于频繁，请稍后再试"})
                return
            payload = self._read_json_body()
            if payload is None:
                self._send_json(400, {"error": "请求体不是合法的 JSON 对象"})
                return
            _, error = _apply_save(payload, BRIDGE)
            if error:
                self._send_json(400, {"error": error})
                return
            _log_save_summary()
            self._send_json(200, _public_state(BRIDGE))
        except Exception as exc:  # noqa: BLE001 - 保证浏览器一定能收到响应
            log(f"保存请求处理异常: {exc}")
            try:
                self._send_json(500, {"error": "服务器内部错误，请查看容器日志"})
            except Exception:  # noqa: BLE001
                pass

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()


# ---------------------------------------------------------------------------
# 监督循环
# ---------------------------------------------------------------------------


BRIDGE = BridgeManager()
RESTART_DEBOUNCE_SECONDS = 1.2
CRASH_RESTART_DELAY_SECONDS = 3


def _endpoint_valid(cfg):
    endpoint = (cfg.get("MCP_ENDPOINT") or "").strip()
    return endpoint.startswith(("wss://", "ws://")) and len(endpoint) > 9


def control_loop(stop_event):
    running_fingerprint = None
    pending_change_since = None
    last_start_attempt = 0.0
    last_error_reported = ""

    while not stop_event.is_set():
        cfg = load_config()
        fingerprint = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
        desired = _endpoint_valid(cfg)
        alive = BRIDGE.alive

        if not desired:
            pending_change_since = None
            if alive:
                BRIDGE.stop("MCP 端点未配置或格式不正确")
            message = (
                "尚未配置 MCP 端点：请在配置页面填写小智 MCP 服务地址"
                if not cfg.get("MCP_ENDPOINT")
                else "MCP 端点格式不正确，请检查 ws:// 或 wss:// 前缀"
            )
            if message != last_error_reported:
                log(message)
                last_error_reported = message
            BRIDGE.set_error(message)
        elif not alive:
            pending_change_since = None
            now = time.time()
            if now - last_start_attempt >= CRASH_RESTART_DELAY_SECONDS:
                if BRIDGE.start():
                    last_start_attempt = now
                    running_fingerprint = fingerprint
            elif now - last_start_attempt >= 1 and last_error_reported:
                last_error_reported = ""
        else:
            if fingerprint != running_fingerprint:
                if pending_change_since is None:
                    pending_change_since = time.time()
                if time.time() - pending_change_since >= RESTART_DEBOUNCE_SECONDS:
                    log("检测到配置变化，正在重启 MCP 桥接进程")
                    BRIDGE.stop("配置已更新")
                    running_fingerprint = fingerprint
                    pending_change_since = None
            else:
                pending_change_since = None
            last_error_reported = ""
        stop_event.wait(1.0)


def _shutdown(signum, frame):
    log(f"收到信号 {signum}，正在退出")
    STOP_EVENT.set()


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log(f"配置页监听端口: {CONFIG_PORT}")
    log(f"配置文件: {CONFIG_PATH}")

    global STOP_EVENT
    STOP_EVENT = threading.Event()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    httpd = ThreadingHTTPServer(("0.0.0.0", CONFIG_PORT), ConfigHandler)
    httpd.daemon_threads = True
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    log("HTTP 配置服务已启动")

    try:
        control_loop(STOP_EVENT)
    finally:
        log("正在停止 HTTP 服务与 MCP 桥接进程")
        httpd.shutdown()
        BRIDGE.stop("服务退出")


STOP_EVENT = threading.Event()


if __name__ == "__main__":
    main()
