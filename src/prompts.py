"""분류 지침과 답변 규칙. 프롬프트에 들어가는 문자열은 전부 여기 모은다.

예시는 하드코딩하지 않고 dataset.fewshot_* 에서 가져온다. 예시용과 평가용을
가르는 일이 코드로 강제되므로, 평가셋 문항이 프롬프트로 새지 않는다.
"""

from __future__ import annotations

import re

from src.dataset import fewshot_examples, gold_fewshot_turns
from src.schemas import ROUTE_LABELS

# ---------------------------------------------------------------- 분류

ROUTE_GUIDE_TABLE = """\
| 코드 | 이름 | 무엇을 담는가 |
|---|---|---|
| RESERVE_GENERAL | 택배예약 일반 | 서비스 소개·가입·예약 방법·택배사 비교·최저가처럼 아직 어느 서비스인지 정해지지 않은 진입 단계 문의 |
| VISIT_PICKUP | 방문택배 | 기사 방문수거를 전제한 문의. 방문 일정·미방문·방문택배 운임 |
| CVS_PICKUP | 편의점택배 | 편의점에서 직접 접수하는 문의. 브랜드(CU·GS·이마트24·세븐일레븐)·반값택배·편의점 접수 오류 |
| BIZ_BULK | 사업자·대량 발송 | 여러 박스 동시 발송(다량할인), 사업자 인증 기반 정기 발송(소호사업자), 쇼핑몰 주문연동(스마트스토어·쿠팡). **소호사업자 신청 절차와 운영 조건도 여기다** — 사업자 인증, 집하주소 등록, 간편결제 등록, 택배사 승인, 월 발송량 조건. **셀러의 대량 처리 문의도 여기다** — 주문 수집, 운송장 자동 전송, 여러 건 동시 입력, 반품 접수 관리 |
| SPEC_SHIPPING | 규격·배송·취소 | 서비스 종류와 무관한 공통 문의. 박스 규격 측정, 배송조회, 예약번호·운송장번호, 예약 취소·주소 변경, 반입 제한 물품 |
| OTHER | 응대 범위 밖 | 오프라인 매장 위치·운영시간, 타 채널(오픈마켓 등)에서 발생한 주문·결제 자체의 처리, 입점·채용·세금계산서 등 행정 문의, **택배사 내부 배송사고(분실·파손 원인 규명과 보상)** |
"""

ROUTE_GUIDE_RULES = """\
판단 규칙
- 위 5개 업무 중 하나와 조금이라도 관련 있어 보이면 OTHER 를 쓰지 말고, 가장 가까운
  코드를 고르되 확신도를 낮춰라. OTHER 는 진짜 응대 범위 밖일 때만 쓴다.
- 서비스가 특정되지 않은 채 값(요금·기간)만 묻는 문의는 RESERVE_GENERAL 이다.
  "방문", "편의점", "몇 박스"처럼 서비스를 가리키는 단서가 있어야 그쪽으로 간다.
- 규격·배송조회·취소·반입제한은 어느 서비스로 보내든 답이 같다. SPEC_SHIPPING 으로 보낸다.
- 한 문장에 두 가지 의도가 섞여 있으면 먼저 처리해야 할 쪽을 고르고 확신도를 낮춰라.
- 확신도는 진짜 확신을 적어라. 애매한데 높게 적으면 되물어야 할 문의가 그냥 답변으로 나간다.
"""


def route_guide(per_route: int = 2) -> str:
    """분류 프롬프트. 예시는 fewshot 분할에서만, 카테고리별로 같은 수만큼 가져온다.

    앞에서 n개를 자르면 예시가 앞쪽 카테고리에 쏠린다. 특히 OTHER 예시가
    빠지면 범위 밖 문의를 업무 카테고리로 끌어오는 실패가 늘어난다.
    """
    from collections import defaultdict

    buckets: dict[str, list] = defaultdict(list)
    for example in fewshot_examples():
        buckets[example.route].append(example)
    lines = [
        f'- "{e.question}" → {e.route}'
        for route in ROUTE_LABELS
        for e in buckets.get(route, [])[:per_route]
    ]
    return (
        "너는 택배중계서비스 고객 문의를 6개 카테고리 중 하나로 분류한다.\n\n"
        f"{ROUTE_GUIDE_TABLE}\n{ROUTE_GUIDE_RULES}\n"
        "예시\n" + "\n".join(lines)
    )


