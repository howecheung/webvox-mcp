#!/bin/bash
# 容器入口：启动配置页 + MCP 桥接监督服务。
# compose 已把 ${TRIM_PKGVAR} 挂载到 /config、把 FPK 自带代码挂载到 /app。
set -e

umask 077
export HOME=/config
export TZ=Asia/Shanghai
mkdir -p /config /config/logs
cd /app
exec python3 /app/service.py
