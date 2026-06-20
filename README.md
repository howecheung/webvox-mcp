# WebVox-MCP 🎙️🔍

语音机器人的联网搜索 MCP 服务 —— 让 StackChan / 小智 等语音助手拥有实时联网检索能力。

## 架构

```
语音机器人 → wss://api.xiaozhi.me → mcp_pipe.py → 联网查询.py (MCP Server) → 智谱AI 搜索
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

打开 GUI 配置面板：

```bash
python 启动_main.py
```

填入：
- **MCP端点** — 小智平台控制台获取的 WebSocket 地址
- **智谱API密钥** — [open.bigmodel.cn](https://open.bigmodel.cn) 获取

> 配置保存在 `~/.xiaozhi_mcp_config.json`，不会被提交到 Git。

### 3. 启动服务

点击"启动服务"，或在命令行：

```bash
python mcp_pipe.py 联网查询.py
```

## MCP 工具

### `联网查询`

```
参数:
  query_text             - 搜索关键词
  count (可选, 默认8)     - 返回条数 (1-50)
  search_domain_filter   - 限定域名，空=全网
  search_recency_filter  - 时间过滤：noLimit / week / month / year

返回:
  {"success": true, "results": [{"title": "...", "content": "...", "url": "..."}]}
```

## 项目来源与致谢

本项目基于小智AI团队的 MCP 服务教程，深表感谢 🙏

📖 参考文档：[小智AI · MCP服务接入指南](https://my.feishu.cn/docx/JKFXd8bLYo6YZtxz9ORcbnA8nbe)

## 项目结构

```
├── 联网查询.py          # MCP Server — 注册联网搜索工具
├── mcp_pipe.py          # WebSocket 管道 — 连接远程服务器，自动重连
├── 启动_main.py          # GUI 配置面板 (tkinter)
├── config_manager.py    # 配置读写 (~/.xiaozhi_mcp_config.json)
├── requirements.txt     # Python 依赖
└── .env.example         # 环境变量模板
```
