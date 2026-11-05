#!/bin/sh
# 容器入口：启动配置页 + MCP 桥接监督服务。
# compose 已把 ${TRIM_PKGVAR} 挂载到 /config、把 FPK 自带代码挂载到 /app。
set -e

umask 077
export HOME=/config
export TZ=Asia/Shanghai
mkdir -p /config /config/logs
cd /app

# 依赖自检：缺什么就打一条警告到应用日志，避免只看到容器反复重启却不知原因。
python3 - <<'PY' || true
import importlib
mods = ("requests", "bs4", "zhipuai", "websockets", "dotenv", "mcp")
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        missing.append(f"{name}({exc.__class__.__name__})")
print("[deps] 缺失依赖: " + (", ".join(missing) if missing else "无"), flush=True)
PY

exec python3 /app/service.py