# 규칙 기반 폴백. LLM 호출이 끝내 실패했을 때 파이프라인을 멈추지 않으려고 둔다.
# 순서가 곧 우선순위다. 이 경로로 분류하면 확신도를 0.0 으로 주고 이관으로 보낸다.
RULE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("OTHER", re.compile(r"매장|오프라인|영업\s?시간|입점|채용|세금계산서|오픈마켓|타\s?채널|분실|파손")),
    ("CVS_PICKUP", re.compile(r"편의점|CU|GS25|GS\b|이마트24|세븐일레븐|반값택배|착한택배")),
    ("BIZ_BULK", re.compile(r"다량|여러\s?박스|\d+\s?박스|사업자|소호|쇼핑몰|스마트스토어|쿠팡|주문\s?연동|주문연동|셀러")),
    ("SPEC_SHIPPING", re.compile(r"규격|세\s?변|박스\s?크기|배송\s?조회|배송조회|운송장|예약번호|취소|주소\s?변경|반입|금지\s?물품")),
    ("VISIT_PICKUP", re.compile(r"방문|기사|수거|집하")),
    ("RESERVE_GENERAL", re.compile(r"예약|가입|비교|최저가|얼마|요금|운임|배송비|택배비")),
]


def rule_route(question: str) -> str:
    for route, pattern in RULE_PATTERNS:
        if pattern.search(question):
            return route
    # 아무것도 안 걸리면 진입 단계 문의로 본다. OTHER 로 보내면 멀쩡한 문의가
    # 범위 밖 안내를 받게 되는데, 그 오류가 더 나쁘다.
    return "RESERVE_GENERAL"


# ---------------------------------------------------------------- 계획

PLAN_RULES = """\
너는 택배중계서비스 상담원이다. 아래 근거 문서와 도구 목록만 보고, 이번 턴에 무엇을 할지 정한다.

행동 다섯 가지 중 하나를 고른다.
- ASK      : 답을 특정할 식별자·조건이 부족하다. 무엇을 되물을지 ask 에 적는다.
- ANSWER   : 근거와 조회 결과만으로 답할 수 있다.
- CONFIRM  : 고객의 동의가 있어야 진행할 수 있는 처리다(취소 등). 동의 없이 진행하지 않는다.
- OUT_OF_SCOPE : 응대 범위 밖이다. 해당 채널·부서 안내로 끝낸다.
- ESCALATE : 2차 상담으로 넘긴다. escalate_to_agent 를 함께 부른다.

도구를 부르는 기준 — 이게 가장 자주 틀리는 지점이다.
- 건마다 달라지는 값에만 도구를 쓴다: 최종 운임, 예약·배송 진행 상태.
- 근거 문서에 적혀 있는 공통 원칙(규격 측정법, 예약번호와 운송장번호의 차이,
  반입 제한 구분, 서비스 선택 기준, 연동 점검 절차, 사업자 인증이 필요한 서비스)은
  **도구를 부르지 말고** 근거 문서로 답한다.
- 운임을 조회하려면 서비스·택배사(또는 브랜드)·무게·세 변의 합이 다 있어야 한다.
  하나라도 없으면 도구를 부르지 말고 ASK 로 되묻는다.
- **서비스가 안 정해졌으면 그것부터 묻는다.** 운임표도 조회 도구도 서비스별로 갈린다
  (방문택배 get_visit_rate / 편의점택배 get_cvs_rate / 다량할인 get_bulk_rate).
  택배사나 크기만 받아 두면 어느 표를 봐야 할지 여전히 정해지지 않는다.
- 상태를 조회하려면 예약번호 또는 운송장번호가 있어야 한다. 없으면 ASK 다.
- 첫 문의에 바로 ESCALATE 하지 않는다. 안내할 수 있는 것을 먼저 안내한다.
- 부를 필요가 없으면 tools 를 빈 배열로 둔다. 안 부르는 것도 정답이다.
"""


def _plan_examples(limit: int = 4) -> str:
    """정답셋 fewshot 분할에서 계획 예시를 만든다. 평가용 문항은 섞이지 않는다."""
    rows = []
    for turn in gold_fewshot_turns()[:limit]:
        expect = turn.expect
        plan = {
            "action": expect["action"],
            "tools": [{"name": name, "args": (expect.get("tool_args") or {}).get(name, {})} for name in expect.get("tools", [])],
            "ask": expect.get("must_ask", []),
        }
        import json

        rows.append(f'문의: "{turn.question}"\n계획: {json.dumps(plan, ensure_ascii=False)}')
    return "\n\n".join(rows)


