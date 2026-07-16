"""인용 표기 계층 — cid를 사람이 읽는 표기 문자열(display)로 변환.

표기 문자열 생성은 코드가, 배치 판단은 프롬프트가 맡는다 (system_design 5.1절).
변환 실패는 어떤 경우에도 예외를 내지 않고 원문(cid 또는 페이로드)을 그대로 둔다 —
표기는 부가 정보일 뿐 도구 결과를 깨뜨릴 이유가 없다.
"""

import json
import re

# 검색/조회 페이로드에서 문단 아이템이 담기는 리스트 키
_ITEM_LIST_KEYS = ("paragraphs", "results", "definitions")

_SOURCE_PREFIX = {
    "KIFRS": "K-IFRS 제{no}호",
    "KSA": "감사기준서 {no}",
    "GUIDE": "회계감사실무지침 {no}",
}


def _para_display(para_no: str) -> str:
    """para_no 특수 케이스를 표기 조각으로 변환한다."""
    m = re.fullmatch(r"IE사례(\d+)-(\d+)", para_no)
    if m:
        return f"적용사례 사례 {m.group(1)}의 문단 {m.group(2)}"
    m = re.fullmatch(r"부록(\d+)", para_no)
    if m:
        return f"부록 {m.group(1)}"
    m = re.fullmatch(r"정의-(.+)", para_no)
    if m:
        return f"'{m.group(1)}'의 정의"
    if re.fullmatch(r"BC\d+", para_no):
        return f"결론도출근거 {para_no} (기준서 본문 아님)"
    if re.fullmatch(r"A\d+", para_no):
        return f"문단 {para_no}(적용자료)"
    return f"문단 {para_no}"


def make_display(item: dict) -> str:
    """문단 메타데이터 dict에서 완성된 표기 문자열을 만든다.

    cid 접두(KIFRS/KSA/GUIDE)로 출처를 판정한다 — search 히트에는
    source_type이 없어 cid가 유일하게 신뢰 가능한 판정 근거다.
    판정 불가 시 cid를 그대로 돌려준다.
    """
    cid = item.get("cid", "")
    parts = cid.split("::")
    if len(parts) != 3 or parts[0] not in _SOURCE_PREFIX:
        return cid

    source, standard_no, para_no = parts
    head = _SOURCE_PREFIX[source].format(no=item.get("standard_no") or standard_no)
    title = item.get("standard_title")
    if source == "KIFRS" and title:
        head += f" '{title}'"
    return f"{head} {_para_display(item.get('para_no') or para_no)}"


def attach_displays(text: str) -> str:
    """도구 결과 JSON 텍스트의 각 문단 아이템에 display 필드를 덧붙인다.

    JSON이 아니거나 아는 구조가 아니면 원문을 그대로 반환한다.
    """
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
    if not isinstance(payload, dict):
        return text

    changed = False
    for key in _ITEM_LIST_KEYS:
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("cid"):
                item["display"] = make_display(item)
                changed = True
    return json.dumps(payload, ensure_ascii=False) if changed else text
