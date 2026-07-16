"""인용 표기 계층(cid→display) 단위 테스트 — 픽스처 페이로드, 네트워크 불요.

변환 규칙 출처: docs/system_design.md 5.1절 표.
"""

import json

from agent.citations import attach_displays, make_display


def _item(cid: str, title: str | None = None) -> dict:
    d = {"cid": cid, "para_no": cid.rsplit("::", 1)[-1]}
    parts = cid.split("::")
    if len(parts) == 3:
        d["standard_no"] = parts[1]
    if title:
        d["standard_title"] = title
    return d


class TestMakeDisplay:
    def test_kifrs_본문(self):
        item = _item("KIFRS::1115::31", "고객과의 계약에서 생기는 수익")
        assert make_display(item) == "K-IFRS 제1115호 '고객과의 계약에서 생기는 수익' 문단 31"

    def test_kifrs_제목_없으면_생략(self):
        assert make_display(_item("KIFRS::1115::31")) == "K-IFRS 제1115호 문단 31"

    def test_ksa_적용자료(self):
        assert make_display(_item("KSA::315::A12")) == "감사기준서 315 문단 A12(적용자료)"

    def test_ksa_본문(self):
        assert make_display(_item("KSA::315::12")) == "감사기준서 315 문단 12"

    def test_guide(self):
        assert make_display(_item("GUIDE::2017-1::25")) == "회계감사실무지침 2017-1 문단 25"

    def test_결론도출근거(self):
        item = _item("KIFRS::1116::BC1", "리스")
        assert make_display(item) == "K-IFRS 제1116호 '리스' 결론도출근거 BC1 (기준서 본문 아님)"

    def test_적용사례(self):
        item = _item("KIFRS::1103::IE사례5-2", "사업결합")
        assert make_display(item) == "K-IFRS 제1103호 '사업결합' 적용사례 사례 5의 문단 2"

    def test_부록(self):
        assert make_display(_item("KSA::240::부록1")) == "감사기준서 240 부록 1"

    def test_정의(self):
        assert make_display(_item("KSA::240::정의-전문가")) == "감사기준서 240 '전문가'의 정의"

    def test_모르는_접두는_cid_그대로(self):
        assert make_display({"cid": "XYZ::1::2"}) == "XYZ::1::2"

    def test_cid_없으면_빈_문자열(self):
        assert make_display({}) == ""


class TestAttachDisplays:
    def test_search_페이로드_각_히트에_display(self):
        payload = json.dumps(
            {
                "collection": "standards_x",
                "results": [
                    {
                        "cid": "KSA::240::A52",
                        "standard_no": "240",
                        "standard_title": "재무제표감사에서 부정에 관한 감사인의 책임",
                        "para_no": "A52",
                        "text": "...",
                    }
                ],
            },
            ensure_ascii=False,
        )
        out = json.loads(attach_displays(payload))
        assert out["results"][0]["display"] == "감사기준서 240 문단 A52(적용자료)"

    def test_get_paragraph_페이로드_각_문단에_display(self):
        payload = json.dumps(
            {
                "found": True,
                "paragraphs": [
                    {
                        "cid": "KIFRS::1115::31",
                        "standard_no": "1115",
                        "standard_title": "고객과의 계약에서 생기는 수익",
                        "para_no": "31",
                        "text": "...",
                    }
                ],
            },
            ensure_ascii=False,
        )
        out = json.loads(attach_displays(payload))
        assert (
            out["paragraphs"][0]["display"]
            == "K-IFRS 제1115호 '고객과의 계약에서 생기는 수익' 문단 31"
        )

    def test_definitions_리스트도_처리(self):
        payload = json.dumps(
            {"definitions": [{"cid": "KSA::240::정의-부정", "standard_no": "240", "para_no": "정의-부정"}]},
            ensure_ascii=False,
        )
        out = json.loads(attach_displays(payload))
        assert out["definitions"][0]["display"] == "감사기준서 240 '부정'의 정의"

    def test_json_아닌_텍스트는_그대로(self):
        assert attach_displays("일반 오류 메시지") == "일반 오류 메시지"

    def test_오류_봉투는_변형_없이_통과(self):
        payload = json.dumps({"error": {"code": "UPSTREAM_UNAVAILABLE", "hint": "..."}})
        assert json.loads(attach_displays(payload)) == json.loads(payload)
