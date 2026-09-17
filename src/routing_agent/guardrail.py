"""답변에 근거 없는 수치가 섞였는지 기계적으로 검사한다. LLM 을 부르지 않는다.

정책 §10.3 이 금지한 것 중 기계로 잡을 수 있는 것 하나를 잡는다 —
"매뉴얼에도 화면에도 없는 수치를 만들어 안내하는 것".

답변에 나온 수를 모두 뽑아, 조회 결과나 근거 문서에서 같은 수를 찾지 못하면
위반으로 표시한다. 문장 구조의 일부인 작은 수(1~3문장, 2~3일)는 제외한다.
"""

from __future__ import annotations

import re

# 이 값 미만은 검사하지 않는다. "1~3문장", "2kg", "세 변" 처럼 금액이 아닌 수가 대부분이다.
MIN_AMOUNT = 1000

_NUMBER = re.compile(r"\d[\d,]*")


def _norm(text: str) -> str:
    """콤마를 없애 3,800 과 3800 을 같은 것으로 본다."""
    return text.replace(",", "")


def _numbers(text: str) -> list[int]:
    out = []
    for match in _NUMBER.finditer(text):
        digits = _norm(match.group(0))
        if digits.isdigit():
            out.append(int(digits))
    return out


def check_guardrail(answer: str, tool_results: list[str], context: str = "", said: str = "") -> dict:
    """근거에서 찾을 수 없는 큰 수를 골라낸다.

    `said` 는 고객이 직접 말한 것(이번 문의와 이전 대화)이다. 고객이 준
    예약번호·운송장번호를 되읽어 주는 것은 날조가 아니므로 근거로 친다.
    """
    grounded: set[int] = set()
    for source in [*tool_results, context, said]:
        grounded.update(_numbers(str(source)))

    # 합·차로 설명되는 값도 허용한다 — "3,800원 + 제주 3,000원 = 6,800원" 같은
    # 안내가 정상이기 때문이다. 다만 **이번 턴의 조회 결과와 고객 발화**에서 나온
    # 값끼리만 더한다.
    #
    # 근거 문서 전체를 산술 재료로 쓰면 구멍이 너무 커진다. 매뉴얼 운임표에는
    # 1,000 이상인 수가 수십 개 있어 두 값의 합·차만으로도 수백 가지가 "근거 있는
    # 값"이 된다. 실제로 지어낸 6,000원이 매뉴얼의 3,000원 + 3,000원 으로 설명돼
    # 그냥 통과했다. 조회하지도 않은 표의 아무 두 값을 더한 것은 근거가 아니다.
    operands: set[int] = set()
    for source in [*tool_results, said]:
        operands.update(_numbers(str(source)))
    derived: set[int] = set()
    values = sorted(v for v in operands if v >= MIN_AMOUNT)
    for i, a in enumerate(values):
        for b in values[i:]:
            derived.add(a + b)
            derived.add(abs(a - b))

    unsupported = []
    for value in _numbers(answer):
        if value < MIN_AMOUNT:
            continue
        if value in grounded or value in derived:
            continue
        unsupported.append(value)

    return {
        "ok": not unsupported,
        "unsupported": sorted(set(unsupported)),
        "checked_min": MIN_AMOUNT,
        "grounded_count": len(grounded),
    }


def _main() -> None:
    cases = [
        ("근거 있는 금액", "롯데택배 기준 3,800원입니다.", ['{"fee": 3800}'], True),
        ("근거 없는 금액", "롯데택배 기준 4,500원입니다.", ['{"fee": 3800}'], False),
        ("합산 허용", "운임 3,800원에 제주 3,000원이 더해져 6,800원입니다.", ['{"fee": 3800, "surcharge": 3000}'], True),
        ("작은 수 무시", "1~3문장으로 안내드립니다. 2kg 기준입니다.", [], True),
        ("근거 문서에서 찾음", "기본 운임은 3,900원입니다.", [], True),
        # 실제로 화면에서 새어 나간 값. 매뉴얼의 3,000+3,000 으로 설명돼 통과했었다.
        ("문서 값끼리의 합은 근거가 아니다", "기본 운임은 6,000원입니다.", [], False),
    ]
    from routing_agent.context import build_context

    context = build_context("VISIT_PICKUP")
    ok = True
    for label, answer, results, expected in cases:
        ctx = context if "근거 문서" in label else ""
        got = check_guardrail(answer, results, ctx)["ok"]
        mark = "ok  " if got == expected else "FAIL"
        ok &= got == expected
        print(f"  {mark} {label}: 기대={expected} 실제={got}")
    print("OK guardrail" if ok else "FAIL guardrail")


if __name__ == "__main__":
    _main()
