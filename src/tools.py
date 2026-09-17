"""mockdb 함수를 LangChain @tool 로 등록한다.

타입 힌트가 입력 스키마, docstring 이 도구 설명이 된다. 구현은 mockdb.py 에
두고 여기는 얇게 유지한다 — 도구 목록이 바뀌어도 조회 로직은 그대로다.

이름과 인자는 answer_goldenset.json 이 기대하는 것을 그대로 따른다.
여기서 이름을 바꾸면 "도구 호출 적절성" 채점이 통째로 어긋난다.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from src import mockdb


@tool
def get_visit_rate(carrier: str, weight_kg: float, size_cm: float, region: Optional[str] = None) -> dict:
    """방문택배 운임을 택배사·무게·크기 구간으로 조회한다.

    size_cm 은 가로+세로+높이 세 변의 합이다. 제주·도서처럼 지역이 언급되면
    region 을 함께 준다 — 지역 추가운임은 택배사마다 다르다.
    carrier 예: 롯데택배, 한진택배, CJ대한통운, 우체국택배.
    """
    return mockdb.get_visit_rate(carrier, weight_kg, size_cm, region)


@tool
def get_cvs_rate(brand: str, weight_kg: float, size_cm: float, region: Optional[str] = None) -> dict:
    """편의점택배 운임을 브랜드·무게·크기 구간으로 조회한다.

    브랜드마다 기본 운임과 제주·도서 이용 가능 여부가 다르므로 지역을 먼저 확인한다.
    brand 예: CU편의점택배, GS편의점택배, 이마트24편의점택배, 세븐일레븐 편의점택배,
    GS25 반값택배, CU반값택배, 세븐일레븐 착한택배.
    """
    return mockdb.get_cvs_rate(brand, weight_kg, size_cm, region)


@tool
def get_bulk_rate(box_count: int, weight_kg: float, size_cm: float, region: Optional[str] = None) -> dict:
    """다량할인택배 운임을 발송 수량 기준으로 조회한다.

    다량할인은 택배사가 아니라 박스 수량으로 상품이 갈린다(3박스 이상 / 10박스 이상 등).
    수량을 모르면 이 도구를 부르기 전에 먼저 되묻는다.
    """
    return mockdb.get_bulk_rate(box_count, weight_kg, size_cm, region)


@tool
def get_biz_rate() -> dict:
    """소호사업자택배 운임 구간을 조회한다.

    이 값은 매뉴얼에 공개되어 있지 않다. 반드시 이 도구를 불러 '조회 필요'임을
    확인하고, 구간·금액을 임의로 안내하지 않는다.
    """
    return mockdb.get_biz_rate()


@tool
def get_reservation_status(reservation_id: str) -> dict:
    """예약번호로 예약의 현재 상태와 다음 안내를 조회한다.

    방문 지연·취소 가능 여부·편의점 접수 브랜드처럼 건마다 달라지는 값은
    이 조회 없이 답하지 않는다. 예약번호는 R-12345 형태다.
    """
    return mockdb.get_reservation_status(reservation_id)


@tool
def get_tracking_status(tracking_number: str) -> dict:
    """운송장번호로 배송 상태를 조회한다.

    운송장번호는 택배사가 발급한 숫자 번호로, 플랫폼이 발급하는 예약번호와 다르다.
    """
    return mockdb.get_tracking_status(tracking_number)


@tool
def get_box_size_rule() -> dict:
    """박스 규격(세 변의 합) 측정 기준과 규격 초과 시 처리를 조회한다."""
    return mockdb.get_box_size_rule()


@tool
def get_restricted_items(item: Optional[str] = None) -> dict:
    """반입 제한 물품인지 조회한다. item 을 비우면 전체 목록을 돌려준다.

    접수 자체가 불가한 그룹과 택배사 기준에 따라 갈리는 그룹이 다르다.
    후자를 '무조건 불가'로 단정하면 안 된다.
    """
    return mockdb.get_restricted_items(item)


@tool
def get_cancel_rule(stage: Optional[str] = None) -> dict:
    """예약 취소·주소 변경 가능 여부 규칙을 단계별로 조회한다.

    stage 예: "택배사 접수 전", "운송장 발급 후". 실제 가능 여부는 예약 상태
    조회가 있어야 확정된다.
    """
    return mockdb.get_cancel_rule(stage)


@tool
def get_order_sync_troubleshoot(symptom: str) -> dict:
    """쇼핑몰 주문연동 문제의 점검 순서와 이관 시 받아야 할 정보를 조회한다.

    symptom 예: "주문이 수집되지 않음", "운송장번호가 쇼핑몰에 등록되지 않음".
    """
    return mockdb.get_order_sync_troubleshoot(symptom)


@tool
def get_service_guide(service: Optional[str] = None) -> dict:
    """서비스 구분별 설명과 선택 기준을 조회한다. 비우면 전체 비교표를 돌려준다.

    service 예: RESERVE_GENERAL, VISIT_PICKUP, CVS_PICKUP, BULK_DISCOUNT,
    BIZ_ACCOUNT, ORDER_SYNC.
    """
    return mockdb.get_service_guide(service)


@tool
def escalate_to_agent(reason: str, summary: str, sentiment: str = "neutral") -> dict:
    """2차 상담으로 이관한다. 이관 사유와 지금까지 확인한 내용을 반드시 남긴다.

    동일 사안 3회 이상 반복, 강한 불만, 보상 요구, 연동 오류 반복 시 이관한다.
    sentiment 는 neutral 또는 negative.
    """
    return mockdb.escalate_to_agent(reason, summary, sentiment)


ALL_TOOLS = [
    get_visit_rate,
    get_cvs_rate,
    get_bulk_rate,
    get_biz_rate,
    get_reservation_status,
    get_tracking_status,
    get_box_size_rule,
    get_restricted_items,
    get_cancel_rule,
    get_order_sync_troubleshoot,
    get_service_guide,
    escalate_to_agent,
]

TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

# 카테고리별로 부를 만한 도구. 프롬프트에 이 목록만 넣어 선택지를 좁힌다.
ROUTE_TOOLS: dict[str, list[str]] = {
    "RESERVE_GENERAL": ["get_service_guide", "get_visit_rate", "get_cvs_rate", "get_bulk_rate", "escalate_to_agent"],
    "VISIT_PICKUP": ["get_visit_rate", "get_reservation_status", "get_box_size_rule", "escalate_to_agent"],
    "CVS_PICKUP": ["get_cvs_rate", "get_reservation_status", "get_box_size_rule", "escalate_to_agent"],
    "BIZ_BULK": ["get_bulk_rate", "get_biz_rate", "get_order_sync_troubleshoot", "get_reservation_status", "escalate_to_agent"],
    "SPEC_SHIPPING": [
        "get_box_size_rule",
        "get_restricted_items",
        "get_cancel_rule",
        "get_tracking_status",
        "get_reservation_status",
        "escalate_to_agent",
    ],
    "OTHER": ["escalate_to_agent"],
}


def tools_for(route: str) -> list:
    """이 카테고리에서 쓸 수 있는 도구 객체 목록."""
    return [TOOLS_BY_NAME[name] for name in ROUTE_TOOLS.get(route, ["escalate_to_agent"])]


def tool_menu(route: str) -> str:
    """프롬프트에 넣을 도구 목록. 이름과 인자만 간결하게."""
    lines = []
    for t in tools_for(route):
        params = ", ".join(t.args_schema.model_json_schema().get("properties", {}))
        summary = (t.description or "").strip().splitlines()[0]
        lines.append(f"- {t.name}({params}) — {summary}")
    return "\n".join(lines)
