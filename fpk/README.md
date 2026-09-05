# 联网查询MCP（WebVox-MCP）fnOS FPK 打包说明（v1.6.0）

本目录把 [howecheung/webvox-mcp](https://github.com/howecheung/webvox-mcp)
封装成飞牛 fnOS `.fpk` 应用（显示名“联网查询MCP”）。

> 版本区分：本目录是 **v1.6.0** 的完整可用的 fnOS FPK 工程，是对仓库历史中
> 曾被移除的旧 `fpk/`（标注 abandoned、安装即报 env file not found）的完全重写。
> 新包采用 Docker Compose + fnOS 内嵌配置页/日志页，并内置生命周期脚本与
> `webvox-mcp-1.6.0.fpk` 构建产物。

## 形态

- **运行方式**：Docker Compose，使用上游官方多架构镜像
  `ghcr.io/howecheung/webvox-mcp`（按打包时 OCI 清单摘要固定），`platform=all`。
- **安装后配置**：桌面只出现一个“联网查询MCP”图标，点击后以 fnOS 系统页面
  内嵌打开（`iframe` 方式）。页面顶部菜单在“配置 / 运行日志”之间切换，
  不再生成第二个桌面应用图标。
- **可选服务商**：
  - 阿里百炼 WebSearch MCP（默认，推荐）：连接百炼 MCP 广场的
    `https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp`。
    需要在百炼控制台 MCP 广场先开通并把协议升级为 Streamable HTTP
    （地址应为 `/mcp` 结尾，不是旧的 `/sse`）。前 2000 次调用免费，
    用尽后约 29 元/千次，只收取检索费用，不经过阿里侧模型；
  - 智谱 GLM：走 `ZhipuAI.web_search`（open.bigmodel.cn 的 Web Search API）。

配置内容持久化在 `${TRIM_PKGVAR}/.xiaozhi_mcp_config.json`
（容器内为 `/config/.xiaozhi_mcp_config.json`），重新安装同名应用时继续沿用。

## 目录说明

| 路径 | 说明 |
| --- | --- |
| `manifest` | 应用元数据（显示名、端口、桌面入口、changelog 等） |
| `config/resource` | 声明 Docker Compose 项目 `webvox-mcp` |
| `app/docker/docker-compose.yaml` | Compose 编排；把 `var` 挂到 `/config`、把 `python/` 挂到 `/app` |
| `app/ui/config` + `app/ui/images` | fnOS 桌面入口（iframe）：仅“联网查询MCP”一个入口，指向 `/` |
| `app/python/` | 覆盖进容器的业务代码与配置页服务 |
| `cmd/` | 生命周期脚本（Docker 应用启停由应用中心按 compose 管理） |
| `wizard/install` | 安装说明（无表单字段） |
| `webvox-mcp-1.6.0.fpk` | v1.6.0 构建产物（同时发布到 GitHub Releases） |

### app/python 内部结构

| 文件 | 说明 |
| --- | --- |
| `service.py` | 配置页 HTTP 服务（8765）+ 桥接进程监督；配置保存后自动重启桥接 |
| `www/index.html` | 配置页：MCP 端点、服务商卡片二选一、密钥、返回条数（含精简说明） |
| `www/logs.html` | 运行日志页（供页内“运行日志”菜单与 `/logs` 直接访问，3 秒自动刷新） |
| `service.py` | 同时提供配置接口 `/api/config` 与日志接口 `/api/logs` |
| `mcp_pipe.py` | 小智 MCP WebSocket ↔ 本地 stdio MCP 双向管道（自动重连） |
| `联网查询.py` | 对外暴露的 `联网查询` MCP 工具，按服务商分发 |
| `config_manager.py` | 读写 `/config/.xiaozhi_mcp_config.json` |
| `start.sh` | 容器入口，exec 到 `service.py` |

## 安全设计

- API Key / 端点中的 `token` 等查询参数**不向页面明文回显**，也不写日志；
- 页面把已保存密钥显示为“已设置”，留空保存不会覆盖原值，可勾选清除；
- 配置写入接口按来源 IP 做了简单限流，且服务端对静态文件做了路径穿越校验。

> 注意：配置页是直连 HTTP 服务（端口 8765），桌面图标默认仅管理员可见。
> 请只在可信局域网内使用；不要在公网直接暴露该端口。

## 重新打包

Windows 下使用官方 fnpack：

```powershell
.\build-fpk.ps1
```

产物输出到 `dist\webvox-mcp.fpk`，脚本会自动再复制一份带版本号的
`webvox-mcp-1.6.0.fpk` 到本目录。也可以直接：

```bash
fnpack build --directory .
```

> fnpack 为飞牛官方打包 CLI，可放入 PATH，或通过 `FNPACK` 环境变量指定路径。

## 安装与使用

1. 飞牛桌面 → 应用中心 → 手动安装，选择 `webvox-mcp-1.6.0.fpk`（或 `dist\webvox-mcp.fpk`）。
2. 首次启动会拉取固定版本的 `ghcr.io/howecheung/webvox-mcp` 镜像，需 NAS 能访问外网。
3. 点击桌面“联网查询MCP”图标（默认停在配置页）：
   - 填写小智 MCP 端点（`wss://api.xiaozhi.me/mcp/?token=...`）；
   - 页面顶部菜单可切到“运行日志”查看连接状态；
   - 默认选中“阿里百炼 WebSearch MCP”，已开通则填百炼 `sk-` API Key，
     服务地址保持默认即可；改用智谱时切换到“智谱 GLM”并填智谱 API Key；
   - 保存后页面会自动重启 MCP 桥接进程并重连。

## 保存按钮提示“Failed to fetch”排查

配置页读取正常但点保存提示 `Failed to fetch`，通常是浏览器到
`http://<NAS IP>:8765/api/config` 的 POST 被网络层拦截或连接中断，而不是配置内容错误：

1. 换个方式直接访问 `http://<NAS IP>:8765`（不要从带 HTTPS 的桌面/网关入口进入），
   再点保存。
2. 打开浏览器开发者工具 → Network，确认请求是否发到 8765、错误是 CORS/混合内容
   还是连接被重置。
3. 在 fnOS SSH 终端执行 `docker logs --tail 100 webvox-mcp`，看保存瞬间服务端日志；
   现在服务端会在异常时返回 JSON 错误而不是让请求直接断开。
4. 确认 8765 端口没有同时被其他程序占用；应用中心里先停止再启动一次应用。

服务端已对 `/api/config` 返回 CORS 与私有网络预检响应头，并给 POST 加了
20 秒超时提示，方便区分“服务不可达”和“请求被拦截”。

## 注意

- `appname` 固定为 `webvox-mcp`，不要改名（同名升级/重装会沿用配置）。
- 卸载时默认保留 `var` 中的配置。
- 不要绕过应用中心直接删除/重建 `webvox-mcp` 容器。
- 修改 `app/python` 里的任何代码后，需要重新 `fnpack build`。