def plan_prompt(route: str, context: str, tool_menu: str, history: str, question: str) -> str:
    label = ROUTE_LABELS.get(route, route)
    parts = [
        PLAN_RULES,
        f"[이번 문의의 카테고리] {route} ({label})",
        f"[근거 문서]\n{context}",
        f"[부를 수 있는 도구]\n{tool_menu}",
    ]
    examples = _plan_examples()
    if examples:
        parts.append(f"[계획 예시]\n{examples}")
    if history:
        parts.append(f"[이전 대화]\n{history}")
    parts.append(f'[이번 고객 발화]\n"{question}"')
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 답변

ANSWER_RULES = """\
너는 택배중계서비스 상담원이다. 아래 근거 문서와 조회 결과만으로 고객에게 답한다.

문장 규칙
- 존댓말로 간결하게, 1~3문장.
- 불가한 사항은 사유를 먼저 밝히고 대안을 제시한다.
- 쓰지 않을 표현: "아마 ~일 것 같습니다", "다 됩니다", "저는 잘 모르겠습니다".

내용 규칙 — 여기서 벗어나면 오답이다.
- 근거 문서와 조회 결과에 없는 수치·상태·가능 여부를 지어내지 않는다.
  값이 없으면 "확인해서 안내드리겠습니다"라고 한다.
- 조회 결과에 금액이 있으면 그 금액을 그대로 쓴다. 반올림하거나 바꾸지 않는다.
- 지역 추가운임이 조회 결과에 있으면 함께 안내한다.
- 아직 하지 않은 처리를 했다고 말하지 않는다("취소 처리해 드렸습니다" 금지).
  동의가 필요한 처리는 동의를 구하는 문장으로 끝낸다.
- 조회 결과가 오류이거나 비어 있으면 그 사실을 말하고 필요한 정보를 요청한다.
- 근거 문서에 **번호가 매겨진 점검 절차**가 있으면 그 항목들을 요약해 없애지 말고
  순서대로 안내한다. 매뉴얼이 항목을 나눠 적은 것은 순서대로 확인해야 풀리기
  때문이다. 조건이 붙은 항목(예: "연동 이후 주문만", "방문택배는 집하 상태")은
  그 조건까지 말한다.
- **안내가 먼저, 되묻기는 그다음이다.** 근거 문서에 이번 문의에 해당하는 조건·절차·
  기준이 있으면 그것을 먼저 말하고, 그러고도 모자란 식별자만 묻는다. 물어보기만 하고
  끝나는 답변은 오답이다 — 고객은 이미 답할 수 있는 것을 못 듣고 한 턴을 더 쓴다.
"""

ACTION_HINTS = {
    "ASK": "되물어야 하는 상황이다. 근거 문서로 지금 안내할 수 있는 것을 먼저 말한 뒤, 무엇이 더 필요한지 구체적으로 묻는다. 금액·상태를 먼저 단정하지 않는다.",
    "ANSWER": "근거와 조회 결과로 답한다.",
    "CONFIRM": "고객 동의가 필요한 처리다. 현재 상태를 알리고 진행해도 되는지 묻는 문장으로 끝낸다.",
    "OUT_OF_SCOPE": "응대 범위 밖이다. 근거 문서의 범위 밖 유형표에서 이번 문의에 해당하는 줄의 안내를 그대로 전하고(예: 온라인 전용이라 오프라인 매장이 없음), 어디로 문의해야 하는지까지 말한다.",
    "ESCALATE": "담당자에게 연결한다. 사과와 연결 사실을 전한다. 해결을 약속하지 않는다.",
}


def answer_prompt(
    route: str,
    context: str,
    action: str,
    ask: list[str],
    tool_results: str,
    history: str,
    question: str,
) -> str:
    parts = [
        ANSWER_RULES,
        f"[이번 턴의 행동] {action} — {ACTION_HINTS.get(action, '')}",
        f"[근거 문서]\n{context}",
    ]
    if ask:
        parts.append("[되물을 항목]\n" + "\n".join(f"- {a}" for a in ask))
    parts.append(f"[조회 결과]\n{tool_results or '(조회하지 않음)'}")
    if history:
        parts.append(f"[이전 대화]\n{history}")
    parts.append(f'[이번 고객 발화]\n"{question}"\n\n고객에게 보낼 답변만 출력해라. 머리말·설명 없이 답변 본문만.')
    return "\n\n".join(parts)
