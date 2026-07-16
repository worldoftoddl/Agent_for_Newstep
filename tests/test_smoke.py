"""Phase 1 스모크 테스트 — 그래프 컴파일·모델 라우팅·더미 대화 1턴."""

import os

import pytest

from agent.graph import DEFAULT_MODEL, graph, resolve_model


async def test_graph_compiles():
    g = await graph({"configurable": {}})
    assert hasattr(g, "invoke") and hasattr(g, "stream")


def test_resolve_model_default_is_anthropic():
    from langchain_anthropic import ChatAnthropic

    model = resolve_model(DEFAULT_MODEL)
    assert isinstance(model, ChatAnthropic)


def test_resolve_model_local_routes_to_openai_compatible():
    from langchain_openai import ChatOpenAI

    model = resolve_model("local:qwen3-8b")
    assert isinstance(model, ChatOpenAI)
    assert "localhost" in str(model.openai_api_base)


def test_resolve_model_openai(monkeypatch):
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model = resolve_model("openai:gpt-5.2")
    assert isinstance(model, ChatOpenAI)
    assert model.openai_api_base is None  # local:과 달리 공식 엔드포인트


def test_resolve_model_hf_router(monkeypatch):
    from langchain_openai import ChatOpenAI

    monkeypatch.setenv("HF_INFERENCE_TOKEN", "hf-test")
    model = resolve_model("hf:Qwen/Qwen3.6-27B")
    assert isinstance(model, ChatOpenAI)
    assert "router.huggingface.co" in str(model.openai_api_base)


def test_resolve_model_google(monkeypatch):
    from langchain_google_genai import ChatGoogleGenerativeAI

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    model = resolve_model("google_genai:gemini-flash-latest")
    assert isinstance(model, ChatGoogleGenerativeAI)
    # max_tokens 표준 파라미터가 벤더 명칭으로 매핑되는지 확인
    assert model.max_output_tokens == 8192


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="API 키 없음")
async def test_dummy_turn():
    g = await graph({"configurable": {}})
    result = await g.ainvoke(
        {"messages": [{"role": "user", "content": "한 문장으로 자기소개해줘"}]}
    )
    assert result["messages"][-1].content
