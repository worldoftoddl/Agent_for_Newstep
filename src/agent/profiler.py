"""기업이해 전용 그래프 — langgraph.json의 "profiler" 진입점.

감사 착수 전 "기업과 기업환경 이해"(감사기준서 315의 이해 활동)를 공개 웹
자료로 보조하는 고정 워크플로:

  triage(브리핑/대화 분기) → plan(회사명·초점 파싱, URL은 코드 추출)
  → gather(자료 URL 확정 — 사용자 제공 URL 우선, JINA_API_KEY 있으면
  s.jina.ai 검색으로 보충) → extract(웹 추출 서브그래프 재사용, URL당
  LLM 1회) → analyze(LLM 구조화 프로파일) → cite(감사기준 근거 확정, MCP)
  → report(결정적 템플릿 렌더)

산출물은 산업·규제 환경 / 사업의 성격 / 재무 하이라이트 / 최근 이슈 /
유의적 위험 후보(경영진 주장·기준서 근거) / 추가 확인 필요사항 — 즉
"회계사가 감사 이전에 확보해야 할 회사 이해"의 브리핑이다. 공개 웹 자료
기반이므로 감사증거가 아니라 이해 활동의 출발점임을 템플릿이 고지한다.

비용 상한: LLM 호출 triage 1 + plan 1 + extract ≤4 + analyze ≤2(재시도)
= 최대 8회. 자료는 4건, 자료당 추출 결과 5,000자 클립.
"""

import asyncio
import re
from typing import Any, Literal
from urllib.parse import urlparse

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Annotated, TypedDict

from agent.graph import DEFAULT_MODEL, resolve_model
from agent.graph_common import conversation_context, emit, human_texts_newest_first
from agent.mcp_client import get_standards_tools
from agent.scraping import JinaSearcher, ScraperConfig
from agent.standards_lookup import resolve_citation, tool_text
from agent.web_extract import build_scraper_graph

MAX_SOURCES = 4  # 추출할 자료 수 = extract 단계 LLM 호출 상한
MAX_EXTRACT_CHARS = 5_000  # 자료당 추출 결과 클립
MAX_SEARCH_RESULTS = 5  # 검색 질의당 수집 상한
MAX_ANALYZE_ATTEMPTS = 2
MAX_CITED_RISKS = 8

# chat 미니 ReAct(기준서 도구)의 상한 — explainer/reviewer와 동일 패턴
MAX_CHAT_TOOL_ROUNDS = 3
MAX_CHAT_TOOL_CALLS = 4
MAX_TOOL_RESULT_CHARS = 6_000

_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+")

NO_SOURCE_MESSAGE = (
    "기업이해 브리핑에는 조사할 공개 자료가 필요합니다. 두 가지 방법이 "
    "있습니다: ① 회사 소개·IR·뉴스 기사 등 공개 페이지 URL을 메시지에 "
    "함께 적어 주세요 (여러 개 가능). ② 서버에 JINA_API_KEY 시크릿을 "
    "설정하면 회사명만으로 웹 검색부터 자동 수행합니다 (jina.ai 무료 키)."
)


# ── LLM 구조화 출력 ──────────────────────────────────────────────────────
class TriageDecision(BaseModel):
    mode: Literal["profile", "chat"] = Field(
        description=(
            "profile: 특정 회사에 대한 기업이해·사전 조사·브리핑 요청. "
            "chat: 인사·사용법 질문·기능 문의·이전 브리핑에 대한 후속 설명 등 "
            "새 조사가 필요 없는 대화."
        )
    )


class ProfilePlan(BaseModel):
    company: str = Field(
        description="조사 대상 회사명 — 메시지에서 식별. 식별 불가면 빈 문자열"
    )
    focus: str = Field(
        default="",
        description="사용자가 특별히 궁금해하는 초점 (예: '최근 소송', '수익성 추세') — 없으면 빈 문자열",
    )


class IssueNote(BaseModel):
    issue: str = Field(description="최근 이슈 한 줄 요약")
    detail: str = Field(description="내용과 감사 관점에서의 함의 한두 문장")
    source: str = Field(default="", description="근거가 된 자료의 URL (수집 자료 중에서)")


