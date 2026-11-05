# -*- coding: utf-8 -*-
"""读取/保存“联网查询MCP”配置文件。

配置文件默认位于 $HOME/.xiaozhi_mcp_config.json（容器内 HOME=/config，
对应宿主机 @appdata/webvox-mcp/var）。旧版只含 MCP_ENDPOINT/ZHIPU_API_KEY
的配置也能自动兼容并补齐默认字段。
"""

import base64
import ctypes
import json
import os
import sys
import tempfile
from pathlib import Path


_HOME_DIR = Path(os.environ.get("HOME") or str(Path.home()))
CONFIG_PATH = Path(
    os.environ.get("XIAOZHI_MCP_CONFIG", str(_HOME_DIR / ".xiaozhi_mcp_config.json"))
)

DEFAULTS = {
    # 小智 MCP WebSocket 端点，例如 wss://api.xiaozhi.me/mcp/?token=...
    "MCP_ENDPOINT": "",
    # 联网搜索服务商：
    #   zhipu       智谱 GLM Web Search
    #   bailian-mcp 阿里百炼 MCP 广场“联网搜索 WebSearch”（需先开通，默认）
    #   deepseek    DeepSeek 官方联网搜索（api.deepseek.com/v1/tools/search）
    "PROVIDER": "bailian-mcp",
    "ZHIPU_API_KEY": "",
    "DASHSCOPE_API_KEY": "",
    "DASHSCOPE_MCP_URL": (
        "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    ),
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "RESULT_COUNT": 8,
    # 搜索返回粒度：brief（标题+摘要，适合语音设备）/ detail（带完整正文）
    "RESULT_MODE": "brief",
    # 一次搜索回传的总字符上限（机器人端 LLM / TTS 都吃不下长文本）
    "RESULT_MAX_CHARS": 4000,
    # 单次搜索的超时秒数（语音端等不了太久）
    "SEARCH_TIMEOUT": 15,
    # 网页抓取方式：auto（有 DeepSeek Key 就用官方 Fetch，失败回落本地）/ deepseek / local
    "FETCH_MODE": "auto",
    # fetch_url 返回正文的最大字符数
    "FETCH_MAX_CHARS": 6000,
    # 相同查询的结果缓存时长（秒），0=关闭缓存
    "CACHE_TTL": 60,
    # API Key 是否加密存储（Windows 走 DPAPI，其它平台自动回退明文+600 权限）
    "ENCRYPT_KEYS": True,
    # 桥接熔断：连续重连这么多次仍失败就停止自动重试（0=不限次数）
    "RECONNECT_MAX_ATTEMPTS": 20,
}

PROVIDERS = ("zhipu", "bailian-mcp", "deepseek")
FETCH_MODES = ("auto", "deepseek", "local")
RESULT_MODES = ("brief", "detail")

RESULT_MAX_CHARS_MIN = 500
RESULT_MAX_CHARS_MAX = 50000
SEARCH_TIMEOUT_MIN = 3
SEARCH_TIMEOUT_MAX = 60
CACHE_TTL_MAX = 3600

_LEGACY_PLACEHOLDERS = {"xxxxxxxxxxxxxxxxxxxxxx", "你的API密钥", "your-api-key"}

# ---------------------------------------------------------------------------
# API Key 加密存储
#   Windows：调用系统 DPAPI（CryptProtectData），按当前用户+机器绑定，
#            配置被拷到别的机器上也解不开，等同「不明文落盘」。
#   其它平台（NAS 容器）：容器里没有系统级密钥服务，退回 600 权限明文，
#            由 OS 文件权限保护；ENCRYPT_KEYS 开关此时不生效。
# ---------------------------------------------------------------------------
ENC_PREFIX = "enc:v1:"
SECRET_FIELDS = ("ZHIPU_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")


def dpapi_available():
    return sys.platform == "win32"


if dpapi_available():  # pragma: no cover - 平台相关

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_ulong),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    def _dpapi_call(protect, payload):
        blob_in = _DATA_BLOB(
            len(payload),
            ctypes.cast(
                ctypes.create_string_buffer(payload, len(payload)),
                ctypes.POINTER(ctypes.c_char),
            ),
        )
        blob_out = _DATA_BLOB()
        func = (
            ctypes.windll.crypt32.CryptProtectData
            if protect
            else ctypes.windll.crypt32.CryptUnprotectData
        )
        if not func(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            raise OSError("DPAPI 调用失败")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

else:

    def _dpapi_call(protect, payload):  # pragma: no cover - 非 Windows 兜底
        raise OSError("当前平台不支持 DPAPI")


def _encrypt_secret(text):
    if not text or text.startswith(ENC_PREFIX) or not dpapi_available():
        return text
    blob = _dpapi_call(True, text.encode("utf-8"))
    return ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def _decrypt_secret(text):
    if not text or not text.startswith(ENC_PREFIX):
        return text
    if not dpapi_available():
        # 换了平台（例如配置从 Windows 拷到 NAS）：无法解密，按未配置处理
        return ""
    try:
        raw = base64.b64decode(text[len(ENC_PREFIX):])
        return _dpapi_call(False, raw).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _clean_str(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in _LEGACY_PLACEHOLDERS else text


def load_config():
    """读取配置；文件缺失或损坏时返回默认值。"""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    if isinstance(data, dict):
        cfg["MCP_ENDPOINT"] = _clean_str(data.get("MCP_ENDPOINT"))
        provider = _clean_str(data.get("PROVIDER", cfg["PROVIDER"])).lower()
        # v1.5.0 及更早版本的 dashscope 原生搜索已下线，配置自动迁移到百炼 MCP
        if provider == "dashscope":
            provider = "bailian-mcp"
        cfg["PROVIDER"] = (
            provider if provider in PROVIDERS else "bailian-mcp"
        )
        cfg["ZHIPU_API_KEY"] = _clean_str(data.get("ZHIPU_API_KEY"))
        cfg["DASHSCOPE_API_KEY"] = _clean_str(data.get("DASHSCOPE_API_KEY"))
        cfg["DASHSCOPE_MCP_URL"] = _clean_str(
            data.get("DASHSCOPE_MCP_URL", cfg["DASHSCOPE_MCP_URL"])
        ) or cfg["DASHSCOPE_MCP_URL"]
        cfg["DEEPSEEK_API_KEY"] = _clean_str(data.get("DEEPSEEK_API_KEY"))
        cfg["DEEPSEEK_BASE_URL"] = (
            _clean_str(data.get("DEEPSEEK_BASE_URL")) or cfg["DEEPSEEK_BASE_URL"]
        ).rstrip("/")
        try:
            cfg["RESULT_COUNT"] = max(1, min(50, int(data.get("RESULT_COUNT", 8))))
        except (TypeError, ValueError):
            cfg["RESULT_COUNT"] = 8
        fetch_mode = _clean_str(data.get("FETCH_MODE", cfg["FETCH_MODE"])).lower()
        cfg["FETCH_MODE"] = (
            fetch_mode if fetch_mode in FETCH_MODES else cfg["FETCH_MODE"]
        )
        try:
            cfg["FETCH_MAX_CHARS"] = max(
                500, min(20000, int(data.get("FETCH_MAX_CHARS", 6000)))
            )
        except (TypeError, ValueError):
            cfg["FETCH_MAX_CHARS"] = 6000
        result_mode = _clean_str(
            data.get("RESULT_MODE", cfg["RESULT_MODE"])
        ).lower()
        cfg["RESULT_MODE"] = (
            result_mode if result_mode in RESULT_MODES else cfg["RESULT_MODE"]
        )
        try:
            cfg["RESULT_MAX_CHARS"] = max(
                RESULT_MAX_CHARS_MIN,
                min(
                    RESULT_MAX_CHARS_MAX,
                    int(data.get("RESULT_MAX_CHARS", 4000)),
                ),
            )
        except (TypeError, ValueError):
            cfg["RESULT_MAX_CHARS"] = 4000
        try:
            cfg["SEARCH_TIMEOUT"] = max(
                SEARCH_TIMEOUT_MIN,
                min(SEARCH_TIMEOUT_MAX, int(data.get("SEARCH_TIMEOUT", 15))),
            )
        except (TypeError, ValueError):
            cfg["SEARCH_TIMEOUT"] = 15
        try:
            cfg["CACHE_TTL"] = max(
                0, min(CACHE_TTL_MAX, int(data.get("CACHE_TTL", 60)))
            )
        except (TypeError, ValueError):
            cfg["CACHE_TTL"] = 60
        cfg["ENCRYPT_KEYS"] = bool(data.get("ENCRYPT_KEYS", True))
        try:
            cfg["RECONNECT_MAX_ATTEMPTS"] = max(
                0, min(1000, int(data.get("RECONNECT_MAX_ATTEMPTS", 20)))
            )
        except (TypeError, ValueError):
            cfg["RECONNECT_MAX_ATTEMPTS"] = 20
        for field in SECRET_FIELDS:
            cfg[field] = _decrypt_secret(_clean_str(data.get(field)))
    return cfg


def save_config(cfg):
    """原子写入配置，权限 600；Windows 上 API Key 走 DPAPI 加密。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: cfg.get(key, DEFAULTS.get(key, "")) for key in DEFAULTS}
    if cfg.get("ENCRYPT_KEYS", True) and dpapi_available():
        for field in SECRET_FIELDS:
            try:
                payload[field] = _encrypt_secret(payload.get(field) or "")
            except Exception:
                # 加密失败不阻断保存，退回明文（权限仍是 600）
                payload[field] = cfg.get(field, "")
    fd, tmp_path = tempfile.mkstemp(
        dir=str(CONFIG_PATH.parent), prefix=".xiaozhi-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "print":
        print(json.dumps(load_config(), ensure_ascii=False, indent=2))
    else:
        print("配置文件路径:", CONFIG_PATH)
        print("当前配置:", json.dumps(load_config(), ensure_ascii=False, indent=2))
