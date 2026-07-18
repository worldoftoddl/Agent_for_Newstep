"""Jina 웹 검색 — s.jina.ai로 검색 결과(제목·URL·요약)를 받아온다.

r.jina.ai(reader)와 달리 검색은 무키 모드가 없어 JINA_API_KEY가 필수다.
키가 없으면 available=False — 호출자는 검색 없이 강등 동작해야 한다.
X-Respond-With: no-content로 본문 없이 메타데이터만 받아 토큰을 아낀다
(본문 취득은 JinaFetcher/web_extract 몫).
"""

import json
import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .config import ScraperConfig


@dataclass(frozen=True, slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class JinaSearcher:
    def __init__(self, config: ScraperConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key if api_key is not None else os.environ.get("JINA_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(
        self, query: str, max_results: int = 5, topic: str = "general"
    ) -> list[SearchHit]:
        # topic은 검색 제공자 공통 시그니처용 — Jina는 구분이 없어 무시한다
        if not self.available:
            raise ValueError("JINA_API_KEY is required for web search")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-Respond-With": "no-content",
            "User-Agent": self.config.user_agent,
        }
        with httpx.Client(
            timeout=self.config.jina_timeout_seconds, follow_redirects=True
        ) as client:
            with client.stream(
                "GET", f"https://s.jina.ai/?q={quote(query)}", headers=headers
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.config.max_response_bytes:
                        raise ValueError("Response exceeds configured size limit")

        payload = json.loads(bytes(body).decode("utf-8"))
        hits = []
        for item in payload.get("data") or []:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                SearchHit(
                    title=(item.get("title") or "").strip(),
                    url=url,
                    snippet=(item.get("description") or "").strip(),
                )
            )
            if len(hits) >= max_results:
                break
        return hits
