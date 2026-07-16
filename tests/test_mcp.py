"""Phase 2 통합 테스트 — auditPaper_MCP 연결 (실제 HF Space 대상)."""

import os

import pytest

from agent.graph import graph
from agent.mcp_client import get_standards_tools

EXPECTED_TOOLS = {"standards_search", "standards_get_paragraph", "standards_define_terms"}


async def test_standards_tools_exposed():
    tools = await get_standards_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"누락된 도구: {EXPECTED_TOOLS - names}"


async def test_get_paragraph_returns_cid():
    tools = {t.name: t for t in await get_standards_tools()}
    result = await tools["standards_get_paragraph"].ainvoke({"cid": "KIFRS::1115::31"})
    assert "KIFRS::1115::31" in str(result)


async def test_invalid_args_return_error_text_not_exception():
    """서버측 검증 오류는 런을 죽이지 않고 '오류:' 텍스트로 돌아와야 한다.

    실측 사고(2026-07-16): gpt-oss-120b가 source_type에 리스트 대신 문자열을
    넘겨 _MCPToolExecutionError로 스레드가 사망 → UI 크래시(React #185)까지 번짐.
    """
    tools = {t.name: t for t in await get_standards_tools()}
    result = await tools["standards_search"].ainvoke(
        {"query": "감사전략계획서", "source_type": "감사기준"}  # 리스트여야 함
    )
    text = str(result)
    assert text.startswith("오류:"), f"예외 대신 오류 텍스트가 와야 함: {text[:200]}"
    assert "다시 호출" in text


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="API 키 없음")
async def test_revenue_five_steps_citation():
    """완료 기준: 기준서 질문에 cid 인용이 포함된 답변."""
    g = await graph({"configurable": {}})
    result = await g.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "K-IFRS 수익 인식 5단계 모형의 근거 문단을 cid와 함께 알려줘.",
                }
            ]
        },
        config={"recursion_limit": 40},
    )
    content = str(result["messages"][-1].content)
    assert "KIFRS::1115" in content, f"cid 인용 누락: {content[:300]}"
