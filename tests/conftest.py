"""테스트 공통 설정 — 한공회 공식 조서 서식(data/workpapers/)을 대상으로 테스트한다."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()  # skipif(ANTHROPIC_API_KEY)가 수집 시점에 .env를 보도록 선행 로드

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKPAPERS = REPO_ROOT / "data" / "workpapers"


@pytest.fixture(scope="session", autouse=True)
def workpapers_dir():
    os.environ["WORKPAPERS_DIR"] = str(WORKPAPERS)
    return WORKPAPERS
