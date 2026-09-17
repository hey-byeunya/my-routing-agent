"""답변 정답셋을 보강하고 예시용/평가용을 가른다. 다시 돌려도 같은 결과가 나온다.

왜 보강하는가 — 원본 15대화를 5+OTHER 로 병합하면 RESERVE_GENERAL 이 1건뿐이라
카테고리별 개수가 맞지 않는다. 과제가 "카테고리별 개수를 비슷하게 맞춘다"를
요구하므로 5개를 더 저작한다.

저작 원칙은 원본과 같다.
  - 정답은 policy_courierhub.md 와 mockdata_courierhub.json 만으로 결정된다.
    rubric 에 어느 조항·어느 값에서 나왔는지 적는다.
  - forbid 에는 "근거를 안 읽었을 때 나오기 쉬운 그럴듯한 오답"을 넣는다.
    다른 서비스의 운임이나 규격을 확인하지 않고 집은 구간 같은 것.
  - 도구는 건별로 달라지는 값(운임·상태·이관)에만 붙인다. 공통 원칙은 tools=[] 다.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


from routing_agent.schemas import ROUTE_LIST

PATH = Path(__file__).resolve().parent.parent / "data" / "answer_goldenset.json"

# 프롬프트 예시 전용으로 뺄 대화. 점수를 재는 데 쓰지 않는다.
# 카테고리별로 하나씩, 그 카테고리의 전형적인 흐름을 보여 주는 것으로 골랐다.
FEWSHOT = {
    "C-015",  # RESERVE_GENERAL — 다중 의도를 갈라 되묻는 예
    "C-002",  # VISIT_PICKUP    — 식별자 확보 후 운임 조회하는 예
    "C-004",  # BIZ_BULK        — 수량을 확인하고 구간 운임을 조회하는 예
    "C-008",  # SPEC_SHIPPING   — 조회 없이 공통 원칙으로 답하는 예
}

NEW_CONVERSATIONS = [
    {
        "conv_id": "N-001",
        "title": "최저가 문의 → 서비스·규격 특정 → 택배사 비교",
        "route": "RESERVE_GENERAL",
        "route_original": "RESERVE_GENERAL",
        "pattern": "식별자요청",
        "tests": ["🔧 운임 조회 강제", "서비스 미확정 시 최저가 단정 금지"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "택배 보내려는데 어디가 제일 싼가요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ASK",
                    "tools": [],
                    "must_ask": [
                        "이용하실 서비스(방문택배/편의점택배/다량할인택배)",
                        "박스 크기와 중량",
                    ],
                    "must": [],
                    "forbid": ["3800", "3,800원", "가장 저렴한 곳은"],
                    "rubric": "정책 §0 원칙1: 최종 운임은 건별 상이 항목이다. 서비스와 규격을 모르면 최저가를 특정할 수 없다. 금액을 먼저 제시하면 실패.",
                    "reference": "이용하실 서비스와 박스 크기·중량을 알려주시면 택배사별 운임을 비교해 안내드리겠습니다.",
                },
            },
            {
                "turn": 3,
                "role": "customer",
                "text": "집으로 기사님이 와서 가져가면 좋겠어요. 2kg에 세 변 합 60cm 정도예요.",
            },
            {
                "turn": 4,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": ["get_visit_rate"],
                    "tool_args": {"get_visit_rate": {"carrier": "롯데택배", "weight_kg": 2, "size_cm": 60}},
                    "must": ["3800", "롯데택배"],
                    "forbid": ["2990", "3490"],
                    "rubric": "방문택배로 좁혀졌으므로 택배사별 구간을 조회해 비교한다. mockdata carriers.visit_pickup 2kg/60cm 기준 롯데 3800 < 한진 3900. forbid 의 2990 은 다량할인, 3490 은 편의점택배 값으로, 서비스를 혼동했을 때 나오기 쉬운 수치다.",
                    "reference": "방문택배 2kg·세 변 합 60cm 기준으로는 롯데택배가 3,800원으로 가장 저렴하고 한진택배가 3,900원입니다. 제주·도서지역은 추가운임이 붙으며, 확정 금액은 예약 화면에서 확인해 주세요.",
                },
            },
        ],
    },
    {
        "conv_id": "N-002",
        "title": "개인 이용 가능 여부 → 조회 없이 공통 원칙으로 answer",
        "route": "RESERVE_GENERAL",
        "route_original": "RESERVE_GENERAL",
        "pattern": "정책안내",
        "tests": ["🔧 불필요한 조회 금지", "사업자 전용 서비스 구분"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "개인도 쓸 수 있나요? 사업자만 되는 건가요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["소호사업자택배"],
                    "forbid": ["사업자만", "개인은 이용할 수 없"],
                    "rubric": "정책 §2: 개인회원도 방문택배·편의점택배·다량할인택배를 이용할 수 있고, 사업자 인증이 필요한 서비스는 소호사업자택배뿐이다. 매뉴얼에 적힌 공통 원칙이라 §0 원칙1 에 따라 조회 없이 즉답한다. 운임 조회를 부르면 도구 호출 적절성에서 실패.",
                    "reference": "개인 회원도 방문택배·편의점택배·다량할인택배를 모두 이용하실 수 있습니다. 사업자 인증이 필요한 서비스는 소호사업자택배뿐입니다.",
                },
            },
        ],
    },
    {
        "conv_id": "N-003",
        "title": "편의점택배 제주 발송 → 브랜드 특정 → 운임·할증 안내",
        "route": "CVS_PICKUP",
        "route_original": "CVS_PICKUP",
        "pattern": "식별자요청",
        "tests": ["🔧 브랜드별 지역 제한 단정 금지", "지역 할증 포함"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "편의점택배로 제주도에 보낼 수 있나요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ASK",
                    "tools": [],
                    "must_ask": ["이용하실 편의점 브랜드"],
                    "must": [],
                    "forbid": ["다 됩니다", "모두 가능합니다", "전부 가능"],
                    "rubric": "정책 §4.3: 제주·도서 이용 가능 여부는 브랜드마다 다르므로 지역을 먼저 확인한 뒤 상품을 안내한다. 매뉴얼이 '편의점택배는 다 제주도 배송이 됩니다'를 금지 예시로 못박았다.",
                    "reference": "편의점 브랜드에 따라 제주 지역 이용 가능 여부와 운임이 달라집니다. 이용하실 브랜드를 알려주시면 확인해 안내드리겠습니다.",
                },
            },
            {"turn": 3, "role": "customer", "text": "CU요. 2kg 정도에 세 변 합 80cm 안 넘어요."},
            {
                "turn": 4,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": ["get_cvs_rate"],
                    "tool_args": {
                        "get_cvs_rate": {
                            "brand": "CU편의점택배",
                            "weight_kg": 2,
                            "size_cm": 80,
                            "region": "제주",
                        }
                    },
                    "must": ["3490", "3000"],
                    "forbid": ["4200", "3100"],
                    "rubric": "mockdata carriers.cvs_pickup.CU편의점택배 2kg/80cm = 3490, jeju_surcharge = 3000. forbid 의 4200 은 20kg/140cm 구간, 3100 은 0.5kg 구간으로, 규격을 확인하지 않고 집었을 때 나오기 쉬운 값이다.",
                    "reference": "CU편의점택배 기준 2kg·세 변 합 80cm 이하는 3,490원이며, 제주 지역은 3,000원의 추가운임이 붙습니다. 확정 금액은 예약 화면에서 확인해 주세요.",
                },
            },
        ],
    },
    {
        "conv_id": "N-004",
        "title": "박스 규격 측정법 → 조회 없이 공통 원칙으로 answer",
        "route": "SPEC_SHIPPING",
        "route_original": "SPEC_SHIPPING",
        "pattern": "정책안내",
        "tests": ["세 변의 합 기준", "가장 긴 변 기준 오답 차단"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "박스 크기는 어떻게 재나요? 제일 긴 쪽 기준인가요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["세 변"],
                    "forbid": ["가장 긴 변", "제일 긴 쪽을 기준"],
                    "rubric": "정책 §3.1: 포장이 완료된 상태에서 가로·세로·높이를 각각 측정한 뒤 세 변의 길이를 합산한다. 가장 긴 변 기준이 아니다. 고객이 오답을 먼저 제시했으므로 그대로 긍정하면 실패.",
                    "reference": "포장이 완료된 상태에서 가로·세로·높이를 각각 재신 뒤 세 변의 길이를 더한 값을 기준으로 합니다. 예를 들어 40cm·30cm·20cm면 세 변 합 90cm입니다.",
                },
            },
        ],
    },
    {
        "conv_id": "N-005",
        "title": "운송장 쇼핑몰 미등록 첫 문의 → 점검 절차 안내 (성급한 이관 금지)",
        "route": "BIZ_BULK",
        "route_original": "ORDER_SYNC",
        "pattern": "정책안내",
        "tests": ["🔧 첫 문의에 이관 금지", "전송 가능 상태 조건"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "쿠팡 주문인데 운송장번호가 쇼핑몰에 등록이 안 돼요."},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["집하"],
                    "forbid": ["담당자에게 연결", "시스템 오류입니다"],
                    "rubric": "정책 §8.2: 첫 문의에는 점검 순서를 안내한다 — 운송장 발급 여부, 주문연동 접수 건인지, 전송 가능 상태인지(방문택배는 집하, 편의점택배는 운송장출력). 이관은 §10.2 에 따라 반복 안내 후에도 지속될 때다. 첫 턴에 이관하면 실패.",
                    "reference": "운송장번호가 정상 발급됐는지, 주문연동을 통해 접수한 예약이 맞는지, 운송장 전송이 가능한 상태인지(방문택배는 집하 상태, 편의점택배는 운송장출력 상태) 순서대로 확인해 주세요. 그래도 등록되지 않으면 쇼핑몰명·주문번호·운송장번호를 알려주시면 확인해 드리겠습니다.",
                },
            },
        ],
    },
]


# 채점기 자체 검증(scripts/check_grader.py)이 잡아낸 정답셋 결함을 고친다.
# 모범 답안이 자기 기준을 못 넘기면 그 기준이 틀린 것이다.
PATCHES = [
    {
        "conv_id": "C-010",
        "turn": 2,
        "replace_expect": {
            "action": "ANSWER",
            "tools": [],
            "must": ["액체류"],
            "forbid": ["무조건 보낼 수 있습니다", "택배사에 따라 가능합니다"],
            "rubric": (
                "정책 §7.1 이 바뀌었다. 액체류·액상물질은 '택배운송에 적합하지 않은 물품·포장' "
                "군에 들어 접수할 수 없다. 옛 기준은 '택배사마다 다르다' 였는데, 근거 문서가 "
                "바뀌었으므로 정답도 따라 바뀐다 — 수치를 올리려 기준을 느슨하게 한 것이 아니라 "
                "출처가 달라진 것이다."
            ),
            "reference": (
                "액체류는 택배운송에 적합하지 않은 물품으로 분류되어 접수가 어렵습니다. "
                "포장 방법에 따라 달라질 수 있는 품목은 어떻게 포장하면 되는지 함께 안내해 "
                "드리겠습니다."
            ),
        },
        "why": (
            "근거 문서 §7 을 실제 택배 취급제한 기준으로 교체하면서 액체류가 접수 불가 군으로 "
            "옮겨졌다. 모범 답안이 옛 기준을 말하고 있으면 check_grader 가 먼저 걸린다."
        ),
    },
    {
        "conv_id": "C-004",
        "turn": 2,
        "drop_must_ask": ["선호하는 택배사(없으면 상관없음으로 확인)"],
        "why": (
            "다량할인 상품은 택배사가 아니라 박스 수량으로 갈린다(정책 §4.2, "
            "mockdata bulk_discount 는 수량별 상품 목록이다). 조회 도구도 carrier 를 받지 않는다. "
            "택배사를 되물으라는 기준은 모범 답안 자신도 지키지 않는다."
        ),
    },
]


# 화면 경로 문의. 원본 정답셋에는 "무엇이 얼마인가"만 있고 "어디서 하는가"가 없었다.
# 실제 문의에서 큰 비중을 차지하는데 매뉴얼에도 없어서, §2(예약 경로)와 §5.4(예약
# 내역·배송 조회 경로, 3개월 보관)를 먼저 매뉴얼에 넣고 그 근거로 저작했다.
# 둘 다 건별로 달라지는 값이 아니므로 도구를 부르지 않는다 — tools=[] 가 정답이다.
NAVIGATION_CONVERSATIONS = [
    {
        "conv_id": "N-006",
        "title": "처음 이용 — 예약 경로 안내로 끝난다",
        "route": "RESERVE_GENERAL",
        "route_original": "RESERVE_GENERAL",
        "pattern": "정책안내",
        "tests": ["🔧 불필요한 조회 금지", "경로 안내로 종결"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "택배 예약 어디서 해요? 처음이라 하나도 모르겠어요."},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["로그인", "예약"],
                    "forbid": ["3800", "예약번호를 알려주시면"],
                    "rubric": "정책 §2: 예약은 로그인 후 예약 메뉴에서 시작한다. 처음 이용 문의는 경로 안내로 끝낸다. 운임·상태 조회는 필요 없다.",
                    "reference": "앱이나 웹에 로그인하신 뒤 예약 메뉴에서 서비스를 고르시면 예약이 시작됩니다. 서비스 선택부터 결제까지 화면 안내를 따라가시면 됩니다.",
                },
            },
        ],
    },
    {
        "conv_id": "N-007",
        "title": "전화 예약 문의 — 온라인 전용 안내",
        "route": "RESERVE_GENERAL",
        "route_original": "RESERVE_GENERAL",
        "pattern": "정책안내",
        "tests": ["전화 접수 불가", "대신 접수해 주지 않음"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "전화로 예약되나요? 지금 대신 좀 해주세요."},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["온라인"],
                    "forbid": ["대신 예약해 드리겠습니다", "접수해 드렸습니다"],
                    "rubric": "정책 §2·§10.1: 전화 예약을 받지 않는 온라인 전용 서비스다. 상담 채널이 접수를 대신하지 않는다. 경로를 안내하고 끝낸다.",
                    "reference": "전화로는 예약을 받지 않습니다. 온라인 전용 서비스라 앱이나 웹에서 로그인하신 뒤 예약 메뉴에서 직접 접수해 주셔야 합니다.",
                },
            },
        ],
    },
    {
        "conv_id": "N-008",
        "title": "예약 내역이 안 보임 — 조회 기간 3개월",
        "route": "SPEC_SHIPPING",
        "route_original": "SPEC_SHIPPING",
        "pattern": "정책안내",
        "tests": ["🔧 3개월 보관 기간", "계정 확인 우선"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "예약한 내역이 사라졌어요. 확인할 수가 없네요."},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["3개월", "계정"],
                    "forbid": ["예약이 취소되었습니다", "삭제되었습니다"],
                    "rubric": "정책 §5.4: 예약현황은 예약할 때 쓴 계정으로, 최근 3개월까지만 조회된다. 안 보인다고 취소·삭제로 단정하면 실패(§0 원칙 2).",
                    "reference": "예약현황 메뉴는 예약하실 때 쓰신 계정으로 로그인해야 보이고, 최근 3개월 이내 예약만 조회됩니다. 계정과 예약일을 먼저 확인해 주시겠어요?",
                },
            },
        ],
    },
    {
        "conv_id": "N-009",
        "title": "배송조회 경로 → 상태는 조회 없이 단정하지 않는다",
        "route": "SPEC_SHIPPING",
        "route_original": "SPEC_SHIPPING",
        "pattern": "식별자요청",
        "tests": ["경로 안내", "🔧 상태 단정 금지"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "배송 조회는 어디서 해요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["예약현황"],
                    "forbid": ["배송 중입니다", "도착했습니다"],
                    "rubric": "정책 §5.4: 예약현황 화면에서 예약정보와 배송상태를 함께 본다. 상태 자체는 조회 없이 말하지 않는다.",
                    "reference": "예약현황 메뉴에서 예약 정보와 배송 상태를 함께 확인하실 수 있습니다.",
                },
            },
            {"turn": 3, "role": "customer", "text": "제 건 지금 어디쯤이에요?"},
            {
                "turn": 4,
                "role": "agent",
                "expect": {
                    "action": "ASK",
                    "tools": [],
                    "must_ask": ["운송장번호 또는 예약번호"],
                    "must": [],
                    "forbid": ["배송 중입니다", "오늘 도착합니다"],
                    "rubric": "정책 §0 원칙 2: 배송 상태는 조회 없이 말하지 않는다. 식별자가 없으면 되묻는다.",
                    "reference": "운송장번호나 예약번호를 알려주시면 현재 상태를 확인해 드리겠습니다.",
                },
            },
        ],
    },
]


# 대화를 맺는 턴. "추가로 궁금한 점 있으신가요?" 에 "없어요" 라고 답하는 자리가
# 정답셋에 없었고, 그래서 그 턴이 갈 곳이 없어 이관으로 샜다. 종료는 우리가 못
# 푼 것이 아니라 대화가 끝난 것이다.
CLOSING_CONVERSATIONS = [
    {
        "conv_id": "N-010",
        "title": "안내 후 종료 — 더 물을 것이 없다",
        "route": "SPEC_SHIPPING",
        "route_original": "SPEC_SHIPPING",
        "pattern": "정책안내",
        "tests": ["🔧 종료를 이관으로 취급 금지", "새 정보 덧붙이지 않기"],
        "turns": [
            {"turn": 1, "role": "customer", "text": "택배 내역은 어디서 조회해요?"},
            {
                "turn": 2,
                "role": "agent",
                "expect": {
                    "action": "ANSWER",
                    "tools": [],
                    "must": ["예약현황"],
                    "forbid": ["배송 중입니다"],
                    "rubric": "정책 §5.4: 예약현황 메뉴에서 예약정보와 배송상태를 함께 본다.",
                    "reference": "예약 내역과 배송 상태는 예약현황 메뉴에서 확인하실 수 있습니다. 예약할 때 쓰신 계정으로 로그인하셔야 보입니다.",
                },
            },
            {"turn": 3, "role": "customer", "text": "없어요"},
            {
                "turn": 4,
                "role": "agent",
                "expect": {
                    "action": "CLOSE",
                    "tools": [],
                    "must": ["감사"],
                    "forbid": ["담당자에게 연결", "죄송합니다", "3개월"],
                    "rubric": "더 물을 것이 없다는 뜻이다. 대화를 맺는다. 이관도 아니고, 안내를 덧붙일 자리도 아니다.",
                    "reference": "이용해 주셔서 감사합니다. 도움이 필요하시면 언제든지 말씀해 주세요.",
                },
            },
        ],
    },
]


def apply_patches(payload: dict) -> list[str]:
    applied = []
    for patch in PATCHES:
        conv = next((c for c in payload["conversations"] if c["conv_id"] == patch["conv_id"]), None)
        if conv is None:
            continue
        for turn in conv["turns"]:
            if turn.get("turn") != patch["turn"] or not turn.get("expect"):
                continue
            if patch.get("replace_expect"):
                if turn["expect"] != patch["replace_expect"]:
                    turn["expect"] = dict(patch["replace_expect"])
                    applied.append(f"{patch['conv_id']}#{patch['turn']}")
                continue
            before = turn["expect"].get("must_ask", [])
            after = [a for a in before if a not in patch["drop_must_ask"]]
            if before != after:
                turn["expect"]["must_ask"] = after
                turn["expect"]["rubric"] += f" (기준 수정: {patch['why']})"
                applied.append(f"{patch['conv_id']}#{patch['turn']}")
    return applied


def main() -> int:
    payload = json.loads(PATH.read_text(encoding="utf-8"))
    existing = {c["conv_id"] for c in payload["conversations"]}

    added = 0
    for conv in NEW_CONVERSATIONS + NAVIGATION_CONVERSATIONS + CLOSING_CONVERSATIONS:
        if conv["conv_id"] in existing:
            continue
        payload["conversations"].append(conv)
        added += 1

    patched = apply_patches(payload)

    for conv in payload["conversations"]:
        conv["split"] = "fewshot" if conv["conv_id"] in FEWSHOT else "eval"

    payload["schema"]["split"] = (
        "fewshot = 프롬프트 예시 전용(점수에 넣지 않음) / eval = 채점 전용. "
        "과제 요구 '예시로 쓸 것과 점수를 잴 것을 갈라 둔다'를 코드로 강제한다."
    )
    PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"새 대화 {added}개 추가 (이미 있으면 건너뛴다)")
    print(f"기준 수정 {len(patched)}건: {', '.join(patched) or '없음'}\n")
    for split in ("fewshot", "eval"):
        convs = [c for c in payload["conversations"] if c["split"] == split]
        turns = sum(1 for c in convs for t in c["turns"] if t.get("expect"))
        counts = Counter(c["route"] for c in convs)
        print(f"{split:<8} 대화 {len(convs):>2}개 / 채점 턴 {turns:>2}개")
        for route in ROUTE_LIST:
            if counts.get(route):
                print(f"           {route:<17} {counts[route]}")
    print("\nOK extend_goldenset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