class RiskCandidate(BaseModel):
    risk: str = Field(description="유의적 위험 후보 한 줄 (확정이 아니라 후보)")
    rationale: str = Field(description="수집 정보의 어떤 사실이 이 위험을 시사하는지")
    affected_area: str = Field(
        description="영향받는 계정·거래유형과 경영진 주장 (예: '매출 — 발생사실·기간귀속')"
    )
    standards_query: str = Field(
        default="",
        description="이 위험 평가의 근거가 될 감사기준 검색어 — 한국어 핵심 개념. 불필요하면 빈 문자열",
    )
    source_hint: Literal["감사기준", "회계기준", "실무지침", ""] = Field(
        default="감사기준", description="검색을 한정할 문서군"
    )
    citation: str = Field(default="", description="(시스템이 채움) 확정 인용 표기")
    citation_cid: str = Field(default="", description="(시스템이 채움) 인용 cid")


class CompanyProfile(BaseModel):
    company_overview: str = Field(description="회사가 무엇을 하는 곳인지 두세 문장 개관")
    industry_environment: list[str] = Field(
        description="산업·규제 환경 — 경쟁 구도, 규제, 거시 요인 한 줄씩"
    )
    business_nature: list[str] = Field(
        description="사업의 성격 — 주요 제품/서비스, 수익 구조, 주요 고객·시장 한 줄씩"
    )
    financial_highlights: list[str] = Field(
        description="재무 하이라이트 — 자료에서 확인된 수치·추세만, 수치에 기간 병기"
    )
    recent_issues: list[IssueNote] = Field(
        description="최근 이슈 — 소송·규제·경영진 변동·구조조정 등 감사 관련 사건"
    )
    risk_candidates: list[RiskCandidate] = Field(
        description="유의적 위험 후보 — 수집 정보가 시사하는 왜곡표시 위험"
    )
    understanding_gaps: list[str] = Field(
        description="공개 자료로 확인 못 한 것 — 감사팀이 추가로 확보해야 할 이해 항목"
    )
    overall: str = Field(description="감사 착수 관점의 한눈 요약 두세 문장")

    @field_validator("recent_issues", mode="before")
    @classmethod
    def _coerce_str_issues(cls, value):
        """약한 모델이 문자열 리스트를 내는 경우를 승격한다."""
        if isinstance(value, list):
            return [
                {"issue": v[:80], "detail": v} if isinstance(v, str) else v
                for v in value
            ]
        return value

    @field_validator("risk_candidates", mode="before")
    @classmethod
    def _coerce_str_risks(cls, value):
        if isinstance(value, list):
            return [
                {"risk": v[:80], "rationale": v, "affected_area": "(미표기)"}
                if isinstance(v, str)
                else v
                for v in value
            ]
        return value


class ProfilerState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    question: str
    mode: str  # "profile" | "chat" — triage 분기 결과
    company: str
    focus: str
    provided_urls: list  # 사용자가 메시지에 적은 URL
    sources: list  # [{"url", "title"}] 추출 대상으로 확정된 자료
    extracts: list  # [{"url", "text"}] 자료별 추출 결과
    profile: dict
    attempts: int
    error: str | None


def _render_profile(
    company: str,
    profile: CompanyProfile,
    sources: list[dict],
    focus: str = "",
) -> str:
    def _bullets(items: list[str]) -> list[str]:
        return [f"- {item}" for item in items] or ["- (확인된 내용 없음)"]

    lines = [
        f"# 감사 착수 전 기업이해 브리핑 — {company}",
        *([f"*요청 초점: {focus}*"] if focus else []),
        "",
        "## ① 기업 개관",
        profile.company_overview,
        "",
        "## ② 산업·규제 환경",
        *_bullets(profile.industry_environment),
        "",
        "## ③ 사업의 성격 (영업·수익구조·주요 고객)",
        *_bullets(profile.business_nature),
        "",
        "## ④ 재무 하이라이트",
        *_bullets(profile.financial_highlights),
        "",
        "## ⑤ 최근 이슈",
    ]
    if profile.recent_issues:
        for note in profile.recent_issues:
            line = f"- **{note.issue}**\n  {note.detail}"
            if note.source:
                line += f"\n  출처: {note.source}"
            lines.append(line)
    else:
        lines.append("- (확인된 내용 없음)")
    lines += [
        "",
        "## ⑥ 유의적 위험 후보",
    ]
    if profile.risk_candidates:
        for risk in profile.risk_candidates:
            line = (
                f"- **{risk.risk}**\n  _영향: {risk.affected_area}_\n  {risk.rationale}"
            )
            if risk.citation:
                line += f"\n  근거: {risk.citation}"
            lines.append(line)
    else:
        lines.append("- (확인된 내용 없음)")
    lines += [
        "",
        "## ⑦ 추가로 확인해야 할 이해 항목",
        *_bullets(profile.understanding_gaps),
        "",
        "## ⑧ 한눈 요약",
        profile.overall,
        "",
        "## 조사 자료",
        *(
            [
                f"- {s.get('title') or '(제목 없음)'} — {s['url']}"
                for s in sources
            ]
            or ["- (없음)"]
        ),
        "",
    ]
    cited = [r for r in profile.risk_candidates if r.citation_cid]
    if cited:
        seen: set[str] = set()
        lines.append("## 근거 목록")
        for risk in cited:
            if risk.citation_cid in seen:
                continue
            seen.add(risk.citation_cid)
            lines.append(f"- {risk.citation} — `{risk.citation_cid}`")
        lines.append("")
    lines += [
        "---",
        "*이 브리핑은 조회 시점의 공개 웹 자료만으로 작성된 기업이해 활동 "
        "보조 자료이며 감사증거가 아닙니다. 유의적 위험 후보는 후속 위험 "
        "평가 절차로 확정해야 하고, 기준서 근거는 자동 검색으로 원문을 "
        "재확인한 문단(cid 병기)만 인용합니다.*",
    ]
    return "\n".join(lines)


