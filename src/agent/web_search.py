"""웹 검색 도구 — agent(All-in-One)용.

Tavily 우선(결과에 본문 발췌 포함), 없으면 Jina 검색(메타데이터만),
둘 다 키가 없으면 None을 돌려줘 도구 자체가 등록되지 않는다 — 시스템
프롬프트는 도구가 없는 경우의 안내 문구를 유지한다.
"""

from langchain_core.tools import tool

from agent.scraping import JinaSearcher, ScraperConfig, TavilySearcher

MAX_RESULTS = 5
MAX_SNIPPET_CHARS = 400
MAX_RESULT_CHARS = 4_000


def make_web_search_tool(searcher=None):
    """검색 제공자를 골라 web_search 도구를 만든다. 키가 없으면 None."""
    if searcher is None:
        config = ScraperConfig()
        tavily = TavilySearcher(config)
        jina = JinaSearcher(config)
        searcher = tavily if tavily.available else (jina if jina.available else None)
    if searcher is None or not searcher.available:
        return None

    @tool
    def web_search(query: str) -> str:
        """공개 웹을 검색해 상위 결과(제목·URL·본문 발췌)를 돌려준다.

        최신 동향·뉴스·회사 정보 등 조서 파일과 기준서 밖의 정보가 필요할
        때 사용한다. 발췌만으로 부족하면 결과의 URL을 web_extract에 넘겨
        본문 전체를 읽는다. 질의는 한국어 핵심 키워드로 간결하게 쓴다.
        """
        try:
            hits = searcher.search(query, max_results=MAX_RESULTS)
        except Exception as exc:
            return f"오류: 웹 검색 실패 — {exc}"
        if not hits:
            return "검색 결과가 없습니다. 질의를 바꿔 다시 시도하세요."
        parts = []
        for index, hit in enumerate(hits, start=1):
            entry = f"{index}. {hit.title or '(제목 없음)'}\n   [출처: {hit.url}]"
            if hit.snippet:
                entry += f"\n   {hit.snippet[:MAX_SNIPPET_CHARS]}"
            parts.append(entry)
        return "\n".join(parts)[:MAX_RESULT_CHARS]

    return web_search
