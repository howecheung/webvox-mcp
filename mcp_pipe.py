"""
This script is used to connect to the MCP server and pipe the input and output to the websocket endpoint.
Version: 0.1.0

Usage:

export MCP_ENDPOINT=<mcp_endpoint>
python mcp_pipe.py <mcp_script>

"""


from config_manager import CONFIG_PATH, load_config  # 新增导入
import os
config = load_config()
# 设置 MCP_ENDPOINT 环境变量
os.environ["MCP_ENDPOINT"] = config.get("MCP_ENDPOINT", "")


import asyncio
import json
import time
from pathlib import Path
import websockets
import subprocess
import logging
import signal
import sys
import random
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MCP_PIPE')

# Reconnection settings
INITIAL_BACKOFF = 1  # Initial wait time in seconds
MAX_BACKOFF = 600  # Maximum wait time in seconds
reconnect_attempt = 0
backoff = INITIAL_BACKOFF

# 熔断：连续失败到达上限后停止重试并退出，由 web_config 决定是否再来一轮。
# 0 表示不限次数（一直重试）。
RECONNECT_MAX_ATTEMPTS = int(config.get("RECONNECT_MAX_ATTEMPTS", 20) or 20)
GIVE_UP_EXIT_CODE = 78
STATE_FILENAME = "bridge_state.json"


def _state_path():
    return Path(str(CONFIG_PATH)).parent / STATE_FILENAME


def write_state(state, attempt=0, backoff_seconds=0.0, reason=""):
    """把桥接状态写到配置文件同目录，供配置页展示（失败不影响主流程）。"""
    payload = {
        "state": state,
        "attempt": attempt,
        "backoff": round(float(backoff_seconds), 1),
        "reason": reason,
        "pid": os.getpid(),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _state_path()
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


async def connect_with_retry(uri):
    """Connect to WebSocket server with retry mechanism"""
    global reconnect_attempt, backoff
    while True:  # Infinite reconnection
        try:
            if reconnect_attempt > 0:
                if RECONNECT_MAX_ATTEMPTS and reconnect_attempt > RECONNECT_MAX_ATTEMPTS:
                    reason = f"连续重连 {reconnect_attempt - 1} 次仍失败，已停止自动重试"
                    logger.error(reason)
                    write_state("gave_up", reconnect_attempt, backoff, reason)
                    # 用特定退出码告诉上层：不要再自动拉起，等人工触发
                    sys.exit(GIVE_UP_EXIT_CODE)
                wait_time = backoff * (1 + random.random() * 0.1)  # Add some random jitter
                logger.info(f"Waiting {wait_time:.2f} seconds before reconnection attempt {reconnect_attempt}...")
                write_state("reconnecting", reconnect_attempt, wait_time)
                await asyncio.sleep(wait_time)

            # Attempt to connect
            await connect_to_server(uri)

        except SystemExit:
            raise
        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"Connection closed (attempt: {reconnect_attempt}): {e}")            
            # Calculate wait time for next reconnection (exponential backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

async def connect_to_server(uri):
    """Connect to WebSocket server and establish bidirectional communication with `mcp_script`"""
    global reconnect_attempt, backoff
    try:
        logger.info(f"Connecting to WebSocket server...")
        async with websockets.connect(uri) as websocket:
            logger.info(f"Successfully connected to WebSocket server")
            
            # Reset reconnection counter if connection closes normally
            reconnect_attempt = 0
            backoff = INITIAL_BACKOFF
            
            # Start mcp_script process
            process = subprocess.Popen(
                [sys.executable, mcp_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',  # Add encoding parameter
                errors='replace'   # Handle decoding errors gracefully
            )
            logger.info(f"Started {mcp_script} process")
            
            # Create two tasks: read from WebSocket and write to process, read from process and write to WebSocket
            await asyncio.gather(
                pipe_websocket_to_process(websocket, process),
                pipe_process_to_websocket(process, websocket),
                pipe_process_stderr_to_terminal(process)
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"WebSocket connection closed: {e}")
        raise  # Re-throw exception to trigger reconnection
    except Exception as e:
        logger.error(f"Connection error: {e}")
        raise  # Re-throw exception
    finally:
        # Ensure the child process is properly terminated
        if 'process' in locals():
            logger.info(f"Terminating {mcp_script} process")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"{mcp_script} process terminated")

async def pipe_websocket_to_process(websocket, process):
    """Read data from WebSocket and write to process stdin"""
    try:
        while True:
            # Read message from WebSocket
            message = await websocket.recv()
            logger.debug(f"<< {message[:120]}...")
            
            # Write to process stdin (in text mode)
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            process.stdin.write(message + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"Error in WebSocket to process pipe: {e}")
        raise  # Re-throw exception to trigger reconnection
    finally:
        # Close process stdin
        if not process.stdin.closed:
            process.stdin.close()

async def pipe_process_to_websocket(process, websocket):
    """Read data from process stdout and send to WebSocket"""
    try:
        while True:
            # Read data from process stdout
            data = await asyncio.get_event_loop().run_in_executor(
                None, process.stdout.readline
            )
            
            if not data:
                logger.info("Process has ended output")
                break
                
            # GUI 日志只写本地，不能混进 MCP 协议流（否则小智端收到非法 JSON）
            if data.startswith("[GUI_LOG]"):
                logger.info(f"[GUI] {data[len('[GUI_LOG]'):].strip()}")
                continue
                
            logger.debug(f">> {data[:120]}...")
            await websocket.send(data)
    except Exception as e:
        logger.error(f"Error in process to WebSocket pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

async def pipe_process_stderr_to_terminal(process):
    """Read data from process stderr and print to terminal"""
    try:
        while True:
            # Read data from process stderr
            data = await asyncio.get_event_loop().run_in_executor(
                None, process.stderr.readline
            )
            
            if not data:  # If no data, the process may have ended
                logger.info("Process has ended stderr output")
                break
                
            # Print stderr data to terminal (in text mode, data is already a string)
            sys.stderr.write(data)
            sys.stderr.flush()
    except Exception as e:
        logger.error(f"Error in process stderr pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)

# 新增日志发送函数
def send_log_to_gui(message):
    print(f"[GUI_LOG]{message}")

if __name__ == "__main__":
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # mcp_script
    if len(sys.argv) < 2:
        logger.error("Usage: mcp_pipe.py <mcp_script>")
        sys.exit(1)
    
    mcp_script = sys.argv[1]
    
    # Get token from environment variable or command line arguments
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    if not endpoint_url:
        logger.error("Please set the `MCP_ENDPOINT` environment variable")
        sys.exit(1)
    
    # Start main loop
    try:
        asyncio.run(connect_with_retry(endpoint_url))
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")
