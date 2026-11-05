# -*- coding: utf-8 -*-
"""联网查询MCP 本地主服务（Windows / 容器通用）。

职责：
1. 在 8765 端口提供配置网页与 /api/config 读写接口（不把密钥回显给浏览器）；
2. 把配置持久化到 $HOME/.xiaozhi_mcp_config.json；
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

from config_manager import (  # noqa: E402
    CACHE_TTL_MAX,
    CONFIG_PATH,
    dpapi_available,
    load_config,
    save_config,
)


# 容器内默认就是 UTF-8；此设置便于在 Windows 本地调试时日志不乱码
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

CONFIG_PORT = int(os.environ.get("WEBVOX_PORT", "8765"))
# 日志目录：默认放在项目目录下（Windows 本地运行更方便，也避免写入用户根目录被拒）
LOG_DIR = Path(os.environ.get("WEBVOX_LOG_DIR") or (APP_DIR / "logs"))
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


def _terminate_process_tree(proc, pid):
    """跨平台终止桥接进程及其子进程。

    Windows 没有 os.killpg，改用 taskkill /T 终止整棵进程树；
    POSIX 仍使用进程组信号，保证 mcp_pipe 与其子进程一起退出。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        pass
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


BRIDGE_STATE_FILENAME = "bridge_state.json"
GIVE_UP_EXIT_CODE = 78


def bridge_state_path():
    return Path(str(CONFIG_PATH)).parent / BRIDGE_STATE_FILENAME


