"""기준서 MCP 결과 소비 헬퍼 — reviewer·explainer 공용.

mcp_client 래퍼 도구를 코드에서 직접 호출할 때의 텍스트 추출·문단 파싱·
인용 확정(search → get_paragraph 재확인)을 모은다. LLM 없이 결정적으로
동작하며, 실패는 None 반환으로 강등된다 (예외를 밖으로 내지 않는다).
"""

import json

SEARCH_TOP_K = 3


async def tool_text(tool, args: dict) -> str:
    """mcp_client 래퍼 도구를 직접 호출해 텍스트 콘텐츠만 뽑는다."""
    raw = await tool.coroutine(**args)
    content = raw[0] if isinstance(raw, tuple) else raw
    if isinstance(content, list):
        content = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return content if isinstance(content, str) else str(content)


def first_hit(text: str) -> dict | None:
    """도구 결과 JSON에서 cid를 가진 첫 문단 아이템을 찾는다."""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("cid"):
        return payload
    for key in ("results", "paragraphs"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("cid"):
                return item
    return None


async def resolve_citation(
    search, get_para, query: str, source_hint: str = ""
) -> tuple[str, str] | None:
    """검색어로 문단을 찾고 원문 재확인까지 통과하면 (표기, cid)를 돌려준다.

    agent 그래프의 인용 규칙(search → get_paragraph 확정)을 코드로 강제한다.
    검색 실패·재확인 불일치·예외는 모두 None — 인용을 남기지 않는다.
    """
    try:
        args: dict = {"query": query, "top_k": SEARCH_TOP_K}
        if source_hint:
            args["source_type"] = [source_hint]
        hit = first_hit(await tool_text(search, args))
        if not hit:
            return None
        cid = hit["cid"]
        confirmed = first_hit(await tool_text(get_para, {"cid": cid}))
        if not confirmed or confirmed.get("cid") != cid:
            return None
        return (confirmed.get("display") or hit.get("display") or cid, cid)
    except Exception:
        return None
