"""카테고리 정의와 구조화 출력 스키마.

원본 데이터셋은 업무 7개 + OTHER = 8개였다. 과제가 "카테고리 3~5개 + 어디에도
속하지 않는 것 1개"를 요구하므로 사업자·대량 발송 계열 3개를 BIZ_BULK 로 합쳐
5 + OTHER 로 줄였다. 병합 기준은 "근거 문서의 어느 절을 읽어야 하는가"다.
"""

from typing import Literal

from pydantic import BaseModel, Field

ROUTES = Literal[
    "RESERVE_GENERAL",
    "VISIT_PICKUP",
    "CVS_PICKUP",
    "BIZ_BULK",
    "SPEC_SHIPPING",
    "OTHER",
]

ROUTE_LIST: list[str] = list(ROUTES.__args__)

# 어디에도 속하지 않는 문의를 담는 카테고리. 나머지 5개가 업무 카테고리다.
FALLBACK_ROUTE = "OTHER"

ROUTE_LABELS: dict[str, str] = {
    "RESERVE_GENERAL": "택배예약 일반",
    "VISIT_PICKUP": "방문택배",
    "CVS_PICKUP": "편의점택배",
    "BIZ_BULK": "사업자·대량 발송",
    "SPEC_SHIPPING": "규격·배송·취소",
    "OTHER": "응대 범위 밖",
}

# 원본 8구분 → 5+OTHER. REPORT 에서 병합 근거를 보일 때 이 표를 그대로 쓴다.
ROUTE_MERGE: dict[str, str] = {
    "RESERVE_GENERAL": "RESERVE_GENERAL",
    "VISIT_PICKUP": "VISIT_PICKUP",
    "CVS_PICKUP": "CVS_PICKUP",
    "BULK_DISCOUNT": "BIZ_BULK",
    "BIZ_ACCOUNT": "BIZ_BULK",
    "ORDER_SYNC": "BIZ_BULK",
    "SPEC_SHIPPING": "SPEC_SHIPPING",
    "OTHER": "OTHER",
}


def merge_route(original: str) -> str:
    """원본 라우트 코드를 병합된 코드로 옮긴다. 모르는 코드는 OTHER 로 보낸다."""
    return ROUTE_MERGE.get(original.strip(), FALLBACK_ROUTE)


class RouteDecision(BaseModel):
    """분류 노드가 내놓는 판정. 이관 여부는 여기서 정하지 않는다 — 확신도만 준다."""

    route: ROUTES = Field(description="6개 카테고리 중 하나")
    confidence: float = Field(ge=0.0, le=1.0, description="이 판단에 대한 확신도. 애매하면 낮게.")
    reason: str = Field(description="이렇게 판단한 근거를 한 문장으로")


ACTIONS = Literal["ASK", "ANSWER", "CONFIRM", "OUT_OF_SCOPE", "ESCALATE"]


class ToolCallPlan(BaseModel):
    name: str = Field(description="호출할 도구 이름")
    args: dict = Field(default_factory=dict, description="도구 인자")


class AnswerPlan(BaseModel):
    """답변 노드에 앞서 무엇을 할지 정하는 계획.

    네 백엔드 중 둘(claude/opencode CLI)은 네이티브 function calling 이 없다.
    계획을 이 스키마로 받아 파이썬이 도구를 실행하면, 백엔드가 달라도 같은
    프로토콜로 재게 되어 수치가 서로 비교 가능해진다.
    """

    action: ACTIONS = Field(description="이번 턴에 취할 행동")
    tools: list[ToolCallPlan] = Field(default_factory=list, description="호출할 도구 목록. 없으면 빈 배열")
    ask: list[str] = Field(default_factory=list, description="action 이 ASK 일 때 되물을 항목")
