"""agent 그래프 대화 요약 미들웨어 — 임계 선택 로직과 팩토리 조립 검증."""

import os

import pytest
from langchain_core.messages import HumanMessage

from agent.graph import graph, resolve_model, summarization_middleware


def test_fraction_trigger_with_profile():
    """프로파일이 있는 모델은 fraction 임계를 쓴다 (haiku 실측: 200k 등재)."""
    model = resolve_model("anthropic:claude-haiku-4-5-20251001")
    mw = summarization_middleware(model, "anthropic:claude-haiku-4-5-20251001")
    assert any("fraction" in clause for clause in mw._trigger_clauses)


def test_absolute_fallback_without_profile():
    """프로파일 미보유(최신 claude-sonnet-5)는 제공자별 절대값으로 폴백한다."""
    model = resolve_model("anthropic:claude-sonnet-5")
    mw = summarization_middleware(model, "anthropic:claude-sonnet-5")
    clauses = mw._trigger_clauses
    assert not any("fraction" in clause for clause in clauses)
    assert any(clause.get("tokens") == 150_000 for clause in clauses)


def test_absolute_fallback_local_is_conservative():
    model = resolve_model("local:qwen3:8b")
    mw = summarization_middleware(model, "local:qwen3:8b")
    assert any(clause.get("tokens") == 24_000 for clause in mw._trigger_clauses)


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="실 API 키 필요"
)
async def test_agent_factory_builds_with_middleware(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKPAPERS_DIR", str(tmp_path))
    compiled = await graph({"configurable": {"model": "anthropic:claude-haiku-4-5-20251001"}})
    result = await compiled.ainvoke(
        {"messages": [HumanMessage(content="안녕! 한 문장으로 인사만 해줘.")]}
    )
    assert result["messages"][-1].content  # 미들웨어 장착 상태로 정상 응답
