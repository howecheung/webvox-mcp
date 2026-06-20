#!/bin/bash
set -e

CONFIG_FILE="$HOME/.xiaozhi_mcp_config.json"

# 从环境变量写入配置
if [ -n "$MCP_ENDPOINT" ] && [ -n "$ZHIPU_API_KEY" ]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    cat > "$CONFIG_FILE" << JSONEOF
{
  "MCP_ENDPOINT": "$MCP_ENDPOINT",
  "ZHIPU_API_KEY": "$ZHIPU_API_KEY"
}
JSONEOF
    echo "[entrypoint] 配置已写入 $CONFIG_FILE"
else
    echo "[entrypoint] ⚠ MCP_ENDPOINT 或 ZHIPU_API_KEY 未设置，将尝试使用已有配置文件"
fi

# 启动 MCP 管道服务 (mcp_pipe 会自动拉起 联网查询)
echo "[entrypoint] 启动 WebVox-MCP 服务..."
exec python /app/mcp_pipe.py /app/联网查询.py