def _read_bridge_state():
    """读取 mcp_pipe 写的状态文件；读不到就返回空字典。"""
    path = bridge_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _clear_bridge_state():
    try:
        bridge_state_path().unlink(missing_ok=True)
    except OSError:
        pass


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
            _clear_bridge_state()
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
        _terminate_process_tree(proc, pid)
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
                "retry": _read_bridge_state(),
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
        "resultMode": cfg.get("RESULT_MODE", "brief"),
        "resultMaxChars": int(cfg.get("RESULT_MAX_CHARS", 4000)),
        "searchTimeout": int(cfg.get("SEARCH_TIMEOUT", 15)),
        "cacheTtl": int(cfg.get("CACHE_TTL", 60)),
        "reconnectMaxAttempts": int(cfg.get("RECONNECT_MAX_ATTEMPTS", 20)),
        "encryptKeys": bool(cfg.get("ENCRYPT_KEYS", True)),
        "encryptAvailable": dpapi_available(),
        "zhipuKeySet": bool(cfg.get("ZHIPU_API_KEY")),
        "dashscopeKeySet": bool(cfg.get("DASHSCOPE_API_KEY")),
        "deepseekKeySet": bool(cfg.get("DEEPSEEK_API_KEY")),
        "deepseekBaseUrl": cfg.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "fetchMode": cfg.get("FETCH_MODE", "auto"),
        "fetchMaxChars": int(cfg.get("FETCH_MAX_CHARS", 6000)),
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
        if provider not in ("zhipu", "bailian-mcp", "deepseek"):
            return None, "请选择有效的联网搜索服务商"
        new_cfg["PROVIDER"] = provider

    if "DASHSCOPE_MCP_URL" in payload:
        mcp_url = _clean_mcp_url(payload.get("DASHSCOPE_MCP_URL"))
        if not mcp_url:
            return None, "百炼 MCP 服务地址必须以 http:// 或 https:// 开头"
        new_cfg["DASHSCOPE_MCP_URL"] = mcp_url

    if "DEEPSEEK_BASE_URL" in payload:
        base_url = str(payload.get("DEEPSEEK_BASE_URL") or "").strip().rstrip("/")
        if not base_url:
            base_url = "https://api.deepseek.com"
        if not base_url.startswith(("http://", "https://")) or len(base_url) > 200:
            return None, "DeepSeek 接口地址必须以 http:// 或 https:// 开头"
        new_cfg["DEEPSEEK_BASE_URL"] = base_url

    if "RESULT_COUNT" in payload:
        try:
            new_cfg["RESULT_COUNT"] = max(1, min(50, int(payload.get("RESULT_COUNT"))))
        except (TypeError, ValueError):
            return None, "返回条数必须是 1-50 的整数"

    if "RESULT_MODE" in payload:
        result_mode = str(payload.get("RESULT_MODE") or "brief").strip().lower()
        if result_mode not in ("brief", "detail"):
            return None, "搜索返回粒度必须是 brief / detail"
        new_cfg["RESULT_MODE"] = result_mode

    if "RESULT_MAX_CHARS" in payload:
        try:
            new_cfg["RESULT_MAX_CHARS"] = max(
                500, min(50000, int(payload.get("RESULT_MAX_CHARS")))
            )
        except (TypeError, ValueError):
            return None, "回传总字符上限必须是 500-50000 的整数"

    if "SEARCH_TIMEOUT" in payload:
        try:
            new_cfg["SEARCH_TIMEOUT"] = max(
                3, min(60, int(payload.get("SEARCH_TIMEOUT")))
            )
        except (TypeError, ValueError):
            return None, "搜索超时必须是 3-60 的整数（秒）"

    if "FETCH_MODE" in payload:
        fetch_mode = str(payload.get("FETCH_MODE") or "auto").strip().lower()
        if fetch_mode not in ("auto", "deepseek", "local"):
            return None, "网页抓取方式必须是 auto / deepseek / local"
        new_cfg["FETCH_MODE"] = fetch_mode

    if "FETCH_MAX_CHARS" in payload:
        try:
            new_cfg["FETCH_MAX_CHARS"] = max(
                500, min(20000, int(payload.get("FETCH_MAX_CHARS")))
            )
        except (TypeError, ValueError):
            return None, "抓取正文长度必须是 500-20000 的整数"

    if "CACHE_TTL" in payload:
        try:
            new_cfg["CACHE_TTL"] = max(
                0, min(CACHE_TTL_MAX, int(payload.get("CACHE_TTL")))
            )
        except (TypeError, ValueError):
            return None, "缓存时长必须是 0-3600 的整数（秒，0 表示关闭）"

    if "ENCRYPT_KEYS" in payload:
        new_cfg["ENCRYPT_KEYS"] = bool(payload.get("ENCRYPT_KEYS"))

    if "RECONNECT_MAX_ATTEMPTS" in payload:
        try:
            new_cfg["RECONNECT_MAX_ATTEMPTS"] = max(
                0, min(1000, int(payload.get("RECONNECT_MAX_ATTEMPTS")))
            )
        except (TypeError, ValueError):
            return None, "自动重连次数上限必须是 0-1000 的整数（0 表示不限）"

    # 密钥：输入框留空表示保留原值；显式清除时删除
    if payload.get("CLEAR_ZHIPU_KEY") is True:
        new_cfg["ZHIPU_API_KEY"] = ""
    elif payload.get("ZHIPU_API_KEY"):
        new_cfg["ZHIPU_API_KEY"] = str(payload["ZHIPU_API_KEY"]).strip()

    if payload.get("CLEAR_DASHSCOPE_KEY") is True:
        new_cfg["DASHSCOPE_API_KEY"] = ""
    elif payload.get("DASHSCOPE_API_KEY"):
        new_cfg["DASHSCOPE_API_KEY"] = str(payload["DASHSCOPE_API_KEY"]).strip()

    if payload.get("CLEAR_DEEPSEEK_KEY") is True:
        new_cfg["DEEPSEEK_API_KEY"] = ""
    elif payload.get("DEEPSEEK_API_KEY"):
        new_cfg["DEEPSEEK_API_KEY"] = str(payload["DEEPSEEK_API_KEY"]).strip()

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
    elif provider == "deepseek":
        key_ready = bool(cfg.get("DEEPSEEK_API_KEY"))
    else:
        key_ready = bool(cfg.get("DASHSCOPE_API_KEY"))
    log(
        "配置已保存：服务商="
        + provider
        + "，API Key="
        + ("已配置" if key_ready else "未配置（当前服务商搜索会被拒绝）")
        + "，抓取方式="
        + cfg.get("FETCH_MODE", "auto")
        + "，返回粒度="
        + cfg.get("RESULT_MODE", "brief")
        + f"，预算={cfg.get('RESULT_MAX_CHARS', 4000)}字"
        + f"，超时={cfg.get('SEARCH_TIMEOUT', 15)}秒"
        + f"，缓存={cfg.get('CACHE_TTL', 60)}秒"
        + "，Key加密="
        + ("开" if cfg.get("ENCRYPT_KEYS", True) and dpapi_available() else "关")
    )


# ---------------------------------------------------------------------------
# 测试搜索 / 用量统计
# ---------------------------------------------------------------------------

DEEPSEEK_PRICE_PER_1000 = 5.0  # 元 / 1000 次成功请求


def _tool_module():
    """延迟导入 联网查询.py（模块名是中文，只能走 importlib）。"""
    try:
        import importlib

        return importlib.import_module("联网查询")
    except Exception as exc:  # noqa: BLE001
        log(f"加载搜索模块失败: {exc}")
        return None


def _run_test_search(query, count=5):
    """在配置页直接试搜一次，不经过小智端。"""
    module = _tool_module()
    if module is None:
        return {"ok": False, "error": "无法加载搜索模块"}
    func = getattr(module, "联网查询", None)
    if func is None:
        return {"ok": False, "error": "搜索模块里没有联网查询工具"}
    func = getattr(func, "fn", func)  # FastMCP 会包装成 tool 对象
    started = time.perf_counter()
    try:
        result = func(query, int(count))
    except Exception as exc:  # noqa: BLE001 - 页面要拿到可读错误
        return {"ok": False, "error": f"测试搜索异常: {exc}"}
    elapsed = int((time.perf_counter() - started) * 1000)
    if not isinstance(result, dict):
        return {"ok": False, "error": "搜索返回格式异常", "ms": elapsed}
    if not result.get("success"):
        return {"ok": False, "error": result.get("error") or "搜索失败", "ms": elapsed}
    titles = [
        str(item.get("title") or "") for item in (result.get("results") or [])[:5]
    ]
    return {
        "ok": True,
        "provider": result.get("provider"),
        "mode": result.get("mode"),
        "count": result.get("count", 0),
        "cached": bool(result.get("cached")),
        "truncated": bool(result.get("truncated")),
        "ms": elapsed,
        "titles": titles,
    }


