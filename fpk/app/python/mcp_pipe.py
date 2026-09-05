# -*- coding: utf-8 -*-
"""把小智 MCP WebSocket 与本地 stdio MCP 服务连接起来。

用法：python3 mcp_pipe.py /app/联网查询.py
由 service.py 负责在配置变化或进程退出时重启本脚本。
"""

import asyncio
import logging
import os
import random
import signal
import subprocess
import sys
import time


APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config_manager import load_config  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("MCP_PIPE")

INITIAL_BACKOFF = 2
MAX_BACKOFF = 300


async def _pipe_ws_to_process(websocket, proc):
    try:
        while True:
            message = await websocket.recv()
            if isinstance(message, bytes):
                message = message.decode("utf-8", "replace")
            logger.debug("WS -> MCP: %s", message[:200])
            proc.stdin.write(message + "\n")
            proc.stdin.flush()
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()


async def _pipe_process_to_ws(proc, websocket):
    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(
                None, proc.stdout.readline
            )
            if not line:
                logger.info("MCP 子进程 stdout 已结束")
                break
            logger.debug("MCP -> WS: %s", line[:200])
            await websocket.send(line)
    finally:
        pass


async def _drain_stderr(proc):
    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(
                None, proc.stderr.readline
            )
            if not line:
                break
            sys.stderr.write(line)
            sys.stderr.flush()
    finally:
        pass


async def _watch_child(proc):
    await asyncio.get_event_loop().run_in_executor(None, proc.wait)


def _terminate_child(proc):
    if proc is None or proc.poll() is not None:
        return
    logger.info("正在终止 MCP 子进程")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


async def connect_to_server(uri, mcp_script):
    child = None
    try:
        logger.info("正在连接 WebSocket 服务器: %s", _redact_endpoint(uri))
        async with __import__("websockets").connect(uri) as websocket:
            logger.info("Successfully connected to WebSocket server")
            child = subprocess.Popen(
                [sys.executable, mcp_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=APP_DIR,
                env=dict(os.environ),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            logger.info("已启动 %s（pid=%s）", mcp_script, child.pid)
            tasks = {
                asyncio.create_task(_pipe_ws_to_process(websocket, child)),
                asyncio.create_task(_pipe_process_to_ws(child, websocket)),
                asyncio.create_task(_drain_stderr(child)),
                asyncio.create_task(_watch_child(child)),
            }
            # 任一通道结束或子进程退出时，关闭整条链路让上层重连
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            logger.info("MCP 管道链路结束（子进程退出或连接关闭）")
    finally:
        _terminate_child(child)


def _redact_endpoint(uri):
    """日志中隐藏查询串里的 token，避免泄露凭据。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(uri)
        if not parts.query:
            return uri
        redacted = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in ("token", "key", "apikey", "api_key", "access_token"):
                value = "***" if value else ""
            redacted.append((key, value))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(redacted), parts.fragment)
        )
    except Exception:
        return "<endpoint>"


async def connect_with_retry(uri, mcp_script):
    attempt = 0
    backoff = INITIAL_BACKOFF
    while True:
        try:
            if attempt:
                jittered = backoff * (1 + random.random() * 0.1)
                logger.info(
                    "第 %s 次重连，等待 %.1f 秒...", attempt, jittered
                )
                await asyncio.sleep(jittered)
            await connect_to_server(uri, mcp_script)
            attempt = 0
            backoff = INITIAL_BACKOFF
        except Exception as exc:  # noqa: BLE001 - 需无限重连
            attempt += 1
            logger.warning("连接已断开（第 %s 次）: %s", attempt, exc)
            backoff = min(backoff * 2, MAX_BACKOFF)


def _handle_term(signum, frame):
    logger.info("收到退出信号 %s，mcp_pipe 退出", signum)
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    if len(sys.argv) < 2:
        logger.error("用法: mcp_pipe.py <mcp_script>")
        return 1
    mcp_script = sys.argv[1]
    if not os.path.isabs(mcp_script):
        mcp_script = os.path.join(APP_DIR, mcp_script)

    config = load_config()
    endpoint = config.get("MCP_ENDPOINT", "")
    os.environ.setdefault("MCP_ENDPOINT", endpoint)
    if not endpoint or not endpoint.startswith(("ws://", "wss://")):
        logger.error("MCP_ENDPOINT 为空或格式不正确，等待配置页填写后由 service.py 重启")
        return 1

    logger.info("MCP 端点已读取（令牌已隐藏）")
    try:
        asyncio.run(connect_with_retry(endpoint, mcp_script))
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as exc:  # noqa: BLE001
        logger.error("运行出错: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