class ProfilerNodes:
    def __init__(self, model, searcher=None, scraper=None) -> None:
        self.model = model
        self.searcher = searcher or JinaSearcher(ScraperConfig())
        self.scraper = scraper or build_scraper_graph(model)

    def triage(self, state: ProfilerState) -> dict[str, Any]:
        """브리핑 요청인지 일반 대화인지 분기한다 (explainer/reviewer와 동일 패턴)."""
        emit("triaging", "요청 유형을 판단하는 중")
        texts = human_texts_newest_first(state)
        question = texts[0].strip() if texts else ""
        if not question:
            return {"question": question, "mode": "chat", "error": None}
        context = conversation_context(state)
        context_part = f"이전 대화:\n{context}\n" if context else ""
        try:
            decider = self.model.with_structured_output(TriageDecision)
            result = decider.invoke(
                [
                    SystemMessage(
                        content=(
                            "당신은 기업이해 에이전트의 라우터입니다. 사용자 "
                            "메시지가 특정 회사에 대한 기업이해·사전 조사·브리핑 "
                            "요청이면 profile, 인사·사용법 질문·기능 문의·이전 "
                            "브리핑에 대한 후속 설명처럼 새 조사가 필요 없는 "
                            "대화면 chat으로 분류하세요. 다른 회사나 같은 회사의 "
                            "브리핑을 새로 요청하면 profile입니다."
                        )
                    ),
                    HumanMessage(content=f"{context_part}메시지: {question}"),
                ]
            )
            decision = (
                result
                if isinstance(result, TriageDecision)
                else TriageDecision.model_validate(result)
            )
            mode = decision.mode
        except Exception:
            # 폴백 휴리스틱: URL이 있거나 조사 동사가 보이면 profile
            keywords = ("브리핑", "조사", "이해", "분석", "검토", "알려줘", "정리")
            has_signal = bool(_URL_RE.search(question)) or any(
                k in question for k in keywords
            )
            mode = "profile" if has_signal else "chat"
        return {"question": question, "mode": mode, "error": None}

    def route_triage(self, state: ProfilerState) -> Literal["profile", "chat"]:
        return "chat" if state.get("mode") == "chat" else "profile"

    async def chat(self, state: ProfilerState) -> dict[str, Any]:
        """새 조사가 필요 없는 일반 대화 — 기준서 도구를 쥔 미니 ReAct."""
        emit("chatting", "일반 대화로 응답하는 중")
        standards = await get_standards_tools()
        tools_by_name = {t.name: t for t in standards}
        citation_rule = (
            (
                "대화 맥락에 이미 확정돼 있는 기준서 인용(근거·근거 목록의 "
                "표기와 cid)은 도구 없이 그대로 옮겨 설명해도 됩니다. 새로운 "
                "기준서 근거가 필요하면 standards_search로 찾고, 인용을 확정할 "
                "문단은 standards_get_paragraph(cid)로 원문을 확인한 뒤 표기와 "
                "cid를 병기해 인용하세요. 도구로 확인하지 못한 번호는 인용하지 "
                f"마세요. 도구 호출은 총 {MAX_CHAT_TOOL_CALLS}회 이내입니다."
            )
            if standards
            else (
                "대화 맥락에 없는 기준서 번호를 새로 인용하지 마세요 — 지금은 "
                "원문 확인 도구가 연결돼 있지 않습니다."
            )
        )
        messages: list = [
            SystemMessage(
                content=(
                    "당신은 '기업이해 Agent'입니다 — 감사 착수 전에 회계사가 "
                    "확보해야 할 회사 이해(감사기준서 315의 이해 활동)를 공개 웹 "
                    "자료로 보조하는 모드입니다. 지금 요청은 새 조사가 필요 없는 "
                    "일반 대화로 분류되었습니다. 한국어로 간결히 답하고, 사용법을 "
                    "물으면 안내하세요: 회사명을 알려주면 브리핑을 만들며, 회사 "
                    "소개·IR·뉴스 페이지 URL을 함께 주면 그 자료를 우선 "
                    "조사합니다. 이전 브리핑에 대한 후속 질문이면 대화 맥락으로 "
                    f"답하세요. {citation_rule}"
                )
            ),
            *state["messages"],
        ]
        model = self.model
        if standards:
            try:
                model = self.model.bind_tools(standards)
            except Exception:
                model = self.model

        response = None
        calls_used = 0
        for round_no in range(MAX_CHAT_TOOL_ROUNDS + 1):
            response = await model.ainvoke(messages)
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls or round_no == MAX_CHAT_TOOL_ROUNDS:
                break
            messages.append(response)
            for call in tool_calls:
                if calls_used >= MAX_CHAT_TOOL_CALLS:
                    messages.append(
                        ToolMessage(
                            content="(도구 호출 상한 도달 — 지금까지의 정보로 답하세요)",
                            tool_call_id=call["id"],
                        )
                    )
                    continue
                emit("chatting", f"기준서 확인: {call['name']}")
                tool = tools_by_name.get(call["name"])
                try:
                    result = (
                        await tool_text(tool, call["args"])
                        if tool
                        else f"오류: 알 수 없는 도구 {call['name']}"
                    )
                except Exception as exc:
                    result = f"오류: {exc}"
                calls_used += 1
                messages.append(
                    ToolMessage(
                        content=str(result)[:MAX_TOOL_RESULT_CHARS],
                        tool_call_id=call["id"],
                    )
                )
        emit("complete", "응답 완료")
        return {"messages": [response], "error": None}

    def plan(self, state: ProfilerState) -> dict[str, Any]:
        """회사명·초점을 파싱한다. URL 추출은 LLM이 아니라 코드가 한다."""
        emit("planning", "조사 대상을 파악하는 중")
        question = state.get("question", "")
        provided_urls = list(dict.fromkeys(_URL_RE.findall(question)))[:MAX_SOURCES]
        company, focus = "", ""
        try:
            planner = self.model.with_structured_output(ProfilePlan)
            result = planner.invoke(
                [
                    SystemMessage(
                        content=(
                            "사용자 메시지에서 조사 대상 회사명과 특별히 궁금해하는 "
                            "초점을 추출하세요. 회사명을 식별할 수 없으면 빈 "
                            "문자열로 두세요. URL은 추출하지 마세요."
                        )
                    ),
                    HumanMessage(content=question),
                ]
            )
            plan = (
                result
                if isinstance(result, ProfilePlan)
                else ProfilePlan.model_validate(result)
            )
            company, focus = plan.company.strip(), plan.focus.strip()
        except Exception:
            pass
        if not company and not provided_urls:
            return {
                "error": (
                    "조사할 회사를 알 수 없습니다. 회사명을 알려주시거나 조사할 "
                    "공개 자료 URL을 함께 적어 주세요."
                )
            }
        return {
            "company": company or "(회사명 미상)",
            "focus": focus,
            "provided_urls": provided_urls,
            "attempts": 0,
            "error": None,
        }

    def route_plan(self, state: ProfilerState) -> Literal["gather", "fail"]:
        return "fail" if state.get("error") else "gather"

    def gather(self, state: ProfilerState) -> dict[str, Any]:
        """조사 자료 URL을 확정한다 — 사용자 제공 URL 우선, 검색으로 보충."""
        sources: list[dict] = [
            {"url": url, "title": ""} for url in state.get("provided_urls", [])
        ]
        company = state.get("company", "")
        remaining = MAX_SOURCES - len(sources)
        if remaining > 0 and self.searcher.available and company != "(회사명 미상)":
            queries = [
                f"{company} 기업 개요 사업 주요 제품",
                f"{company} 재무 실적 매출 영업이익",
                f"{company} 최근 뉴스 이슈 소송 규제",
            ]
            if state.get("focus"):
                queries.insert(0, f"{company} {state['focus']}")
            seen_domains = {urlparse(s["url"]).netloc for s in sources}
            for query in queries:
                if remaining <= 0:
                    break
                emit("searching", f"웹 검색: {query}")
                try:
                    hits = self.searcher.search(query, max_results=MAX_SEARCH_RESULTS)
                except Exception:
                    continue
                for hit in hits:
                    domain = urlparse(hit.url).netloc
                    # 같은 도메인 반복 대신 자료원을 다양화한다
                    if domain in seen_domains:
                        continue
                    sources.append({"url": hit.url, "title": hit.title})
                    seen_domains.add(domain)
                    remaining -= 1
                    if remaining <= 0:
                        break
        if not sources:
            return {"error": NO_SOURCE_MESSAGE}
        emit("gathering", f"조사 자료 {len(sources)}건 확정")
        return {"sources": sources, "error": None}

    def route_gather(self, state: ProfilerState) -> Literal["extract", "fail"]:
        return "fail" if state.get("error") else "extract"

    def extract(self, state: ProfilerState) -> dict[str, Any]:
        """확정된 자료를 웹 추출 서브그래프로 읽는다 (자료당 LLM 1회)."""
        company = state.get("company", "")
        instruction = (
            f"'{company}' 기업이해 목적의 정보 추출: 사업·주요 제품, 수익 구조, "
            "주요 고객·시장, 재무 성과·추세(수치는 기간 병기), 최근 이슈·소송·"
            "규제·경영진 변동 등 위험 신호. 페이지에서 확인되는 사실만 간결히."
        )
        extracts: list[dict] = []
        total = len(state["sources"])
        for index, source in enumerate(state["sources"], start=1):
            emit("extracting", f"자료 추출 중 ({index}/{total}): {source['url']}")
            try:
                result = self.scraper.invoke(
                    {"url": source["url"], "instruction": instruction}
                )
            except Exception as exc:
                extracts.append(
                    {"url": source["url"], "text": f"(추출 실패: {exc})"}
                )
                continue
            if result.get("error") or not result.get("result"):
                extracts.append(
                    {
                        "url": source["url"],
                        "text": f"(추출 실패: {result.get('error') or '빈 결과'})",
                    }
                )
                continue
            text = result["result"]
            if not isinstance(text, str):
                text = str(text)
            extracts.append({"url": source["url"], "text": text[:MAX_EXTRACT_CHARS]})
        usable = [e for e in extracts if not e["text"].startswith("(추출 실패")]
        if not usable:
            return {
                "extracts": extracts,
                "error": "확정된 자료를 하나도 읽지 못했습니다. URL이 공개 페이지인지 확인해 주세요.",
            }
        return {"extracts": extracts, "error": None}

    def route_extract(self, state: ProfilerState) -> Literal["analyze", "fail"]:
        return "fail" if state.get("error") else "analyze"

    def analyze(self, state: ProfilerState) -> dict[str, Any]:
        attempt = state.get("attempts", 0) + 1
        emit("analyzing", "수집 자료로 기업 프로파일을 작성하는 중", attempt=attempt)
        company = state.get("company", "")
        evidence = "\n\n".join(
            f"[자료 {i}: {e['url']}]\n{e['text']}"
            for i, e in enumerate(state["extracts"], start=1)
        )
        analyzer = self.model.with_structured_output(CompanyProfile)
        try:
            result = analyzer.invoke(
                [
                    SystemMessage(
                        content=(
                            "당신은 회계법인의 시니어로서 감사 착수 전 기업이해 "
                            "브리핑을 작성합니다. 제공된 수집 자료만 사용하고, "
                            "자료에 없는 내용을 추정하지 마세요. 수치는 기간을 "
                            "병기하고, 자료 간 상충은 그대로 적으세요. 관점: "
                            "모든 항목을 '이 사실이 재무제표 왜곡표시 위험에 "
                            "어떤 함의를 갖는가'로 연결하세요 — 특히 "
                            "risk_candidates에는 영향받는 계정과 경영진 주장을 "
                            "명시하고, 위험 평가의 근거가 될 감사기준 검색어를 "
                            "standards_query에 채우세요 (원문 확인과 인용 확정은 "
                            "시스템이 수행합니다). 공개 자료로 확인 못 한 이해 "
                            "항목은 understanding_gaps에 남기세요."
                        )
                    ),
                    HumanMessage(
                        content=(
                            f"조사 대상: {company}\n"
                            + (f"요청 초점: {state['focus']}\n" if state.get("focus") else "")
                            + f"\n[수집 자료]\n{evidence}"
                        )
                    ),
                ]
            )
            profile = (
                result
                if isinstance(result, CompanyProfile)
                else CompanyProfile.model_validate(result)
            )
            return {"profile": profile.model_dump(), "attempts": attempt, "error": None}
        except Exception as exc:  # 구조화 출력 실패·일시 오류 → 재시도 후 fail
            return {"attempts": attempt, "error": f"프로파일 생성 실패 — {exc}"}

    def route_analyze(self, state: ProfilerState) -> Literal["cite", "retry", "fail"]:
        if not state.get("error"):
            return "cite"
        if state.get("attempts", 0) < MAX_ANALYZE_ATTEMPTS:
            return "retry"
        return "fail"

    async def cite(self, state: ProfilerState) -> dict[str, Any]:
        """위험 후보의 감사기준 근거를 검색·재확인해 확정한다 (LLM 없음, 결정적)."""
        emit("citing", "위험 후보의 기준서 근거를 검색·확정하는 중")
        profile = CompanyProfile.model_validate(state["profile"])
        tools = {t.name: t for t in await get_standards_tools()}
        search = tools.get("standards_search")
        get_para = tools.get("standards_get_paragraph")
        if search is None or get_para is None:
            return {"profile": profile.model_dump()}

        targets = [r for r in profile.risk_candidates if r.standards_query][
            :MAX_CITED_RISKS
        ]

        async def _cite_one(risk: RiskCandidate) -> bool:
            resolved = await resolve_citation(
                search, get_para, risk.standards_query, risk.source_hint
            )
            if resolved is None:
                return False
            risk.citation, risk.citation_cid = resolved
            return True

        cited = sum(await asyncio.gather(*(_cite_one(r) for r in targets)))
        if cited:
            emit("citing", f"기준서 근거 {cited}건 확정")
        return {"profile": profile.model_dump()}

    def report(self, state: ProfilerState) -> dict[str, Any]:
        emit("reporting", "기업이해 브리핑을 작성하는 중")
        profile = CompanyProfile.model_validate(state["profile"])
        text = _render_profile(
            state.get("company", ""),
            profile,
            state.get("sources", []),
            state.get("focus", ""),
        )
        emit("complete", "기업이해 브리핑 완료")
        return {"messages": [AIMessage(content=text)], "error": None}

    def fail(self, state: ProfilerState) -> dict[str, Any]:
        message = state.get("error") or "기업이해 브리핑에 실패했습니다."
        emit("failed", message)
        return {"messages": [AIMessage(content=f"오류: {message}")]}


