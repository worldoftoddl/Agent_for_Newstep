"""agent web_search 도구 테스트 — 네트워크 무호출."""

from agent.scraping import SearchHit
from agent.web_search import make_web_search_tool


class FakeSearcher:
    def __init__(self, hits=None, fail=False):
        self.hits = hits or []
        self.fail = fail

    @property
    def available(self):
        return True

    def search(self, query, max_results=5):
        if self.fail:
            raise ValueError("quota exceeded")
        return self.hits[:max_results]


def test_returns_none_without_any_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    assert make_web_search_tool() is None


def test_formats_hits_with_source_urls():
    tool = make_web_search_tool(
        FakeSearcher(
            hits=[
                SearchHit(title="기사 A", url="https://a.example.com/", snippet="발췌 " * 200),
                SearchHit(title="", url="https://b.example.com/", snippet=""),
            ]
        )
    )

    output = tool.invoke({"query": "삼성전자 이슈"})

    assert "1. 기사 A" in output and "[출처: https://a.example.com/]" in output
    assert "2. (제목 없음)" in output
    assert len(output) < 4_100  # 스니펫·전체 클립


def test_search_failure_degrades_to_error_text():
    tool = make_web_search_tool(FakeSearcher(fail=True))
    output = tool.invoke({"query": "질의"})
    assert output.startswith("오류:")

    tool = make_web_search_tool(FakeSearcher(hits=[]))
    assert "검색 결과가 없습니다" in tool.invoke({"query": "질의"})
