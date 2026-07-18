"""Jina Reader fetcher — fetch·JS 렌더링·본문 정리를 r.jina.ai에 위임한다.

JSON 모드(Accept: application/json)로 받아 data.content(마크다운)와
data.url(최종 URL)만 사용한다. JINA_API_KEY가 없으면 무키 모드(20 RPM)로
동작하므로 키는 선택 사항이다. 바이트 상한은 직접 fetcher와 같은
max_response_bytes를 적용한다.
"""

import json
import os

import httpx

from .config import ScraperConfig
from .http_fetcher import FetchResult


class JinaFetcher:
    def __init__(self, config: ScraperConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key if api_key is not None else os.environ.get("JINA_API_KEY", "")

    def fetch(self, url: str) -> FetchResult:
        headers = {
            "Accept": "application/json",
            "X-Return-Format": "markdown",
            "User-Agent": self.config.user_agent,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(
            timeout=self.config.jina_timeout_seconds, follow_redirects=True
        ) as client:
            with client.stream(
                "GET", self.config.jina_base_url + url, headers=headers
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.config.max_response_bytes:
                        raise ValueError("Response exceeds configured size limit")

        payload = json.loads(bytes(body).decode("utf-8"))
        data = payload.get("data") or {}
        content = (data.get("content") or "").strip()
        if not content:
            raise ValueError("Jina Reader returned no content")
        return FetchResult(
            html=content,
            final_url=data.get("url") or url,
            content_type="text/markdown",
        )