def _usage_stats():
    """读取 usage.jsonl，汇总今天的调用量与 DeepSeek 预估费用。"""
    module = _tool_module()
    path = None
    if module is not None:
        try:
            path = module.usage_file_path()
        except Exception:  # noqa: BLE001
            path = None
    stats = {
        "date": time.strftime("%Y-%m-%d"),
        "calls": 0,
        "cached": 0,
        "failed": 0,
        "items": 0,
        "byProvider": {},
        "deepseekEstimatedCost": 0.0,
        "deepseekBillable": 0,
        "enabled": dpapi_available(),  # 仅表示加密能力，与缓存无关
        "file": str(path) if path else "",
    }
    if path is None or not os.path.exists(path):
        return stats

    today = stats["date"]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not str(rec.get("ts", "")).startswith(today):
                    continue
                provider = str(rec.get("provider") or "?")
                count = int(rec.get("count") or 0)
                cached = bool(rec.get("cached"))
                stats["calls"] += 1
                if cached:
                    stats["cached"] += 1
                if count <= 0:
                    stats["failed"] += 1
                else:
                    stats["items"] += count
                bucket = stats["byProvider"].setdefault(
                    provider, {"calls": 0, "cached": 0, "items": 0}
                )
                bucket["calls"] += 1
                if cached:
                    bucket["cached"] += 1
                bucket["items"] += count
                # 计费口径：DeepSeek 只对「成功返回结果且未命中缓存」的请求计费
                if provider == "deepseek" and not cached and count > 0:
                    stats["deepseekBillable"] += 1
    except OSError as exc:
        log(f"读取用量统计失败: {exc}")
        return stats

    stats["deepseekEstimatedCost"] = round(
        stats["deepseekBillable"] * DEEPSEEK_PRICE_PER_1000 / 1000.0, 4
    )
    return stats


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
            elif path == "/api/usage":
                self._send_json(200, _usage_stats())
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
            if path == "/api/reconnect":
                if _rate_limited(self.client_address[0]):
                    self._send_json(429, {"error": "操作过于频繁，请稍后再试"})
                    return
                _clear_bridge_state()
                FORCE_RECONNECT.set()
                log("配置页请求手动重连")
                self._send_json(200, _public_state(BRIDGE))
                return
            if path == "/api/test-search":
                if _rate_limited(self.client_address[0]):
                    self._send_json(429, {"error": "操作过于频繁，请稍后再试"})
                    return
                payload = self._read_json_body()
                if payload is None:
                    self._send_json(400, {"error": "请求体不是合法的 JSON 对象"})
                    return
                query = str(payload.get("query") or "").strip()
                if not query:
                    self._send_json(400, {"error": "请先填写要测试的搜索词"})
                    return
                try:
                    count = int(payload.get("count") or 5)
                except (TypeError, ValueError):
                    count = 5
                count = max(1, min(10, count))
                self._send_json(200, _run_test_search(query, count))
                return
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
FORCE_RECONNECT = threading.Event()


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
            state = _read_bridge_state()
            gave_up = state.get("state") == "gave_up"
            if FORCE_RECONNECT.is_set():
                FORCE_RECONNECT.clear()
                gave_up = False
                _clear_bridge_state()
                last_start_attempt = 0.0
                log("收到手动重连请求，准备重新拉起桥接进程")
            elif gave_up and running_fingerprint is not None and fingerprint != running_fingerprint:
                gave_up = False
                _clear_bridge_state()
                log("配置已更新，解除熔断并重新连接")
            if gave_up:
                reason = state.get("reason") or "连续重连失败"
                message = f"已停止自动重连（{reason}），可点“重新连接”恢复"
                if message != last_error_reported:
                    log(message)
                    last_error_reported = message
                BRIDGE.set_error(message)
                stop_event.wait(1.0)
                continue
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

    # 容器（fnOS / NAS）里必须监听 0.0.0.0 才能被端口映射访问；
    # Windows 本地默认只听回环，避免把配置页暴露到局域网。
    default_host = "0.0.0.0" if os.name == "posix" else "127.0.0.1"
    bind_host = os.environ.get("WEBVOX_HOST", default_host)
    httpd = ThreadingHTTPServer((bind_host, CONFIG_PORT), ConfigHandler)
    httpd.daemon_threads = True
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    log(f"HTTP 配置服务已启动: http://{bind_host}:{CONFIG_PORT}")

    try:
        control_loop(STOP_EVENT)
    finally:
        log("正在停止 HTTP 服务与 MCP 桥接进程")
        httpd.shutdown()
        BRIDGE.stop("服务退出")


STOP_EVENT = threading.Event()


if __name__ == "__main__":
    main()
