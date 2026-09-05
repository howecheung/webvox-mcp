# -*- coding: utf-8 -*-
"""联网查询 MCP 工具：智谱 GLM Web Search 或 阿里百炼 WebSearch MCP。

通过配置页选择服务商；所有服务商对外暴露同一个“联网查询”工具，签名与
上游 webvox-mcp 保持一致：

- zhipu       智谱 GLM Web Search
- bailian-mcp 阿里百炼 MCP 广场“联网搜索”WebSearch（需在控制台先开通）
"""

import logging
import os
import sys
from typing import Optional


APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from config_manager import load_config  # noqa: E402


logger = logging.getLogger(__name__)

# 兼容 Windows 控制台 UTF-8（容器内通常无需，保留无副作用）
if sys.platform == "win32":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

mcp = FastMCP("mcps")

DASHSCOPE_MCP_DEFAULT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
)


def _clamp_count(count):
    try:
        return max(1, min(50, int(count)))
    except (TypeError, ValueError):
        return 8


def _zhipu_search(config, query_text, count, domain, recency):
    """调用智谱 Web Search，返回与上游一致的 title/content/url 结果。"""
    from zhipuai import ZhipuAI

    api_key = config.get("ZHIPU_API_KEY", "")
    if not api_key:
        return {"success": False, "error": "智谱 API Key 未配置，请在配置页面中设置"}

    client = ZhipuAI(api_key=api_key)
    params = {
        "search_engine": "search_std",
        "search_query": query_text,
        "count": count,
        "content_size": "medium",
    }
    if domain:
        params["search_domain_filter"] = domain
    if recency and recency != "noLimit":
        params["search_recency_filter"] = recency

    response = client.web_search.web_search(**params)
    results = []
    seen = set()
    if hasattr(response, "search_result"):
        for result in response.search_result:
            content = getattr(result, "content", "") or ""
            title = getattr(result, "title", "") or ""
            url = getattr(result, "url", "") or ""
            fingerprint = content[:60] or title[:60]
            if fingerprint and fingerprint not in seen:
                seen.add(fingerprint)
                results.append({"title": title, "content": content, "url": url})
            if len(results) >= count:
                break
    return {
        "success": True,
        "provider": "zhipu",
        "query": query_text,
        "count": len(results),
        "results": results,
    }


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)


def _call_mcp_in_thread(coro_factory):
    """在独立线程里运行 asyncio，避免和 FastMCP 的事件循环互相干扰。"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_async, coro_factory())
        return future.result(timeout=120)


def _mcp_content_text(result):
    parts = []
    for item in getattr(result, "content", None) or []:
        if isinstance(item, dict):
            text = item.get("text") or ""
        else:
            text = getattr(item, "text", None) or str(item)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


async def _remote_bailian_search(url, api_key, query, count):
    """以 MCP 客户端身份调用百炼 WebSearch，返回工具原始文本。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": "Bearer " + api_key}
    async with streamablehttp_client(url, headers=headers) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "bailian_web_search",
                {"query": query, "count": count},
            )
            if getattr(result, "isError", False):
                raise RuntimeError(_mcp_content_text(result) or "百炼 MCP 返回错误")
            return _mcp_content_text(result)


def _bailian_mcp_search(config, query_text, count, domain, recency):
    """调用百炼 MCP 广场的 WebSearch（HTTP MCP Server）。"""
    api_key = config.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "阿里百炼 API Key 未配置，请在配置页面中设置",
        }
    url = (config.get("DASHSCOPE_MCP_URL") or DASHSCOPE_MCP_DEFAULT_URL).strip()
    if not url.startswith(("http://", "https://")):
        return {"success": False, "error": "百炼 MCP 服务地址格式不正确"}
    query = (query_text or "").strip()
    if not query:
        return {"success": False, "error": "搜索内容不能为空"}

    try:
        raw_text = _call_mcp_in_thread(
            lambda: _remote_bailian_search(url, api_key, query, count)
        )
    except ImportError:
        return {
            "success": False,
            "error": "容器内缺少 MCP 客户端库，无法调用百炼 MCP WebSearch",
        }
    except Exception as exc:  # noqa: BLE001 - 把远端错误转为工具返回
        return {"success": False, "error": f"调用百炼 MCP 失败: {exc}"}

    if not raw_text.strip():
        return {"success": False, "error": "百炼 MCP 返回为空"}

    import json as _json

    data = None
    try:
        data = _json.loads(raw_text)
    except ValueError:
        data = None

    results = []
    if isinstance(data, dict):
        pages = data.get("pages") or []
        for page in pages:
            if not isinstance(page, dict):
                continue
            title = str(page.get("title") or "").strip()
            url = str(page.get("url") or "").strip()
            snippet = str(
                page.get("snippet") or page.get("content") or ""
            ).strip()
            if title or url:
                results.append(
                    {"title": title, "content": snippet, "url": url}
                )
            if len(results) >= count:
                break
    elif isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(
                item.get("snippet") or item.get("content") or ""
            ).strip()
            results.append({"title": title, "content": snippet, "url": url})
            if len(results) >= count:
                break

    if not results:
        # 远端协议变化或格式未知时，把原文带回给上层模型消化
        results.append(
            {"title": "百炼搜索结果", "content": raw_text[:2000], "url": ""}
        )

    return {
        "success": True,
        "provider": "bailian-mcp",
        "query": query_text,
        "count": len(results),
        "results": results,
    }


@mcp.tool()
def 联网查询(
    query_text: str,
    count: Optional[int] = None,
    search_domain_filter: str = "",
    search_recency_filter: str = "noLimit",
) -> dict:
    """
    联网查询最新信息（新闻、金价、天气、股票、热播影视等）。

    服务商由配置页面选择：
    - bailian-mcp（默认）：阿里百炼 MCP 广场 WebSearch（需在百炼控制台先开通）
    - zhipu：智谱 GLM Web Search

    :param query_text: 客户端传来的查询文字
    :param count: 返回结果条数 1-50；不传时使用配置页面里的“默认返回条数”
    :param search_domain_filter: 指定搜索域名（如 www.sohu.com），默认全网
    :param search_recency_filter: noLimit/week/month/year
    :return: {"success": bool, "results": [...]}
    """
    try:
        config = load_config()
        result_count = _clamp_count(count if count is not None else config.get("RESULT_COUNT", 8))
        provider = (config.get("PROVIDER") or "bailian-mcp").strip().lower()

        if provider == "bailian-mcp":
            result = _bailian_mcp_search(
                config, query_text, result_count, search_domain_filter, search_recency_filter
            )
        else:
            result = _zhipu_search(
                config, query_text, result_count, search_domain_filter, search_recency_filter
            )

        if result.get("success"):
            logger.info(
                "联网查询完成 [%s] provider=%s → %s 条结果",
                query_text,
                provider,
                result.get("count", 0),
            )
        else:
            logger.warning("联网查询失败 [%s]: %s", query_text, result.get("error"))
        return result
    except Exception as exc:  # noqa: BLE001 - 工具调用需始终返回 JSON 给上层 LLM
        logger.error("联网查询异常 [%s]: %s", query_text, exc)
        return {"success": False, "error": f"搜索失败: {exc}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
