"""auditPaper_MCP 연결 — 기준서 RAG 도구(standards_*) 3종을 LangChain 도구로 노출.

전송 방식은 MCP_TRANSPORT로 선택:
  - "http" (기본): HF Space 상시 배포 + Bearer 토큰 — 배포·로컬 개발 공통 경로
  - "stdio": 같은 머신의 auditPaper_MCP를 직접 기동 — 로컬 개발 옵션
"""

import logging
import os

from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.citations import attach_displays

logger = logging.getLogger(__name__)


def _server_config() -> dict:
    if os.environ.get("MCP_TRANSPORT", "http") == "stdio":
        mcp_dir = os.environ["AUDITPAPER_MCP_DIR"]
        return {
            "transport": "stdio",
            "command": os.path.join(mcp_dir, ".venv/bin/python"),
            "args": ["-m", "server.mcp_server"],
            "cwd": mcp_dir,
            "env": {
                "QDRANT_URL": os.environ.get("QDRANT_URL", ""),
                "QDRANT_API_KEY": os.environ.get("QDRANT_API_KEY", ""),
            },
        }
    return {
        "transport": "streamable_http",
        "url": os.environ["MCP_HTTP_URL"],
        "headers": {"Authorization": f"Bearer {os.environ['MCP_AUTH_TOKEN']}"},
        # HF Space 기동 직후 임베딩 모델 로드로 첫 standards_search가 느릴 수 있음
        "timeout": 30,
        "sse_read_timeout": 120,
    }


def _process_content(content):
    """도구 결과 콘텐츠(문자열 또는 블록 리스트)에 display를 주입한다."""
    if isinstance(content, str):
        return attach_displays(content)
    if isinstance(content, list):
        return [
            {**block, "text": attach_displays(block["text"])}
            if isinstance(block, dict) and block.get("type") == "text"
            else block
            for block in content
        ]
    return content


def _with_displays(tool) -> StructuredTool:
    """도구 결과의 각 문단에 표기 문자열(display)을 덧붙이는 래퍼 (system_design 5.1).

    원본 도구의 coroutine을 직접 호출한다 — tool.ainvoke를 거치면 래퍼와 원본이
    각각 tool run으로 기록되어 LangSmith 트레이스가 이중으로 남는다.
    MCP 어댑터 도구는 content_and_artifact 규약이라 (content, artifact) 튜플을 다룬다.
    """

    async def _run(**kwargs):
        result = await tool.coroutine(**kwargs)
        if isinstance(result, tuple) and len(result) == 2:
            content, artifact = result
            return _process_content(content), artifact
        return _process_content(result)

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        response_format=tool.response_format,
    )


_tools_cache: list | None = None


async def get_standards_tools() -> list:
    """standards_* 도구 목록을 반환한다. 프로세스당 1회 조회 후 캐시.

    MCP 서버에 닿지 못하면 빈 목록을 반환해 에이전트가 기준 인용 없이도
    동작하게 한다 (실패는 캐시하지 않으므로 다음 요청에서 재시도).
    """
    global _tools_cache
    if _tools_cache is None:
        try:
            client = MultiServerMCPClient({"auditpaper-standards": _server_config()})
            _tools_cache = [_with_displays(t) for t in await client.get_tools()]
            logger.info("auditPaper_MCP 도구 %d종 연결", len(_tools_cache))
        except Exception:
            logger.exception("auditPaper_MCP 연결 실패 — 기준서 도구 없이 진행")
            return []
    return _tools_cache
