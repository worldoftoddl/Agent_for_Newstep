"""Runtime configuration, kept outside graph state.

이식 시 변경: 모델 지정(model 필드·SCRAPER_MODEL env)은 제거 — 모델은 호출자가
resolve_model로 라우팅한 인스턴스를 서브그래프에 주입한다.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    timeout_seconds: float = 20.0
    max_response_bytes: int = 2_000_000
    max_redirects: int = 5
    chunk_chars: int = 12_000
    chunk_overlap: int = 500
    max_chunks: int = 5  # 청크당 LLM 1회 — 비용 관문 (초과분은 버리고 고지)
    max_extraction_attempts: int = 2
    user_agent: str = "AgentForNewstep/0.1"
    use_jina: bool = True  # Jina Reader를 1차 fetch 경로로 (실패 시 직접 fetch 폴백)
    jina_base_url: str = "https://r.jina.ai/"
    jina_timeout_seconds: float = 40.0  # Jina 경유 지연(~수 초) 감안
    max_single_pass_chars: int = 50_000  # Jina 경로 통짜 추출 시 본문 클립 상한
