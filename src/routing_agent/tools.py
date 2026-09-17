"""mockdb 함수를 LangChain @tool 로 등록한다.

구현은 mockdb.py 에 두고 여기는 얇게 유지한다.

도구 목록은 정책 §0 원칙 1 의 "건별 상이" 칸에서 그대로 끌어왔다 —
최종 운임 · 예약·배송 진행 상태 · 이관. 매뉴얼에 적힌 공통 원칙(규격
측정법, 예약번호 개념, 반입 제한, 서비스 선택 기준, 연동 점검 절차)은
도구가 아니라 근거 문서로 답한다. 그래서 여기에 도구가 없다.

정답셋 23개 턴을 훑어보면 도구가 붙은 것은 운임 3건·상태 4건·이관 2건뿐이고
나머지 14건은 전부 tools=[] 다. 즉 "도구를 안 부르는 것"도 정답이다.
쓰지도 않을 도구를 메뉴에 올리면 관련 없는 조회가 늘고, 그것이 곧
도구 호출 적절성 점수로 떨어진다.

소호사업자 운임에는 도구를 두지 않았다. 그 값은 매뉴얼에도 목데이터에도
없다 — 조회할 곳이 없으니 도구를 만들면 오히려 "조회했다"는 착각을 준다.
근거 문서 §4.4 를 읽고 "조회 필요"라고 답하는 것이 정답이다(정답셋 C-005).
"""

from __future__ import annotations

from typing import Optional

from langchain_core.tools import tool

from routing_agent import mockdb


def _names(group: str) -> str:
    """목데이터에 실제로 있는 이름을 쉼표로 잇는다."""
    return ", ".join(sorted(mockdb.carrier_names(group)))


def _fill_names() -> None:
    """도구 설명에 붙는 이름 목록을 데이터에서 채운다.

    택배사·편의점 브랜드가 늘 때 코드를 고치게 두면, 데이터에는 있는데 도구
    설명에는 없는 상태가 조용히 생긴다. 모델은 설명만 보고 고르므로 그러면
    새 택배사를 영영 못 부른다. 이름의 출처를 목데이터 한 곳으로 둔다.
    """
    get_visit_rate.description += f"\ncarrier 예: {_names('visit_pickup')}."
    get_cvs_rate.description += f"\nbrand 예: {_names('cvs_pickup')}."


@tool
def get_visit_rate(carrier: str, weight_kg: float, size_cm: float, region: Optional[str] = None) -> dict:
    """방문택배 운임을 택배사·무게·크기 구간으로 조회한다.

    size_cm 은 가로+세로+높이 세 변의 합이다. 제주·도서처럼 지역이 언급되면
    region 을 함께 준다 — 지역 추가운임은 택배사마다 다르다.
    """
    return mockdb.get_visit_rate(carrier, weight_kg, size_cm, region)


@tool
def get_cvs_rate(brand: str, weight_kg: float, size_cm: float, region: Optional[str] = None) -> dict:
    """편의점택배 운임을 브랜드·무게·크기 구간으로 조회한다.

    브랜드마다 기본 운임과 제주·도서 이용 가능 여부가 다르므로 지역을 먼저 확인한다.
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
def escalate_to_agent(reason: str, summary: str, sentiment: str = "neutral") -> dict:
    """2차 상담으로 이관한다. 이관 사유와 지금까지 확인한 내용을 반드시 남긴다.

    동일 사안 3회 이상 반복, 강한 불만, 보상 요구, 연동 오류 반복 시 이관한다.
    첫 문의에 바로 부르지 않는다 — 먼저 안내할 수 있는 것을 안내한다.
    sentiment 는 neutral 또는 negative.
    """
    return mockdb.escalate_to_agent(reason, summary, sentiment)


ALL_TOOLS = [
    get_visit_rate,
    get_cvs_rate,
    get_bulk_rate,
    get_reservation_status,
    get_tracking_status,
    escalate_to_agent,
]

TOOLS_BY_NAME = {t.name: t for t in ALL_TOOLS}

# 카테고리별로 부를 만한 도구. 카테고리와 무관한 조회가 메뉴에 없으면 부를 수도 없다.
ROUTE_TOOLS: dict[str, list[str]] = {
    "RESERVE_GENERAL": ["get_visit_rate", "get_cvs_rate", "get_bulk_rate", "escalate_to_agent"],
    "VISIT_PICKUP": ["get_visit_rate", "get_reservation_status", "escalate_to_agent"],
    "CVS_PICKUP": ["get_cvs_rate", "get_reservation_status", "escalate_to_agent"],
    "BIZ_BULK": ["get_bulk_rate", "get_reservation_status", "escalate_to_agent"],
    "SPEC_SHIPPING": ["get_tracking_status", "get_reservation_status", "escalate_to_agent"],
    "OTHER": [],
}


def tools_for(route: str) -> list:
    """이 카테고리에서 쓸 수 있는 도구 객체 목록. OTHER 는 비어 있다."""
    return [TOOLS_BY_NAME[name] for name in ROUTE_TOOLS.get(route, [])]


def tool_menu(route: str) -> str:
    """프롬프트에 넣을 도구 목록. 이름과 인자만 간결하게."""
    tools = tools_for(route)
    if not tools:
        return "(이 카테고리에서 부를 수 있는 도구가 없다. 근거 문서만으로 안내하거나 범위 밖임을 알린다.)"
    lines = []
    for t in tools:
        params = ", ".join(t.args_schema.model_json_schema().get("properties", {}))
        summary = (t.description or "").strip().splitlines()[0]
        lines.append(f"- {t.name}({params}) — {summary}")
    return "\n".join(lines)


_fill_names()
