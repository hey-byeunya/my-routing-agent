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


# ---------------------------------------------------------------- 근거 없는 단정

# "X 는 ~ 가능합니다" 처럼 **무엇에 대해 가능·불가를 단정하는** 문장을 찾는다.
# 주어 자리의 낱말이 근거 어디에도 없으면, 우리는 모르는 것을 두고 규정을 만든 것이다.
_CLAIM = re.compile(
    r"([가-힣A-Za-z0-9]{2,})(?:은|는|이|가)\s*[^.!?]{0,40}?"
    r"(가능합니다|불가합니다|불가능합니다|됩니다|안\s?됩니다|할\s?수\s?있습니다|할\s?수\s?없습니다)"
)

# 서술어·부사처럼 주어로 볼 수 없는 것들. 이 낱말이 주어 자리에 잡히면 흘린다.
_NOT_SUBJECT = {
    "이용", "확인", "안내", "조회", "문의", "신청", "접수", "발송", "배송", "예약",
    "취소", "변경", "결제", "사용", "처리", "출력", "등록", "수거", "이것", "그것",
    "저희", "고객", "해당", "경우", "이후", "이전", "지금", "현재", "특정", "조건",
}


def unsupported_claims(answer: str, context: str = "", tool_results: list[str] | None = None,
                       said: str = "") -> list[str]:
    """근거에 없는 것을 두고 가능·불가를 단정했는가.

    가드레일이 숫자만 보는 탓에 `"사과는 예약 취소 후에 가능합니다"` 같은
    **없는 규정**이 그대로 나갔다(#36). 정책 §10.3 이 금지한 것의 절반이 이쪽인데
    검사가 없었다. 숫자와 같은 방식으로 — 근거에 있으면 통과, 없으면 잡는다.

    고객이 말한 낱말이라는 것만으로는 근거가 되지 않는다. 고객이 "사과" 라고 했다고
    사과에 대한 규정이 생기지는 않기 때문이다. 그래서 `said` 는 근거로 세지 않는다.
    """
    grounded = (context or "") + "\n" + "\n".join(str(t) for t in (tool_results or []))
    out: list[str] = []
    for subject, _verb in _CLAIM.findall(answer or ""):
        word = subject.strip()
        if len(word) < 2 or word in _NOT_SUBJECT or word.isdigit():
            continue
        if word in grounded:
            continue
        # 앞 두 글자만 걸쳐도 근거에 있는 말로 본다 ("편의점택배" vs "편의점")
        if len(word) >= 3 and word[:2] in grounded:
            continue
        out.append(word)
    return sorted(set(out))


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

    # 숫자만 보면 "X 는 ~ 가능합니다" 같은 **말로 하는 단정**이 그대로 나간다.
    # 같은 방식으로 본다 — 근거에 있으면 통과, 없으면 잡는다.
    claims = unsupported_claims(answer, context, tool_results)

    return {
        "ok": not unsupported and not claims,
        "unsupported": sorted(set(unsupported)),
        "unsupported_claims": claims,
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


# ---------------------------------------------------------------- 되풀이 검사

_PUNCT = re.compile(r"[^0-9A-Za-z가-힣]+")


def _shape(text: str) -> str:
    """문장부호·공백을 걷어낸 뼈대. 같은 말인지 비교할 때만 쓴다."""
    return _PUNCT.sub("", text or "")


def is_repeat_answer(answer: str, history: list | None, threshold: float = 0.9) -> bool:
    """직전에 우리가 한 말을 그대로 되풀이하려는가.

    "예약 한 거 없는데?" 라고 했는데 앞 턴과 **똑같은 문장**을 다시 내보내면
    대화가 제자리를 돈다. 고객은 답을 못 받았는데 우리는 답했다고 여긴다.

    프롬프트로 "되풀이하지 마라" 고 적어 봤지만 듣지 않았다(#36). 모델에게
    부탁하는 대신 파이썬이 센다 — 이 프로젝트가 판정은 LLM, 셈과 임계값은
    파이썬으로 가르는 것과 같은 자리다.
    """
    if not answer or not history:
        return False
    previous = [item[1] for item in history if item and item[0] == "agent" and len(item) > 1]
    if not previous:
        return False
    now = _shape(answer)
    if not now:
        return False
    last = _shape(previous[-1])
    if not last:
        return False
    # 짧은 쪽이 긴 쪽에 통째로 들어가면 같은 말로 본다. 꼬리만 붙인 경우를 잡는다.
    short, long_ = (now, last) if len(now) <= len(last) else (last, now)
    if short and short in long_ and len(short) / len(long_) >= threshold:
        return True
    return now == last
