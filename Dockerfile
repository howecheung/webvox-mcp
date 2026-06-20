FROM python:3.13-slim

WORKDIR /app

# 安装系统依赖 (无需额外编译库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用文件
COPY mcp_pipe.py .
COPY 联网查询.py .
COPY config_manager.py .

# 复制启动脚本
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 不需要暴露端口 (WebSocket 客户端，对外连接)
ENTRYPOINT ["docker-entrypoint.sh"]
