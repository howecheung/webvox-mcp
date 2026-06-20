# server.py
from mcp.server.fastmcp import FastMCP
import sys
import logging

logger = logging.getLogger(__name__)

# Fix UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

from zhipuai import ZhipuAI
from config_manager import load_config

# Create an MCP server·12
mcp = FastMCP("mcps")

# Add an addition tool
# 修改原有的ZhipuAI初始化
@mcp.tool()
def 联网查询(
    query_text: str,
    count: int = 8,
    search_domain_filter: str = "",
    search_recency_filter: str = "noLimit"
) -> dict:
    """
    通过智谱AI Web Search API查询信息
    查询最近新闻、金价、热播电影电视等一切用户需要联网查询的内容。
    :param query_text: 客户端传来的查询文字
    :param count: 返回结果的条数，范围1-50，默认8（适合TTS朗读的长度）
    :param search_domain_filter: 指定搜索域名，默认空（全网搜索），如 "www.sohu.com"
    :param search_recency_filter: 时间过滤，默认noLimit，可选 "week"/"month"/"year"
    :return: 查询结果 {"success": True, "results": [...]} 或 {"success": False, "error": "..."}
    """
    try:
        config = load_config()
        if not config.get("ZHIPU_API_KEY") or config["ZHIPU_API_KEY"] == "xxxxxxxxxxxxxxxxxxxxxx":
            return {"success": False, "error": "智谱API Key未配置，请在启动面板中设置"}

        client = ZhipuAI(api_key=config["ZHIPU_API_KEY"])

        # 构建搜索参数
        search_params = {
            "search_engine": "search_std",
            "search_query": query_text,
            "count": min(max(count, 1), 50),
            "content_size": "medium"
        }
        if search_domain_filter:
            search_params["search_domain_filter"] = search_domain_filter
        if search_recency_filter:
            search_params["search_recency_filter"] = search_recency_filter

        response = client.web_search.web_search(**search_params)

        # 解析并去重结果
        results = []
        seen = set()
        if hasattr(response, 'search_result'):
            for result in response.search_result:
                content = getattr(result, 'content', '')
                title = getattr(result, 'title', '')
                url = getattr(result, 'url', '')
                # 用 content 前 60 字做去重指纹
                fingerprint = content[:60] if content else ""
                if fingerprint and fingerprint not in seen:
                    seen.add(fingerprint)
                    results.append({
                        "title": title,
                        "content": content,
                        "url": url
                    })

        logger.info(f"联网查询完成 [{query_text}] → {len(results)}条结果")
        return {"success": True, "query": query_text, "count": len(results), "results": results}

    except Exception as e:
        logger.error(f"联网查询失败 [{query_text}]: {str(e)}")
        return {"success": False, "error": f"搜索失败: {str(e)}"}





# Start the server
if __name__ == "__main__":
    mcp.run(transport="stdio")


