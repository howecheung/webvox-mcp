# -*- coding: utf-8 -*-
"""小智联网查询 MCP —— 本地图形配置界面（Windows / tkinter）。

三个搜索服务商可切换：智谱、百炼 MCP 广场、DeepSeek 官方联网搜索。
与 Web 配置页（web_config.py）共用同一份配置文件，保存时会保留未在界面上修改的字段。
"""

import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from config_manager import load_config, save_config

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get(
    "XIAOZHI_MCP_CONFIG",
    os.path.join(os.path.expanduser("~"), ".xiaozhi_mcp_config.json"),
)
WEB_PAGE_URL = "http://127.0.0.1:%s" % os.environ.get("WEBVOX_PORT", "8765")

PROVIDER_LABELS = {
    "zhipu": "智谱 GLM Web Search",
    "bailian-mcp": "阿里百炼 MCP 广场 · 联网搜索",
    "deepseek": "DeepSeek 官方联网搜索",
}

FETCH_MODE_LABELS = {
    "auto": "自动（优先 DeepSeek，失败回落本地）",
    "deepseek": "仅 DeepSeek 官方抓取接口",
    "local": "仅本地抓取（免费）",
}

RESULT_MODE_LABELS = {
    "brief": "简要（标题 + 摘要，推荐语音设备）",
    "detail": "详细（带完整正文，耗 token）",
}


class MCPConfigApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("小智联网查询 MCP 配置")
        self.config = load_config()
        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 界面 ----------
    def create_widgets(self):
        frame = ttk.LabelFrame(self.root, text="API 配置")
        frame.pack(padx=10, pady=10, fill="x")

        ttk.Label(frame, text="MCP 端点:").grid(row=0, column=0, sticky="w")
        self.mcp_entry = ttk.Entry(frame, width=52)
        self.mcp_entry.insert(0, self.config.get("MCP_ENDPOINT", ""))
        self.mcp_entry.grid(row=0, column=1, padx=5, pady=2, sticky="we")
        ttk.Label(
            frame, text="小智 MCP WebSocket 地址（wss://…）", foreground="#666"
        ).grid(row=0, column=2, sticky="w")

        ttk.Label(frame, text="搜索服务商:").grid(row=1, column=0, sticky="w")
        self.provider_var = tk.StringVar()
        self.provider_box = ttk.Combobox(
            frame,
            width=48,
            state="readonly",
            textvariable=self.provider_var,
            values=[PROVIDER_LABELS[k] for k in ("zhipu", "bailian-mcp", "deepseek")],
        )
        self.provider_box.grid(row=1, column=1, padx=5, pady=2, sticky="we")
        self.provider_box.bind("<<ComboboxSelected>>", lambda _e: self.on_provider_change())
        saved_provider = self.config.get("PROVIDER", "bailian-mcp")
        self.provider_var.set(PROVIDER_LABELS.get(saved_provider, PROVIDER_LABELS["bailian-mcp"]))

        ttk.Label(frame, text="智谱 API 密钥:").grid(row=2, column=0, sticky="w")
        self.zhipu_entry = ttk.Entry(frame, width=52, show="*")
        self.zhipu_entry.insert(0, self.config.get("ZHIPU_API_KEY", ""))
        self.zhipu_entry.grid(row=2, column=1, padx=5, pady=2, sticky="we")

        ttk.Label(frame, text="百炼 API 密钥:").grid(row=3, column=0, sticky="w")
        self.dashscope_entry = ttk.Entry(frame, width=52, show="*")
        self.dashscope_entry.insert(0, self.config.get("DASHSCOPE_API_KEY", ""))
        self.dashscope_entry.grid(row=3, column=1, padx=5, pady=2, sticky="we")

        ttk.Label(frame, text="百炼 MCP 地址:").grid(row=4, column=0, sticky="w")
        self.dashscope_url_entry = ttk.Entry(frame, width=52)
        self.dashscope_url_entry.insert(0, self.config.get("DASHSCOPE_MCP_URL", ""))
        self.dashscope_url_entry.grid(row=4, column=1, padx=5, pady=2, sticky="we")

        ttk.Label(frame, text="DeepSeek API 密钥:").grid(row=5, column=0, sticky="w")
        self.deepseek_entry = ttk.Entry(frame, width=52, show="*")
        self.deepseek_entry.insert(0, self.config.get("DEEPSEEK_API_KEY", ""))
        self.deepseek_entry.grid(row=5, column=1, padx=5, pady=2, sticky="we")

        ttk.Label(frame, text="DeepSeek 接口地址:").grid(row=6, column=0, sticky="w")
        self.deepseek_url_entry = ttk.Entry(frame, width=52)
        self.deepseek_url_entry.insert(0, self.config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        self.deepseek_url_entry.grid(row=6, column=1, padx=5, pady=2, sticky="we")

        ttk.Label(frame, text="返回结果条数:").grid(row=7, column=0, sticky="w")
        self.count_entry = ttk.Entry(frame, width=10)
        self.count_entry.insert(0, str(self.config.get("RESULT_COUNT", 8)))
        self.count_entry.grid(row=7, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(frame, text="网页抓取方式:").grid(row=8, column=0, sticky="w")
        self.fetch_mode_var = tk.StringVar()
        self.fetch_mode_box = ttk.Combobox(
            frame,
            width=48,
            state="readonly",
            textvariable=self.fetch_mode_var,
            values=[FETCH_MODE_LABELS[k] for k in ("auto", "deepseek", "local")],
        )
        self.fetch_mode_box.grid(row=8, column=1, padx=5, pady=2, sticky="we")
        self.fetch_mode_var.set(
            FETCH_MODE_LABELS.get(
                self.config.get("FETCH_MODE", "auto"), FETCH_MODE_LABELS["auto"]
            )
        )

        ttk.Label(frame, text="抓取正文长度:").grid(row=9, column=0, sticky="w")
        self.fetch_chars_entry = ttk.Entry(frame, width=10)
        self.fetch_chars_entry.insert(0, str(self.config.get("FETCH_MAX_CHARS", 6000)))
        self.fetch_chars_entry.grid(row=9, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(frame, text="搜索返回粒度:").grid(row=10, column=0, sticky="w")
        self.result_mode_var = tk.StringVar()
        self.result_mode_box = ttk.Combobox(
            frame,
            width=48,
            state="readonly",
            textvariable=self.result_mode_var,
            values=[RESULT_MODE_LABELS[k] for k in ("brief", "detail")],
        )
        self.result_mode_box.grid(row=10, column=1, padx=5, pady=2, sticky="we")
        self.result_mode_var.set(
            RESULT_MODE_LABELS.get(
                self.config.get("RESULT_MODE", "brief"), RESULT_MODE_LABELS["brief"]
            )
        )

        ttk.Label(frame, text="回传字符上限:").grid(row=11, column=0, sticky="w")
        self.result_chars_entry = ttk.Entry(frame, width=10)
        self.result_chars_entry.insert(0, str(self.config.get("RESULT_MAX_CHARS", 4000)))
        self.result_chars_entry.grid(row=11, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(frame, text="搜索超时(秒):").grid(row=12, column=0, sticky="w")
        self.search_timeout_entry = ttk.Entry(frame, width=10)
        self.search_timeout_entry.insert(0, str(self.config.get("SEARCH_TIMEOUT", 15)))
        self.search_timeout_entry.grid(row=12, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(frame, text="缓存时长(秒):").grid(row=13, column=0, sticky="w")
        self.cache_ttl_entry = ttk.Entry(frame, width=10)
        self.cache_ttl_entry.insert(0, str(self.config.get("CACHE_TTL", 60)))
        self.cache_ttl_entry.grid(row=13, column=1, sticky="w", padx=5, pady=2)

        frame.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(pady=8)
        ttk.Button(btn_frame, text="保存配置", command=self.on_save).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="启动服务", command=self.on_start_service).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="打开 Web 配置页", command=self.on_open_web).pack(side="left", padx=5)

        log_frame = ttk.LabelFrame(self.root, text="操作日志")
        log_frame.pack(padx=10, pady=10, fill="both", expand=True)
        self.log_text = ScrolledText(log_frame, height=10, wrap=tk.WORD)
        self.log_text.pack(padx=5, pady=5, fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.on_provider_change()

    def current_provider(self):
        label = self.provider_var.get()
        for key, text in PROVIDER_LABELS.items():
            if text == label:
                return key
        return "bailian-mcp"

    def current_fetch_mode(self):
        label = self.fetch_mode_var.get()
        for key, text in FETCH_MODE_LABELS.items():
            if text == label:
                return key
        return "auto"

    def current_result_mode(self):
        label = self.result_mode_var.get()
        for key, text in RESULT_MODE_LABELS.items():
            if text == label:
                return key
        return "brief"

    def on_provider_change(self):
        """按当前服务商高亮对应输入框，其余置灰。"""
        provider = self.current_provider()
        self.zhipu_entry.configure(state="normal" if provider == "zhipu" else "disabled")
        self.dashscope_entry.configure(state="normal" if provider == "bailian-mcp" else "disabled")
        self.dashscope_url_entry.configure(state="normal" if provider == "bailian-mcp" else "disabled")
        self.deepseek_entry.configure(state="normal" if provider == "deepseek" else "disabled")
        self.deepseek_url_entry.configure(state="normal" if provider == "deepseek" else "disabled")
        self.log("当前服务商：%s" % PROVIDER_LABELS[provider])

    # ---------- 行为 ----------
    def on_save(self):
        endpoint = self.mcp_entry.get().strip()
        if endpoint and not endpoint.startswith(("ws://", "wss://")):
            messagebox.showerror("保存失败", "MCP 端点须以 ws:// 或 wss:// 开头")
            return
        try:
            count = int(self.count_entry.get().strip() or "8")
        except ValueError:
            messagebox.showerror("保存失败", "返回结果条数必须是整数")
            return
        if not 1 <= count <= 50:
            messagebox.showerror("保存失败", "返回结果条数需在 1-50 之间")
            return
        try:
            fetch_chars = int(self.fetch_chars_entry.get().strip() or "6000")
        except ValueError:
            messagebox.showerror("保存失败", "抓取正文长度必须是整数")
            return
        if not 500 <= fetch_chars <= 20000:
            messagebox.showerror("保存失败", "抓取正文长度需在 500-20000 之间")
            return
        try:
            result_chars = int(self.result_chars_entry.get().strip() or "4000")
        except ValueError:
            messagebox.showerror("保存失败", "回传字符上限必须是整数")
            return
        if not 500 <= result_chars <= 50000:
            messagebox.showerror("保存失败", "回传字符上限需在 500-50000 之间")
            return
        try:
            search_timeout = int(self.search_timeout_entry.get().strip() or "15")
        except ValueError:
            messagebox.showerror("保存失败", "搜索超时必须是整数")
            return
        if not 3 <= search_timeout <= 60:
            messagebox.showerror("保存失败", "搜索超时需在 3-60 秒之间")
            return
        try:
            cache_ttl = int(self.cache_ttl_entry.get().strip() or "60")
        except ValueError:
            messagebox.showerror("保存失败", "缓存时长必须是整数")
            return
        if not 0 <= cache_ttl <= 3600:
            messagebox.showerror("保存失败", "缓存时长需在 0-3600 秒之间（0 表示关闭）")
            return

        provider = self.current_provider()
        current = load_config()
        current.update(
            {
                "MCP_ENDPOINT": endpoint,
                "PROVIDER": provider,
                "ZHIPU_API_KEY": self.zhipu_entry.get().strip(),
                "DASHSCOPE_API_KEY": self.dashscope_entry.get().strip(),
                "DASHSCOPE_MCP_URL": self.dashscope_url_entry.get().strip(),
                "DEEPSEEK_API_KEY": self.deepseek_entry.get().strip(),
                "DEEPSEEK_BASE_URL": self.deepseek_url_entry.get().strip()
                or "https://api.deepseek.com",
                "RESULT_COUNT": count,
                "FETCH_MODE": self.current_fetch_mode(),
                "FETCH_MAX_CHARS": fetch_chars,
                "RESULT_MODE": self.current_result_mode(),
                "RESULT_MAX_CHARS": result_chars,
                "SEARCH_TIMEOUT": search_timeout,
                "CACHE_TTL": cache_ttl,
            }
        )
        try:
            save_config(current)
        except Exception as exc:  # 写盘失败（权限等）要明确告知
            self.log("配置保存失败：%s" % exc)
            messagebox.showerror("保存失败", str(exc))
            return
        self.config = current
        self.log(
            "配置已保存（服务商=%s，条数=%d，抓取=%s/%d，粒度=%s/%d字，超时=%d秒，缓存=%d秒）"
            % (
                provider,
                count,
                current["FETCH_MODE"],
                fetch_chars,
                current["RESULT_MODE"],
                result_chars,
                search_timeout,
                cache_ttl,
            )
        )
        messagebox.showinfo("保存成功", "配置已保存到\n%s" % CONFIG_PATH)

    def on_start_service(self):
        current_dir = APP_DIR
        mcp_script = os.path.join(current_dir, "mcp_pipe.py")
        web_search_script = os.path.join(current_dir, "联网查询.py")
        try:
            subprocess.Popen(
                [sys.executable, mcp_script, web_search_script],
                cwd=current_dir,
            )
        except Exception as exc:
            self.log("服务启动失败：%s" % exc)
            messagebox.showerror("启动失败", str(exc))
            return
        self.log("服务已启动（后台进程）")
        messagebox.showinfo("启动成功", "服务已在后台运行")

    def on_open_web(self):
        web_script = os.path.join(APP_DIR, "web_config.py")
        if not os.path.exists(web_script):
            messagebox.showerror("未找到 web_config.py", web_script)
            return
        threading.Thread(target=self._start_web_service, args=(web_script,), daemon=True).start()
        self.after(1200, lambda: webbrowser.open(WEB_PAGE_URL))

    def _start_web_service(self, web_script):
        try:
            subprocess.Popen(
                [sys.executable, web_script],
                cwd=APP_DIR,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.log("Web 配置服务已启动：%s" % WEB_PAGE_URL)
        except Exception as exc:
            self.log("Web 配置服务启动失败：%s" % exc)

    def on_close(self):
        self.root.destroy()

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see(tk.END)


if __name__ == "__main__":
    app = MCPConfigApp()
    app.root.mainloop()
