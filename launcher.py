"""
WebVox-MCP 启动器 (PyInstaller 打包入口)
- 无参数启动: 打开 GUI 配置面板 (启动_main.py)
- 带 .py 参数启动: 在子进程模式下运行指定脚本
- 自动修补 subprocess.Popen，将 "python" 替换为 sys.executable
- 修复 Windows asyncio 兼容性
"""
import sys
import os
import subprocess
import runpy

# === 显式导入被 runpy 间接使用的模块 (确保 PyInstaller 打包) ===
import tkinter
import tkinter.ttk
import tkinter.messagebox
import tkinter.scrolledtext
import asyncio
import websockets
import zhipuai
import requests
import bs4
import dotenv
import mcp
import mcp.server.fastmcp

# === Windows asyncio 兼容修复 ===
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# === 获取脚本所在目录 ===
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后: sys._MEIPASS 是解压目录
    BUNDLE_DIR = sys._MEIPASS
else:
    # 开发模式
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

# === 修补 subprocess.Popen ===
# 原始代码使用 subprocess.Popen(["python", ...]) 启动子进程
# 打包后没有独立的 python 解释器，需要改为用 sys.executable (即 exe 自身)
_original_popen = subprocess.Popen

def _patched_popen(args, *p_args, **kwargs):
    if isinstance(args, list) and len(args) > 0:
        exe = args[0]
        if exe in ("python", "python3", "python3.exe", "python.exe"):
            args = [sys.executable] + args[1:]
    elif isinstance(args, str) and args.startswith("python"):
        args = args.replace("python", sys.executable, 1)
    return _original_popen(args, *p_args, **kwargs)

subprocess.Popen = _patched_popen

# === 分发模式 ===
if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
    # --- 子进程模式: 运行指定的 .py 脚本 ---
    script_to_run = sys.argv[1]
    if not os.path.isabs(script_to_run):
        script_to_run = os.path.join(BUNDLE_DIR, script_to_run)
    # 重新设置 sys.argv 为脚本的参数
    sys.argv = [script_to_run] + sys.argv[2:]
    runpy.run_path(script_to_run, run_name='__main__')
else:
    # --- GUI 模式: 启动配置面板 ---
    gui_script = os.path.join(BUNDLE_DIR, '启动_main.py')
    runpy.run_path(gui_script, run_name='__main__')
