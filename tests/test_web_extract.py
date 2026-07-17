"""웹 추출 서브그래프·도구 래퍼 테스트 — 네트워크·LLM 무호출 (전부 페이크 주입)."""

from types import SimpleNamespace

import pytest

import agent.web_extract as web_extract
from agent.scraping import FetchResult, ScraperConfig
from agent.web_extract import build_scraper_graph, make_web_extract_tool


class FakeFetcher:
    def __init__(self, html="<main><h1>Widget</h1><p>Price: $12</p></main>"):
        self.html = html

    def fetch(self, url):
        return FetchResult(html=self.html, final_url=url, content_type="text/html")


class FakeModel:
    """구조화 출력 시 dict, 아니면 .content 문자열을 돌려주는 페이크."""

    def __init__(self, structured=None, text="Widget 12달러"):
        self.structured = structured
        self.text = text

    def with_structured_output(self, _schema):
        return SimpleNamespace(invoke=lambda _messages: self.structured)

    def invoke(self, _messages):
        return SimpleNamespace(content=self.text)


@pytest.fixture(autouse=True)
def _skip_dns(monkeypatch):
    monkeypatch.setattr(web_extract, "validate_public_url", lambda url: url)


def test_graph_extracts_structured_result():
    graph = build_scraper_graph(
        model=FakeModel(structured={"name": "Widget", "price": 12}),
        config=ScraperConfig(chunk_chars=1_000),
        fetcher=FakeFetcher(),
    )

    result = graph.invoke(
        {
            "url": "https://example.com/product",
            "instruction": "Extract the product name and price",
            "output_schema": {
                "title": "Product",
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                },
                "required": ["name", "price"],
            },
        }
    )

    assert result["result"] == {"name": "Widget", "price": 12}
    assert result["attempts"] == 1
    assert result["error"] is None


def test_graph_retries_then_fails_on_empty_extraction():
    graph = build_scraper_graph(
        model=FakeModel(text=""),
        config=ScraperConfig(chunk_chars=1_000, max_extraction_attempts=2),
        fetcher=FakeFetcher(),
    )

    result = graph.invoke({"url": "https://example.com/x", "instruction": "추출"})

    assert result["attempts"] == 2
    assert result["error"]


def test_chunk_cap_drops_tail_and_reports():
    graph = build_scraper_graph(
        model=FakeModel(text="요약"),
        config=ScraperConfig(chunk_chars=50, chunk_overlap=5, max_chunks=2),
        fetcher=FakeFetcher(html="<main>" + "가나다라마바사 " * 100 + "</main>"),
    )

    result = graph.invoke({"url": "https://example.com/long", "instruction": "추출"})

    assert len(result["chunks"]) == 2
    assert result["chunks_dropped"] >= 1


def test_extraction_keeps_only_text_from_block_content():
    """anthropic output_version=v1: content가 reasoning 블록 섞인 리스트여도 텍스트만 남는다."""
    blocks = [
        {"type": "reasoning", "reasoning": "", "extras": {"signature": "xxx"}},
        {"type": "text", "text": "제목: Example Domain"},
    ]
    graph = build_scraper_graph(
        model=FakeModel(text=blocks),
        config=ScraperConfig(chunk_chars=1_000),
        fetcher=FakeFetcher(),
    )

    result = graph.invoke({"url": "https://example.com/p", "instruction": "추출"})

    assert result["result"] == "제목: Example Domain"


def test_tool_returns_source_header_and_text():
    tool = make_web_extract_tool(
        FakeModel(text="위젯 가격은 12달러"),
        config=ScraperConfig(chunk_chars=1_000),
        fetcher=FakeFetcher(),
    )

    output = tool.invoke(
        {"url": "https://example.com/product", "instruction": "가격 추출"}
    )

    assert output.startswith("[출처: https://example.com/product]")
    assert "위젯 가격은 12달러" in output


def test_tool_reports_fetch_error_as_text():
    class BrokenFetcher:
        def fetch(self, url):
            raise ValueError("Response exceeds configured size limit")

    tool = make_web_extract_tool(
        FakeModel(), config=ScraperConfig(), fetcher=BrokenFetcher()
    )

    output = tool.invoke({"url": "https://example.com/big", "instruction": "추출"})

    assert output.startswith("오류:")
    assert "size limit" in output


def test_tool_clips_long_result_with_notice():
    tool = make_web_extract_tool(
        FakeModel(text="가" * 10_000),
        config=ScraperConfig(chunk_chars=1_000),
        fetcher=FakeFetcher(),
    )

    output = tool.invoke({"url": "https://example.com/p", "instruction": "추출"})

    assert "절단됨" in output
    assert len(output) < 7_000


def test_tool_relays_chunk_drop_notice():
    tool = make_web_extract_tool(
        FakeModel(text="요약"),
        config=ScraperConfig(chunk_chars=50, chunk_overlap=5, max_chunks=1),
        fetcher=FakeFetcher(html="<main>" + "가나다라마바사 " * 100 + "</main>"),
    )

    output = tool.invoke({"url": "https://example.com/long", "instruction": "추출"})

    assert "앞부분만 추출" in output
