"""Tavily 웹 검색 — LLM용으로 추출된 본문 스니펫이 실려 오는 검색 API.

s.jina.ai(메타데이터만)와 달리 결과의 content 필드에 관련 본문 발췌가
포함된다 — 검색만으로도 증거가 되고, 정독이 필요한 URL만 웹 추출로
넘기면 된다. TAVILY_API_KEY가 없으면 available=False (호출자 강등).
topic="news"는 최근 기사 위주로 검색한다.
"""

import json
import os

import httpx

from .config import ScraperConfig
from .jina_search import SearchHit

API_URL = "https://api.tavily.com/search"


class TavilySearcher:
    def __init__(self, config: ScraperConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key if api_key is not None else os.environ.get("TAVILY_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(
        self, query: str, max_results: int = 5, topic: str = "general"
    ) -> list[SearchHit]:
        if not self.available:
            raise ValueError("TAVILY_API_KEY is required for web search")
        body = {"query": query, "max_results": max_results, "topic": topic}
        if topic == "news":
            body["days"] = 90
        with httpx.Client(timeout=self.config.jina_timeout_seconds) as client:
            response = client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": self.config.user_agent,
                },
                content=json.dumps(body),
            )
            response.raise_for_status()
            payload = response.json()

        hits = []
        for item in payload.get("results") or []:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    snippet=(item.get("content") or "").strip(),
                )
            )
            if len(hits) >= max_results:
                break
        return hits
