"""ExcelBrief 에이전트 그래프 — langgraph.json 진입점.

모델 라우팅: config["configurable"]["model"] 값으로 요청마다 모델을 결정한다.
  - "anthropic:<model-id>"     → Anthropic API (기본, 프롬프트 캐싱)
  - "openai:<model-id>"        → OpenAI API
  - "google_genai:<model-id>"  → Google Gemini API
  - "hf:<org/model>"           → HF Inference Providers 라우터 (오픈모델 서버리스,
                                  OpenAI 호환, HF_TOKEN 인증 — GPU Space 불필요)
  - "local:<model-name>"       → OpenAI 호환 로컬 서버 (Ollama/vLLM)

UI의 모델 드롭다운은 ui/src/lib/models.ts 레지스트리를 /api/models로 받아
벤더 API 키가 설정된 모델만 노출한다 — 레지스트리 수정 시 그 파일도 갱신.
"""

import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.runnables import RunnableConfig

from agent.mcp_client import get_standards_tools
from agent.prompts import SYSTEM_PROMPT
from agent.tools.documents import DOCUMENT_TOOLS
from agent.tools.excel import EXCEL_TOOLS
from agent.tools.table import TABLE_TOOLS

load_dotenv()

DEFAULT_MODEL = "anthropic:claude-sonnet-5"


# max_tokens 8192: 기본 4096이면 조서 해설이 근거 목록 전에 절단됨 (Phase 5 채점에서 실증).
# init_chat_model의 표준 파라미터라 벤더별 명칭(max_output_tokens 등)으로 자동 매핑된다.
MAX_TOKENS = 8192


def resolve_model(spec: str):
    """모델 문자열 접두사로 제공자를 분기한다. 라우트 추가는 이 함수만 수정."""
    if spec.startswith("local:"):
        return init_chat_model(
            f"openai:{spec.removeprefix('local:')}",
            base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1"),
            api_key=os.environ.get("LOCAL_LLM_API_KEY", "not-needed"),
            max_tokens=MAX_TOKENS,
        )
    if spec.startswith("hf:"):
        # HF_INFERENCE_TOKEN: "Inference Providers 호출" 권한이 있는 fine-grained
        # 토큰 (CLI용 write 토큰 HF_TOKEN에는 이 권한이 없음 — 403 실측)
        return init_chat_model(
            f"openai:{spec.removeprefix('hf:')}",
            base_url="https://router.huggingface.co/v1",
            api_key=os.environ.get("HF_INFERENCE_TOKEN") or os.environ.get("HF_TOKEN"),
            max_tokens=MAX_TOKENS,
        )
    if spec.startswith("anthropic:"):
        # output_version="v1": 스트리밍 병합 시 thinking 블록이 signature만 남아
        # 다음 턴 재전송에서 400(thinking.thinking Field required)이 나는 문제 회피 —
        # 표준 콘텐츠 블록으로 왕복 직렬화한다.
        # cache_control: 요청 최상위 파라미터로 자동 프롬프트 캐싱 활성화 —
        # 시스템 프롬프트+도구 정의(~5.7k 토큰)가 ReAct 라운드마다 전액 재과금되던
        # 것을 캐시 (LangSmith 실측: 전 호출 cache_read=0이었음).
        return init_chat_model(
            spec,
            output_version="v1",
            max_tokens=MAX_TOKENS,
            model_kwargs={"cache_control": {"type": "ephemeral"}},
        )
    if spec.startswith("google_genai:"):
        # 키 이름 관용 지원: langchain-google-genai는 GOOGLE_API_KEY만 찾지만
        # Google AI Studio가 발급 안내하는 이름은 GEMINI_API_KEY다.
        return init_chat_model(
            spec,
            max_tokens=MAX_TOKENS,
            api_key=os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY"),
        )
    # openai:<id> 등 — init_chat_model 표준 접두사에 위임
    return init_chat_model(spec, max_tokens=MAX_TOKENS)


async def graph(config: RunnableConfig):
    """요청 config를 받아 에이전트를 조립하는 팩토리 (langgraph 서버가 호출)."""
    model_spec = (config.get("configurable") or {}).get("model", DEFAULT_MODEL)
    tools = EXCEL_TOOLS + TABLE_TOOLS + DOCUMENT_TOOLS + list(await get_standards_tools())
    return create_agent(
        model=resolve_model(model_spec),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
