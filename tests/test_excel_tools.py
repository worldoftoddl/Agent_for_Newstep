"""Excel 탐색 도구 테스트 — 한공회 공식 조서 서식(실파일) 기준."""

import os

import pytest

from agent.tools.excel import (
    excel_find,
    excel_read_range,
    excel_sheet_stats,
    excel_workbook_overview,
    list_workpapers,
)

WP_계약 = "감사조서서식_1100~1300 감사계약.xlsx"
WP_3650 = "감사조서서식_3650 감사 전 재무제표 확인.xlsx"
WP_4000 = "감사조서서식_4000 계정별 실증절차 (KIFRS용) 2025.xlsx"


# ── list_workpapers ──────────────────────────────────────────
def test_list_workpapers_lists_official_forms():
    out = list_workpapers.invoke({})
    assert WP_계약 in out and WP_3650 in out and WP_4000 in out


# ── excel_workbook_overview ──────────────────────────────────
def test_overview_shows_sheets_and_merges():
    out = excel_workbook_overview.invoke({"path": WP_3650})
    assert "3650" in out and "3650A 신규" in out
    assert "49행 × 16열" in out
    assert "병합" in out


def test_overview_loads_phonetic_file():
    """openpyxl phonetic 속성 회귀 테스트 — 패치 없이는 로드가 죽는 파일."""
    out = excel_workbook_overview.invoke({"path": WP_4000})
    assert "시트 36개" in out
    assert "실증절차" in out


def test_overview_missing_file_suggests_candidates():
    out = excel_workbook_overview.invoke({"path": "없는파일.xlsx"})
    assert "오류" in out
    assert WP_3650 in out  # 후보 목록 제시


def test_overview_blocks_path_escape():
    out = excel_workbook_overview.invoke({"path": "../탈출.xlsx"})
    assert "오류" in out


# ── excel_read_range ─────────────────────────────────────────
def test_read_range_values_as_markdown():
    out = excel_read_range.invoke(
        {"path": WP_3650, "sheet": "3650", "cell_range": "A1:C3"}
    )
    assert "감사 전 재무제표 확인" in out
    assert "|" in out  # 마크다운 표


def test_read_range_formulas_mode_shows_cross_sheet_refs():
    out = excel_read_range.invoke(
        {"path": WP_계약, "sheet": "1200", "cell_range": "A4:D5", "mode": "formulas"}
    )
    assert "='1100'!B4" in out  # 시트 간 참조 수식


def test_read_range_cell_cap_returns_guidance():
    # 3650A 신규: 113행 × 13열 = 1,469셀 > 500
    out = excel_read_range.invoke(
        {"path": WP_3650, "sheet": "3650A 신규", "cell_range": "A1:M113"}
    )
    assert "500" in out and "나눠" in out  # 예외가 아니라 분할 안내


def test_read_range_bad_sheet_is_error_text():
    out = excel_read_range.invoke(
        {"path": WP_3650, "sheet": "없는시트", "cell_range": "A1:B2"}
    )
    assert "오류" in out


# ── excel_find ───────────────────────────────────────────────
def test_find_locates_cell_across_sheets():
    out = excel_find.invoke({"path": WP_3650, "query": "감사 전 재무제표"})
    assert "3650!A2" in out


def test_find_scoped_to_sheet():
    out = excel_find.invoke(
        {"path": WP_3650, "query": "감사 전 재무제표", "sheet": "3650"}
    )
    assert "3650!A2" in out
    assert "3650A 신규!" not in out


# ── excel_sheet_stats ────────────────────────────────────────
def test_sheet_stats_counts_formulas():
    out = excel_sheet_stats.invoke({"path": WP_계약, "sheet": "1200"})
    assert "수식 셀: 3" in out  # ='1100'!… 3건


# ── 에이전트 통합 (개요→정독 탐색 순서) ──────────────────────
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="API 키 없음")
async def test_agent_explores_overview_first():
    from agent.graph import graph

    g = await graph({"configurable": {}})
    result = await g.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": f"{WP_3650} 파일이 어떤 조서인지 간단히 설명해줘.",
                }
            ]
        },
        config={"recursion_limit": 20},
    )
    tool_calls = [
        tc["name"]
        for m in result["messages"]
        for tc in (getattr(m, "tool_calls", None) or [])
    ]
    assert "excel_workbook_overview" in tool_calls
    assert result["messages"][-1].content
