# -*- coding: utf-8 -*-
"""读取/保存“小智联网查询MCP”配置文件。

配置文件默认位于 $HOME/.xiaozhi_mcp_config.json（容器内 HOME=/config，
对应宿主机 @appdata/webvox-mcp/var）。旧版只含 MCP_ENDPOINT/ZHIPU_API_KEY
的配置也能自动兼容并补齐默认字段。
"""

import json
import os
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
    "PROVIDER": "bailian-mcp",
    "ZHIPU_API_KEY": "",
    "DASHSCOPE_API_KEY": "",
    "DASHSCOPE_MCP_URL": (
        "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
    ),
    "RESULT_COUNT": 8,
}

_LEGACY_PLACEHOLDERS = {"xxxxxxxxxxxxxxxxxxxxxx", "你的API密钥", "your-api-key"}


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
            provider if provider in ("zhipu", "bailian-mcp") else "bailian-mcp"
        )
        cfg["ZHIPU_API_KEY"] = _clean_str(data.get("ZHIPU_API_KEY"))
        cfg["DASHSCOPE_API_KEY"] = _clean_str(data.get("DASHSCOPE_API_KEY"))
        cfg["DASHSCOPE_MCP_URL"] = _clean_str(
            data.get("DASHSCOPE_MCP_URL", cfg["DASHSCOPE_MCP_URL"])
        ) or cfg["DASHSCOPE_MCP_URL"]
        try:
            cfg["RESULT_COUNT"] = max(1, min(50, int(data.get("RESULT_COUNT", 8))))
        except (TypeError, ValueError):
            cfg["RESULT_COUNT"] = 8
    return cfg


def save_config(cfg):
    """原子写入配置，权限 600。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: cfg.get(key, DEFAULTS.get(key, "")) for key in DEFAULTS}
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
