"""웹 추출 서브그래프 + agent 도구 래퍼 (langgraph_web_scraping_agent 이식).

원본 그래프를 독립 그래프가 아닌 **서브그래프**로 취급한다:
- build_scraper_graph(model, ...) — {url, instruction} 입력의 컴파일된 그래프.
  MessagesState가 아니므로 호출자가 구조화 인수를 만들어 invoke한다.
- make_web_extract_tool(model) — agent(All-in-One)의 도구 노출용 래퍼.
  ReAct 모델이 도구 인수를 채우므로 메시지 파싱 계층이 필요 없다.

이식 시 변경: Playwright browser fallback 제거, 모델은 호출자 주입,
청크 수 상한(비용 관문)·도구 결과 클립(컨텍스트 보호) 추가.
"""

import json
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from agent.graph_common import emit, msg_text
from agent.scraping import HttpFetcher, ScraperConfig, html_to_text, split_text, validate_public_url


class ScraperInput(TypedDict):
    url: str
    instruction: str
    output_schema: NotRequired[dict[str, Any] | None]


class ScraperState(ScraperInput, total=False):
    final_url: str
    raw_html: str
    cleaned_text: str
    chunks: list[str]
    chunks_dropped: int
    chunk_results: list[Any]
    result: Any
    attempts: int
    error: str | None


def _response_value(response: Any) -> Any:
    """LLM 응답에서 값을 꺼낸다 — 구조화 출력(dict)은 그대로, 메시지는 텍스트만.

    anthropic output_version="v1"은 content가 블록 리스트(reasoning 포함)라
    .content를 그대로 쓰면 서명 딸린 reasoning 블록까지 결과에 섞인다.
    """
    if not hasattr(response, "content"):
        return response
    return msg_text(response)


def _has_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


