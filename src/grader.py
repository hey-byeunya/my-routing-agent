"""채점기. 과제가 요구한 두 지표를 잰다.

  ① 도구 호출 적절성 — 실제로 호출한 도구 집합이 기대 도구와 **정확히 일치**하면 1점.
     부분집합이 아니라 정확히 일치여야 한다. 그래야 "필요한 근거를 안 읽은 것",
     "관련 없는 근거를 훑은 것", "넘겨야 하는데 지어낸 것"이 이 하나로 다 걸린다.

  ② 답변 적절성 — must 를 전부 담고 forbid 를 하나도 어기지 않으면 1점.
     표현이 같을 필요는 없다. 문자열로 안 잡히면 LLM 에게 "이 사실이 다른
     표현으로 들어 있는가"만 묻는다. 표현이 아니라 사실을 본다.

채점기 자체를 믿을 수 있는지는 scripts/check_grader.py 가 검증한다 —
정답셋의 모범 답안(reference)을 넣어 전부 통과해야 한다.
"""

from __future__ import annotations

import re
from typing import Any

JUDGE_PROMPT = """\
아래 상담 답변에 특정 사실이 담겨 있는지만 판단해라. 표현이 달라도 사실이 같으면 담긴 것이다.

판단 기준
- 숫자는 표기(3,800 / 3800 / 3천8백)가 달라도 같은 값이면 담긴 것이다.
- 의미가 같은 다른 낱말로 쓰였으면 담긴 것이다.
- 비슷한 이야기를 하지만 그 사실을 말하지 않았으면 담기지 않은 것이다.

[답변]
{answer}

[확인할 사실]
{fact}

담겼으면 yes, 아니면 no. 다른 말은 쓰지 마라."""

FORBID_PROMPT = """\
아래 상담 답변이 금지된 내용을 **사실로 주장**하고 있는지만 판단해라.
그 내용을 부정하거나("~가 아니라", "~하지 않습니다") 바로잡는 문장은 주장이 아니다.

[답변]
{answer}

[금지된 내용]
{item}

주장하고 있으면 y, 아니면 n 한 글자만 출력해라.
"""

ASK_PROMPT = """\
아래 상담 답변이 특정 항목을 고객에게 되묻고 있는지만 판단해라. 표현이 달라도 같은 항목을 물었으면 그렇다.

[답변]
{answer}

[되물어야 할 항목]
{item}

물었으면 yes, 아니면 no. 다른 말은 쓰지 마라."""

_NUMBER_RUN = re.compile(r"(\d[\d,]*)")


def _normalize(text: str) -> str:
    """숫자의 콤마를 없애 3,800 과 3800 을 같게 만든다."""
    return _NUMBER_RUN.sub(lambda m: m.group(1).replace(",", ""), text or "")


def _contains(answer: str, needle: str) -> bool:
    return _normalize(needle) in _normalize(answer)


# must_ask 는 "박스 크기와 중량" 처럼 서술형 라벨이라 통째로는 답변에 절대 안 나온다.
# 낱말 단위로 겹치는지 보고, 그래도 애매하면 LLM 에게 묻는다.
_PARTICLES = ("으로", "와", "과", "을", "를", "은", "는", "이", "가", "의", "에", "도", "만", "로")
_PAREN = re.compile(r"\([^)]*\)")
_SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")


def _tokens(label: str) -> list[str]:
    text = _PAREN.sub(" ", label)  # 괄호 안 부연은 되물음의 핵심이 아니다
    out = []
    for raw in _SPLIT.split(text):
        if len(raw) < 2:
            continue
        for particle in _PARTICLES:
            if len(raw) > len(particle) + 1 and raw.endswith(particle):
                raw = raw[: -len(particle)]
                break
        out.append(raw)
    return out


def _overlap(answer: str, label: str, threshold: float = 0.6) -> bool:
    tokens = _tokens(label)
    if not tokens:
        return False
    hit = 0
    for token in tokens:
        # 어미가 달라도("확인하려는" vs "확인하시려는") 앞 두 글자로 걸리게 한다.
        if token in answer or (len(token) >= 3 and token[:2] in answer):
            hit += 1
    return hit / len(tokens) >= threshold


def _ask_llm(llm, prompt: str) -> bool:
    if llm is None:
        return False
    try:
        return str(llm.invoke(prompt).content).strip().lower().startswith("y")
    except Exception:  # noqa: BLE001 - 판정 실패는 "못 찾았다"로 본다
        return False


def grade_turn(
    expect: dict,
    answer: str,
    called: list[str],
    llm=None,
    action: str | None = None,
) -> dict:
    """한 턴을 채점한다. llm 이 None 이면 문자열 매칭만 쓴다."""
    expected_tools = set(expect.get("tools", []))
    actual_tools = set(called)
    tools_ok = actual_tools == expected_tools

    missing: list[str] = []
    judged: list[str] = []
    for fact in expect.get("must", []):
        if _contains(answer, str(fact)):
            continue
        if _ask_llm(llm, JUDGE_PROMPT.format(answer=answer, fact=fact)):
            judged.append(str(fact))
            continue
        missing.append(str(fact))

    # 금지 항목도 2단이다. 문자열로 잡혀도 부정문일 수 있다 — "제일 긴 쪽 기준이
    # 아니라 세 변의 합"은 금지 내용을 바로잡은 것이지 주장한 것이 아니다.
    violated = []
    for item in expect.get("forbid", []):
        if not _contains(answer, str(item)):
            continue
        if llm is not None and not _ask_llm(llm, FORBID_PROMPT.format(answer=answer, item=item)):
            continue
        violated.append(str(item))

    unasked: list[str] = []
    if expect.get("action") == "ASK":
        for item in expect.get("must_ask", []):
            if _overlap(answer, str(item)):
                continue
            if _ask_llm(llm, ASK_PROMPT.format(answer=answer, item=item)):
                continue
            unasked.append(str(item))

    answer_ok = not missing and not violated and not unasked

    return {
        "tools_ok": tools_ok,
        "answer_ok": answer_ok,
        "action_ok": action is None or action == expect.get("action"),
        "expected_tools": sorted(expected_tools),
        "actual_tools": sorted(actual_tools),
        "missing": missing,
        "violated": violated,
        "unasked": unasked,
        "judged_by_llm": judged,
    }


def failure_reason(result: dict) -> str:
    """실패 원인을 한 낱말로 분류한다. 점수만 세지 않고 원인을 모으기 위해."""
    if not result["tools_ok"]:
        expected, actual = set(result["expected_tools"]), set(result["actual_tools"])
        if expected and not actual:
            return "근거_미조회"
        if actual - expected and not expected:
            return "불필요_조회"
        if actual - expected:
            return "관련없는_조회"
        return "필요한_조회_누락"
    if result["violated"]:
        return "금지_내용_언급"
    if result["missing"]:
        return "필수_사실_누락"
    if result["unasked"]:
        return "되물음_누락"
    return "통과"


def summarize(results: list[dict]) -> dict[str, Any]:
    n = len(results) or 1
    return {
        "n": len(results),
        "tool_score": sum(r["tools_ok"] for r in results) / n,
        "answer_score": sum(r["answer_ok"] for r in results) / n,
        "action_score": sum(r["action_ok"] for r in results) / n,
        "both": sum(r["tools_ok"] and r["answer_ok"] for r in results) / n,
    }
