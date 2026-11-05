# -*- coding: utf-8 -*-
"""联网查询 MCP 工具：智谱 GLM Web Search / 阿里百炼 WebSearch MCP / DeepSeek 联网搜索。

通过配置页选择服务商；所有服务商对外暴露同一个工具（对外名 “webvox-search”），
签名与上游 webvox-mcp 保持一致：

- zhipu       智谱 GLM Web Search
- bailian-mcp 阿里百炼 MCP 广场“联网搜索”WebSearch（需在控制台先开通）
- deepseek    DeepSeek 官方联网搜索（POST /v1/tools/search）

fetch_url 抓页能力默认下线（小智控制台只保留一个工具）；需要时取消 fetch_url
上方的装饰器注释即可恢复。
"""

import copy
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional


APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from mcp.server.fastmcp import FastMCP  # noqa: E402

import config_manager  # noqa: E402
from config_manager import load_config  # noqa: E402


logger = logging.getLogger(__name__)

# requests 是容器镜像里的既有依赖；万一镜像没装，也不该让整个 MCP Server 崩掉，
# 这里改成可选导入，用到 DeepSeek / 本地抓取时再给出明确提示。
try:  # pragma: no cover - 取决于运行环境
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None

# 兼容 Windows 控制台 UTF-8（容器内通常无需，保留无副作用）
if sys.platform == "win32":
    try:
        sys.stderr.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

mcp = FastMCP("mcps")

# --- 结果缓存 ---------------------------------------------------------------
# 同一 query 短时间内重复提问（语音端很常见）直接复用上次结果，
# 既省 DeepSeek 的按次计费，也避免语音端干等。
CACHE_TTL_DEFAULT = 60
CACHE_TTL_MAX = 3600
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
        if not item:
            return None
        expire, payload = item
        if expire < time.time():
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(payload)


def _cache_put(key, payload, ttl):
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        now = time.time()
        if len(_CACHE) > 256:
            for stale in [k for k, v in _CACHE.items() if v[0] < now]:
                _CACHE.pop(stale, None)
            if len(_CACHE) > 256:
                _CACHE.clear()
        _CACHE[key] = (now + ttl, copy.deepcopy(payload))


# --- 用量统计 ---------------------------------------------------------------
USAGE_FILENAME = "usage.jsonl"
_USAGE_MAX_LINES = 2000
_USAGE_MAX_BYTES = 512 * 1024


def usage_file_path():
    """用量记录与配置文件同目录（容器内即 /config/usage.jsonl）。"""
    try:
        parent = Path(str(config_manager.CONFIG_PATH)).parent
    except Exception:
        parent = Path(APP_DIR)
    return parent / USAGE_FILENAME


def _record_usage(provider, count, cached, elapsed_ms, mode):
    try:
        path = usage_file_path()
        if path.exists() and path.stat().st_size > _USAGE_MAX_BYTES:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            path.write_text("\n".join(lines[-_USAGE_MAX_LINES:]) + "\n", encoding="utf-8")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": provider,
            "count": int(count or 0),
            "cached": bool(cached),
            "ms": int(elapsed_ms or 0),
            "mode": mode,
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - 统计失败不能影响搜索
        logger.debug("写入用量统计失败: %s", exc)


DASHSCOPE_MCP_DEFAULT_URL = (
    "https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp"
)


def _clamp_count(count):
    try:
        return max(1, min(50, int(count)))
    except (TypeError, ValueError):
        return 8


def _zhipu_search(config, query_text, count, domain, recency, timeout=15):
    """调用智谱 Web Search，返回与上游一致的 title/content/url 结果。"""
    from zhipuai import ZhipuAI

    api_key = config.get("ZHIPU_API_KEY", "")
    if not api_key:
        return {"success": False, "error": "智谱 API Key 未配置，请在配置页面中设置"}

    try:
        client = ZhipuAI(api_key=api_key, timeout=timeout)
    except TypeError:
        # 旧版 SDK 不支持 timeout 参数
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