class ScraperNodes:
    def __init__(
        self,
        model: BaseChatModel,
        config: ScraperConfig,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        self.model = model
        self.config = config
        self.fetcher = fetcher or HttpFetcher(config)

    def validate_request(self, state: ScraperState) -> dict[str, Any]:
        emit("validating_request", "입력값을 검증하는 중")
        instruction = state.get("instruction", "").strip()
        if not instruction:
            raise ValueError("instruction must not be empty")
        return {
            "url": validate_public_url(state["url"]),
            "instruction": instruction,
            "attempts": 0,
            "error": None,
        }

    def fetch_page(self, state: ScraperState) -> dict[str, Any]:
        emit("fetching", "페이지를 가져오는 중", url=state["url"])
        fetched = self.fetcher.fetch(state["url"])
        return {"raw_html": fetched.html, "final_url": fetched.final_url}

    def clean_content(self, state: ScraperState) -> dict[str, Any]:
        emit("cleaning", "페이지 본문을 정리하는 중")
        text = html_to_text(state["raw_html"])
        if not text:
            raise ValueError("No readable page content was found")
        return {"cleaned_text": text}

    def chunk_content(self, state: ScraperState) -> dict[str, Any]:
        chunks = split_text(
            state["cleaned_text"],
            self.config.chunk_chars,
            self.config.chunk_overlap,
        )
        # 비용 관문: 청크당 LLM 1회이므로 상한을 넘는 본문 꼬리는 버리고 고지한다
        dropped = max(0, len(chunks) - self.config.max_chunks)
        if dropped:
            chunks = chunks[: self.config.max_chunks]
        emit("chunking", "본문 분할 완료", total=len(chunks), dropped=dropped)
        return {"chunks": chunks, "chunks_dropped": dropped}

    def extract_chunks(self, state: ScraperState) -> dict[str, Any]:
        schema = state.get("output_schema")
        extractor = self.model.with_structured_output(schema) if schema else self.model
        results: list[Any] = []
        total = len(state["chunks"])
        attempt = state.get("attempts", 0) + 1
        for index, chunk in enumerate(state["chunks"], start=1):
            emit("extracting", "정보를 추출하는 중", current=index, total=total, attempt=attempt)
            messages = [
                SystemMessage(
                    content=(
                        "Extract only information supported by the supplied webpage text. "
                        "Do not guess missing values. Return a concise structured result."
                    )
                ),
                HumanMessage(
                    content=f"Extraction instruction:\n{state['instruction']}\n\nWebpage text:\n{chunk}"
                ),
            ]
            response = extractor.invoke(messages)
            results.append(_response_value(response))
        return {"chunk_results": results, "attempts": attempt, "error": None}

    def merge_results(self, state: ScraperState) -> dict[str, Any]:
        results = [result for result in state["chunk_results"] if _has_value(result)]
        if not results:
            return {"result": None}
        if len(results) == 1:
            return {"result": results[0]}

        emit("merging", "분할 추출 결과를 병합하는 중", total=len(results))
        schema = state.get("output_schema")
        merger = self.model.with_structured_output(schema) if schema else self.model
        messages = [
            SystemMessage(
                content=(
                    "Merge the extraction results into one result. Remove duplicates, preserve "
                    "source facts, and do not invent values."
                )
            ),
            HumanMessage(content=f"Instruction:\n{state['instruction']}\n\nResults:\n{results!r}"),
        ]
        response = merger.invoke(messages)
        return {"result": _response_value(response)}

    def validate_result(self, state: ScraperState) -> dict[str, Any]:
        emit("validating_result", "추출 결과를 검증하는 중")
        if _has_value(state.get("result")):
            return {"error": None}
        return {"error": "The model returned an empty extraction result"}

    def route_after_validation(self, state: ScraperState) -> Literal["retry", "complete", "fail"]:
        if not state.get("error"):
            return "complete"
        if state.get("attempts", 0) < self.config.max_extraction_attempts:
            return "retry"
        return "fail"

    def complete(self, state: ScraperState) -> dict[str, Any]:
        emit("complete", "추출 완료")
        return {}

    def fail(self, state: ScraperState) -> dict[str, Any]:
        emit("failed", state.get("error") or "추출에 실패했습니다")
        return {}


def build_scraper_graph(
    model: BaseChatModel,
    config: ScraperConfig | None = None,
    fetcher: HttpFetcher | None = None,
):
    """웹 추출 서브그래프를 조립한다. 의존성은 테스트·호스팅용으로 주입 가능."""
    settings = config or ScraperConfig()
    nodes = ScraperNodes(model, settings, fetcher=fetcher)

    builder = StateGraph(ScraperState, input_schema=ScraperInput)
    builder.add_node("validate_request", nodes.validate_request)
    builder.add_node(
        "fetch_page",
        nodes.fetch_page,
        retry_policy=RetryPolicy(max_attempts=3, initial_interval=1.0),
    )
    builder.add_node("clean_content", nodes.clean_content)
    builder.add_node("chunk_content", nodes.chunk_content)
    builder.add_node("extract_chunks", nodes.extract_chunks)
    builder.add_node("merge_results", nodes.merge_results)
    builder.add_node("validate_result", nodes.validate_result)
    builder.add_node("complete", nodes.complete)
    builder.add_node("fail", nodes.fail)

    builder.add_edge(START, "validate_request")
    builder.add_edge("validate_request", "fetch_page")
    builder.add_edge("fetch_page", "clean_content")
    builder.add_edge("clean_content", "chunk_content")
    builder.add_edge("chunk_content", "extract_chunks")
    builder.add_edge("extract_chunks", "merge_results")
    builder.add_edge("merge_results", "validate_result")
    builder.add_conditional_edges(
        "validate_result",
        nodes.route_after_validation,
        {"retry": "extract_chunks", "complete": "complete", "fail": "fail"},
    )
    builder.add_edge("complete", END)
    builder.add_edge("fail", END)
    return builder.compile()


MAX_RESULT_CHARS = 6_000  # 도구 결과 클립 — MCP 도구 결과 상한과 같은 기준


def make_web_extract_tool(
    model: BaseChatModel,
    config: ScraperConfig | None = None,
    fetcher: HttpFetcher | None = None,
):
    """라우팅된 모델을 서브그래프에 묶어 web_extract 도구를 만든다."""
    graph = build_scraper_graph(model, config=config, fetcher=fetcher)

    @tool
    def web_extract(url: str, instruction: str) -> str:
        """공개 웹 페이지를 가져와 지시에 맞는 정보만 추출해 돌려준다.

        사용자가 URL을 주며 그 내용의 요약·특정 정보 추출을 요청할 때 사용한다.
        http(s) 공개 페이지만 허용되며 사내망·비공개 주소는 차단된다.
        instruction에는 무엇을 추출할지 한 문장으로 구체적으로 적는다
        (예: "개정 기준서의 시행일과 주요 변경 사항을 추출").
        """
        try:
            state = graph.invoke({"url": url, "instruction": instruction})
        except ValueError as exc:  # UnsafeUrlError 포함 — 원인 그대로 안내
            return f"오류: {exc}"
        except Exception as exc:
            return f"오류: 페이지를 가져오지 못했습니다 — {type(exc).__name__}: {exc}"

        if state.get("error"):
            return f"오류: {state['error']}"

        result = state.get("result")
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        notes = []
        if state.get("chunks_dropped"):
            notes.append(
                f"본문이 길어 앞부분만 추출함 (청크 {state['chunks_dropped']}개 미처리)"
            )
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS]
            notes.append(f"결과가 길어 {MAX_RESULT_CHARS}자에서 절단됨")
        final_url = state.get("final_url", url)
        header = f"[출처: {final_url}]"
        if notes:
            header += " [" + " / ".join(notes) + "]"
        return f"{header}\n{text}"

    return web_extract
