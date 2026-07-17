"""CSV 입력 지원 테스트 — 값 전용 단일 시트 워크북 변환 경로 전체를 검증한다.

인코딩(utf-8-sig·cp949) 판별, Excel 도구 호환, 표 SQL 질의, 그래프 파일 탐지.
"""

import pytest

from agent.graph_common import EXCEL_SUFFIXES, find_target_file
from agent.tools.excel import (
    excel_formula_map,
    excel_read_range,
    excel_workbook_overview,
    list_workpapers,
)
from agent.tools.table import excel_query_table

CSV_UTF8 = "매출내역.csv"
CSV_CP949 = "지출내역_cp949.csv"
BODY = "거래처,지역,금액\nA상사,서울,1000\nB물산,부산,2000\nC상사,서울,3000\n"


@pytest.fixture()
def csv_dir(tmp_path, monkeypatch):
    (tmp_path / CSV_UTF8).write_bytes(BODY.encode("utf-8-sig"))
    (tmp_path / CSV_CP949).write_bytes(BODY.encode("cp949"))
    monkeypatch.setenv("WORKPAPERS_DIR", str(tmp_path))
    return tmp_path


def test_list_and_overview(csv_dir):
    assert CSV_UTF8 in list_workpapers.invoke({})
    out = excel_workbook_overview.invoke({"path": CSV_UTF8})
    assert "시트 1개" in out and "[매출내역]" in out  # 시트명 = 파일 stem
    assert "4행 × 3열" in out


@pytest.mark.parametrize("filename", [CSV_UTF8, CSV_CP949])
def test_read_range_both_encodings(csv_dir, filename):
    sheet = filename.rsplit(".", 1)[0]
    out = excel_read_range.invoke(
        {"path": filename, "sheet": sheet, "cell_range": "A1:C4"}
    )
    assert "A상사" in out and "부산" in out  # 한글 인코딩 판별 성공
    assert "값 전용 형식" in out  # CSV 안내 문구


def test_formula_map_notes_no_formulas(csv_dir):
    out = excel_formula_map.invoke({"path": CSV_UTF8, "sheet": "매출내역"})
    assert "값 전용 형식" in out


def test_numeric_cells_typed(csv_dir):
    out = excel_query_table.invoke(
        {
            "path": CSV_UTF8,
            "sheet": "매출내역",
            "cell_range": "A1:C4",
            "sql": 'SELECT "지역", SUM("금액") AS s FROM data GROUP BY 1 ORDER BY s DESC',
        }
    )
    # 서울 4000 > 부산 2000 — 숫자 타입 추론이 됐다는 뜻 (문자열이면 SUM 불가)
    assert out.index("서울") < out.index("부산")
    assert "4000" in out


def test_graphs_accept_csv(csv_dir):
    assert ".csv" in EXCEL_SUFFIXES
    assert find_target_file([f"[첨부 파일: {CSV_UTF8}]"]).name == CSV_UTF8