def _call_mcp_in_thread(coro_factory, timeout=120):
    """在独立线程里运行 asyncio，避免和 FastMCP 的事件循环互相干扰。"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_run_async, coro_factory())
        return future.result(timeout=timeout)


def _apply_budget(results, max_chars):
    """按总字符预算截断结果，返回 (results, truncated)。

    语音设备端一次只能消化有限文本，逐条累加，超出预算的条目截断正文，
    后续条目丢弃但不影响已保留内容的完整性。
    """
    try:
        budget = max(0, int(max_chars))
    except (TypeError, ValueError):
        return results, False
    if not budget:
        return results, False

    kept = []
    used = 0
    truncated = False
    for item in results:
        body = str(item.get("content") or "")
        cost = len(body) + len(str(item.get("title") or ""))
        if used + cost <= budget:
            kept.append(item)
            used += cost
            continue
        # 剩下预算不够整条，能截多少截多少
        remain = budget - used - len(str(item.get("title") or ""))
        if remain > 100:
            clipped = dict(item)
            clipped["content"] = body[:remain]
            kept.append(clipped)
        truncated = True
        break
    return kept, truncated


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


async def _remote_bailian_search(url, api_key, query, count, timeout=15):
    """以 MCP 客户端身份调用百炼 WebSearch，返回工具原始文本。"""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": "Bearer " + api_key}
    async with streamablehttp_client(url, headers=headers, timeout=timeout) as (
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


def _bailian_mcp_search(config, query_text, count, domain, recency, timeout=15):
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
            lambda: _remote_bailian_search(url, api_key, query, count, timeout),
            timeout=timeout + 5,
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


DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"


def _deepseek_search(config, query_text, count, domain, recency, timeout=15, mode="brief"):
    """调用 DeepSeek 官方联网搜索接口（POST /v1/tools/search）。

    mode=brief 时只要摘要（summary/snippet），不请求全文，避免语音设备端
    上下文爆炸；mode=detail 才带 need_content=true 取完整正文。
    """
    if _requests is None:  # pragma: no cover - 依赖缺失
        return {
            "success": False,
            "error": "当前运行环境缺少 requests 依赖，无法调用 DeepSeek 接口（可改用智谱或百炼）",
        }

    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "DeepSeek API Key 未配置，请在配置页面中设置",
        }
    query = (query_text or "").strip()
    if not query:
        return {"success": False, "error": "搜索内容不能为空"}

    base_url = (
        config.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL
    ).rstrip("/")
    url = f"{base_url}/v1/tools/search"

    payload = {
        "query": query,
        "count": count,
        "need_content": mode == "detail",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = _requests.post(
            url, json=payload, headers=headers, timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 - 网络异常转工具返回
        return {"success": False, "error": f"调用 DeepSeek 搜索失败: {exc}"}

    if response.status_code != 200:
        message = ""
        try:
            err = response.json().get("error") or {}
            message = err.get("message") or response.text[:200]
        except ValueError:
            message = response.text[:200]
        if response.status_code == 401:
            message = message or "DeepSeek API Key 无效"
        elif response.status_code == 402:
            message = message or "DeepSeek 账户余额不足"
        elif response.status_code == 429:
            message = message or "DeepSeek 请求过于频繁，请稍后重试"
        return {
            "success": False,
            "error": f"DeepSeek 搜索失败（HTTP {response.status_code}）: {message}",
        }

    try:
        data = response.json()
    except ValueError:
        return {"success": False, "error": "DeepSeek 返回内容无法解析为 JSON"}

    results = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        link = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        summary = str(item.get("summary") or "").strip()
        content = str(item.get("content") or "").strip()
        if mode == "detail":
            body = content or summary or snippet
        else:
            body = summary or snippet or content
        if title or link:
            results.append({"title": title, "content": body, "url": link})
        if len(results) >= count:
            break

    if not results:
        return {
            "success": True,
            "provider": "deepseek",
            "query": query_text,
            "count": 0,
            "results": [],
        }

    return {
        "success": True,
        "provider": "deepseek",
        "query": query_text,
        "count": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 网页抓取（Fetch）
# ---------------------------------------------------------------------------

FETCH_MAX_CHARS_DEFAULT = 6000
FETCH_MAX_CHARS_LIMIT = 20000

_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _clamp_max_chars(value):
    try:
        return max(500, min(FETCH_MAX_CHARS_LIMIT, int(value)))
    except (TypeError, ValueError):
        return FETCH_MAX_CHARS_DEFAULT


def _deepseek_fetch(config, url):
    """调用 DeepSeek 官方抓取接口（POST /v1/tools/fetch），返回 Markdown 正文。"""
    if _requests is None:  # pragma: no cover - 依赖缺失
        return {
            "success": False,
            "error": "当前运行环境缺少 requests 依赖，无法调用 DeepSeek 抓取接口",
        }

    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return {
            "success": False,
            "error": "DeepSeek API Key 未配置，无法使用 DeepSeek 抓取接口",
        }
    base_url = (
        config.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL
    ).rstrip("/")
    endpoint = f"{base_url}/v1/tools/fetch"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = _requests.post(
            endpoint, json={"url": url}, headers=headers, timeout=60
        )
    except Exception as exc:  # noqa: BLE001 - 网络异常转工具返回
        return {"success": False, "error": f"调用 DeepSeek 抓取失败: {exc}"}

    if response.status_code != 200:
        message = ""
        try:
            err = response.json().get("error") or {}
            message = err.get("message") or response.text[:200]
        except ValueError:
            message = response.text[:200]
        if response.status_code == 401:
            message = message or "DeepSeek API Key 无效"
        elif response.status_code == 402:
            message = message or "DeepSeek 账户余额不足"
        elif response.status_code == 403:
            message = message or "该页面的抓取请求被安全策略拒绝"
        elif response.status_code == 404:
            message = message or "页面不可用或没有可提取的正文"
        elif response.status_code == 429:
            message = message or "DeepSeek 请求过于频繁，请稍后重试"
        return {
            "success": False,
            "error": f"DeepSeek 抓取失败（HTTP {response.status_code}）: {message}",
        }

    try:
        data = response.json()
    except ValueError:
        return {"success": False, "error": "DeepSeek 返回内容无法解析为 JSON"}

    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    if not content:
        return {
            "success": False,
            "error": "DeepSeek 未返回网页正文（该页面可能不可读或无正文）",
        }
    return {
        "success": True,
        "source": "deepseek",
        "url": url,
        "title": title,
        "content": content,
    }


def _local_fetch(url):
    """本地直接抓取网页并抽取正文（无 API Key 时的兜底，不产生外部计费）。"""
    if _requests is None:  # pragma: no cover - 依赖缺失
        return {"success": False, "error": "本地抓取需要 requests 依赖，当前运行环境未安装"}

    try:
        from bs4 import BeautifulSoup  # noqa: F401
    except ImportError:
        # 容器里可能没装 beautifulsoup4，退回正则去标签
        BeautifulSoup = None

    try:
        response = _requests.get(
            url,
            headers={"User-Agent": _FETCH_USER_AGENT},
            timeout=30,
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 - 网络异常转工具返回
        return {"success": False, "error": f"抓取网页失败: {exc}"}

    if response.status_code >= 400:
        return {
            "success": False,
            "error": f"抓取网页失败（HTTP {response.status_code}）: {url}",
        }

    try:
        response.encoding = response.apparent_encoding or response.encoding
    except Exception:  # noqa: BLE001 - 编码探测失败时沿用原值
        pass

    if BeautifulSoup is None:
        return _local_fetch_without_bs4(response.text, url, limit_chars=None)

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(
        ["script", "style", "noscript", "iframe", "svg", "nav", "header",
         "footer", "aside", "form"]
    ):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    node = soup.find("article") or soup.find("main") or soup.body or soup
    lines = [line.strip() for line in node.get_text("\n", strip=True).splitlines()]
    content = "\n".join(line for line in lines if line)
    if not content:
        return {"success": False, "error": "页面没有可提取的文本内容"}

    return {
        "success": True,
        "source": "local",
        "url": url,
        "title": title,
        "content": content,
    }


def _local_fetch_without_bs4(html_text, url, limit_chars=None):
    """没有 beautifulsoup4 时的极简正文提取（正则去标签）。"""
    import html as html_module
    import re

    text = html_text or ""
    title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if match:
        title = html_module.unescape(match.group(1)).strip()
    # 去掉不可见/装饰性区块
    text = re.sub(
        r"<(script|style|noscript|iframe|svg|nav|header|footer|aside|form|head)\b.*?</\1>",
        " ",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    # 块级标签换行，行内标签直接去掉
    text = re.sub(r"</?(p|div|br|li|tr|h[1-6]|section|article|main)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    content = "\n".join(line for line in lines if line)
    if limit_chars and len(content) > limit_chars:
        content = content[:limit_chars]
    if not content:
        return {"success": False, "error": "页面没有可提取的文本内容"}
    return {
        "success": True,
        "source": "local",
        "url": url,
        "title": title,
        "content": content,
    }


@mcp.tool(name="webvox-search", title="联网搜索")
def 联网查询(
    query_text: str,
    count: Optional[int] = None,
    search_domain_filter: str = "",
    search_recency_filter: str = "noLimit",
) -> dict:
    """
    联网查询最新信息（新闻、金价、天气、股票、热播影视等）。

    :param query_text: 客户端传来的查询文字
    :param count: 返回结果条数 1-50；不传时使用配置页面里的“默认返回条数”
    :param search_domain_filter: 指定搜索域名（如 www.sohu.com），默认全网
    :param search_recency_filter: noLimit/week/month/year
    :return: {"success": bool, "results": [{"title", "content", "url"}]}
    """
    try:
        started = time.perf_counter()
        config = load_config()
        result_count = _clamp_count(count if count is not None else config.get("RESULT_COUNT", 8))
        provider = (config.get("PROVIDER") or "bailian-mcp").strip().lower()
        mode = (config.get("RESULT_MODE") or "brief").strip().lower()
        if mode not in ("brief", "detail"):
            mode = "brief"
        try:
            timeout = max(3, min(60, int(config.get("SEARCH_TIMEOUT", 15))))
        except (TypeError, ValueError):
            timeout = 15
        try:
            cache_ttl = max(0, min(CACHE_TTL_MAX, int(config.get("CACHE_TTL", CACHE_TTL_DEFAULT))))
        except (TypeError, ValueError):
            cache_ttl = CACHE_TTL_DEFAULT

        cache_key = "|".join(
            [
                provider,
                mode,
                str(result_count),
                (query_text or "").strip(),
                (search_domain_filter or "").strip(),
                (search_recency_filter or "").strip(),
            ]
        )
        if cache_ttl:
            hit = _cache_get(cache_key)
            if hit is not None:
                hit["cached"] = True
                _record_usage(provider, hit.get("count", 0), True, 0, mode)
                logger.info(
                    "联网查询命中缓存 [%s] provider=%s → %s 条（TTL %ss）",
                    query_text,
                    provider,
                    hit.get("count", 0),
                    cache_ttl,
                )
                return hit

        if provider == "bailian-mcp":
            result = _bailian_mcp_search(
                config,
                query_text,
                result_count,
                search_domain_filter,
                search_recency_filter,
                timeout=timeout,
            )
        elif provider == "deepseek":
            result = _deepseek_search(
                config,
                query_text,
                result_count,
                search_domain_filter,
                search_recency_filter,
                timeout=timeout,
                mode=mode,
            )
        else:
            result = _zhipu_search(
                config,
                query_text,
                result_count,
                search_domain_filter,
                search_recency_filter,
                timeout=timeout,
            )

        if result.get("success"):
            kept, truncated = _apply_budget(
                result.get("results") or [], config.get("RESULT_MAX_CHARS", 4000)
            )
            result["results"] = kept
            result["count"] = len(kept)
            result["mode"] = mode
            if truncated:
                result["truncated"] = True
            _cache_put(cache_key, result, cache_ttl)
            _record_usage(
                provider,
                result.get("count", 0),
                False,
                int((time.perf_counter() - started) * 1000),
                mode,
            )
            logger.info(
                "联网查询完成 [%s] provider=%s mode=%s → %s 条结果%s",
                query_text,
                provider,
                mode,
                result.get("count", 0),
                "（已按预算截断）" if truncated else "",
            )
        else:
            _record_usage(
                provider,
                0,
                False,
                int((time.perf_counter() - started) * 1000),
                mode,
            )
            logger.warning("联网查询失败 [%s]: %s", query_text, result.get("error"))
        return result
    except Exception as exc:  # noqa: BLE001 - 工具调用需始终返回 JSON 给上层 LLM
        logger.error("联网查询异常 [%s]: %s", query_text, exc)
        return {"success": False, "error": f"搜索失败: {exc}"}


# fetch_url 默认不上线：小智控制台只保留「webvox-search」一个工具。
# 需要恢复网页抓取能力时，取消下一行注释并重启桥接即可。
# @mcp.tool()
def fetch_url(url: str, max_chars: Optional[int] = None) -> dict:
    """抓取指定网页并返回正文内容（Markdown / 纯文本）。

    优先使用 DeepSeek 官方抓取接口（POST /v1/tools/fetch，返回 Markdown）；
    若未配置 DeepSeek API Key 或该接口失败，自动改用本地抓取并抽取正文。
    适用于“帮我看看这个链接讲了什么”“总结这篇报道”等需求。

    :param url: 要抓取的网页地址，必须是 http:// 或 https:// 开头的完整 URL
    :param max_chars: 返回正文的最大字符数，默认取配置里的“抓取正文长度”，最大 20000
    :return: {"success": bool, "url": str, "title": str, "content": str, "source": str}
    """
    try:
        target = (url or "").strip()
        if not target:
            return {"success": False, "error": "网址不能为空"}
        if not target.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": "网址必须以 http:// 或 https:// 开头",
            }
        if len(target) > 8192:
            return {"success": False, "error": "网址长度不能超过 8192 个字符"}

        config = load_config()
        limit = _clamp_max_chars(
            max_chars
            if max_chars is not None
            else config.get("FETCH_MAX_CHARS", FETCH_MAX_CHARS_DEFAULT)
        )
        config_mode = str(config.get("FETCH_MODE") or "auto").strip().lower()

        result = None
        use_deepseek = config_mode in ("auto", "deepseek")
        use_local = config_mode in ("auto", "local")
        if use_deepseek and config.get("DEEPSEEK_API_KEY"):
            result = _deepseek_fetch(config, target)
            if not result.get("success"):
                logger.warning(
                    "DeepSeek 抓取失败，改用本地抓取 [%s]: %s",
                    target,
                    result.get("error"),
                )
                result = None
        if result is None:
            if not use_local:
                return {
                    "success": False,
                    "error": "网页抓取失败（DeepSeek 抓取接口返回失败，且未启用本地抓取）",
                }
            result = _local_fetch(target)

        if not result.get("success"):
            logger.warning("网页抓取失败 [%s]: %s", target, result.get("error"))
            return result

        content = result.get("content", "")
        truncated = len(content) > limit
        if truncated:
            content = content[:limit]
        result["content"] = content
        result["truncated"] = truncated
        result["length"] = len(content)
        logger.info(
            "网页抓取完成 [%s] source=%s → %d 字",
            target,
            result.get("source"),
            len(content),
        )
        return result
    except Exception as exc:  # noqa: BLE001 - 工具调用需始终返回 JSON 给上层 LLM
        logger.error("网页抓取异常 [%s]: %s", url, exc)
        return {"success": False, "error": f"抓取失败: {exc}"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