def build_profiler_graph(nodes: ProfilerNodes):
    """기업이해 그래프를 조립한다 — 노드 묶음은 테스트용으로 주입 가능."""
    builder = StateGraph(ProfilerState)
    builder.add_node("triage", nodes.triage)
    builder.add_node("chat", nodes.chat)
    builder.add_node("plan", nodes.plan)
    builder.add_node("gather", nodes.gather)
    builder.add_node("extract", nodes.extract)
    builder.add_node("analyze", nodes.analyze)
    builder.add_node("cite", nodes.cite)
    builder.add_node("report", nodes.report)
    builder.add_node("fail", nodes.fail)

    builder.add_edge(START, "triage")
    builder.add_conditional_edges(
        "triage", nodes.route_triage, {"profile": "plan", "chat": "chat"}
    )
    builder.add_edge("chat", END)
    builder.add_conditional_edges(
        "plan", nodes.route_plan, {"gather": "gather", "fail": "fail"}
    )
    builder.add_conditional_edges(
        "gather", nodes.route_gather, {"extract": "extract", "fail": "fail"}
    )
    builder.add_conditional_edges(
        "extract", nodes.route_extract, {"analyze": "analyze", "fail": "fail"}
    )
    builder.add_conditional_edges(
        "analyze",
        nodes.route_analyze,
        {"cite": "cite", "retry": "analyze", "fail": "fail"},
    )
    builder.add_edge("cite", "report")
    builder.add_edge("report", END)
    builder.add_edge("fail", END)
    return builder.compile()


async def profiler(config: RunnableConfig):
    """요청 config로 모델을 정해 기업이해 그래프를 조립한다 (langgraph 서버가 호출)."""
    model_spec = (config.get("configurable") or {}).get("model", DEFAULT_MODEL)
    return build_profiler_graph(ProfilerNodes(resolve_model(model_spec)))
