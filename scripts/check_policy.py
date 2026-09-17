"""이관·반복 판단을 LLM 없이 검증한다.

"언제 사람에게 넘기는가"는 이 에이전트에서 가장 조용히 틀리는 자리다. 틀려도
답변은 그럴듯하게 나오고, 평가 수치도 한두 칸밖에 안 움직인다. 실제로 두 번
틀렸다 — 범위 밖 문의를 이관했고(#5), 다른 사안을 물었는데 반복 문의로
이관했다(#23). 그래서 판단을 순수 함수로 꺼내 여기서 고정한다.

    python scripts/check_policy.py
"""

from __future__ import annotations

from pathlib import Path

from routing_agent.agent import ASK_LIMIT, asks_on_issue, conversation_progress, should_escalate

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(label)


def talk(n: int, route: str = "RESERVE_GENERAL") -> list[tuple]:
    """같은 사안으로 n번 주고받은 이력."""
    return [x for _ in range(n)
            for x in (("customer", "문의", route), ("agent", "안내", route))]


def main() -> int:
    print("이관·반복 판단 검증\n")

    print("[1] 이관 게이트")
    check("확신이 서면 넘기지 않는다", not should_escalate("RESERVE_GENERAL", 0.9, talk(5)))
    check("범위 밖은 확신도와 무관하게 넘기지 않는다 (§10.1)",
          not should_escalate("OTHER", 0.05, talk(5, "OTHER")))
    check("첫 문의는 확신이 없어도 되묻는다 (§10.2 에 '확신도 낮음'은 없다)",
          not should_escalate("RESERVE_GENERAL", 0.1, []))
    for n in range(ASK_LIMIT):
        check(f"같은 사안 {n}번 되물은 뒤에도 아직 되묻는다",
              not should_escalate("RESERVE_GENERAL", 0.1, talk(n)))
    check(f"같은 사안 {ASK_LIMIT}번을 되묻고도 확신이 없으면 넘긴다",
          should_escalate("RESERVE_GENERAL", 0.1, talk(ASK_LIMIT)))
    check("다른 사안을 여러 번 물은 것은 되물음으로 세지 않는다",
          not should_escalate("RESERVE_GENERAL", 0.1, talk(ASK_LIMIT, "CVS_PICKUP")))
    check("카테고리가 없는 옛 이력은 되물음으로 세지 않는다",
          not should_escalate("RESERVE_GENERAL", 0.1, [("customer", "q"), ("agent", "a")]))

    print("\n[2] 같은 사안 세기")
    check("이력이 없으면 0", asks_on_issue([], "RESERVE_GENERAL") == 0)
    check("같은 사안만 센다",
          asks_on_issue(talk(2) + talk(3, "CVS_PICKUP"), "RESERVE_GENERAL") == 2,
          str(asks_on_issue(talk(2) + talk(3, "CVS_PICKUP"), "RESERVE_GENERAL")))
    check("빈 답변은 세지 않는다",
          asks_on_issue([("agent", "", "RESERVE_GENERAL")], "RESERVE_GENERAL") == 0)

    print("\n[3] 계획 단계에 넘기는 사실")
    first = conversation_progress(talk(2, "CVS_PICKUP"), "RESERVE_GENERAL")
    check("다른 사안만 있었으면 '처음 나왔다'고 알린다", "처음" in first, first)
    check("같은 사안이 반복되면 횟수를 준다",
          "3번째" in conversation_progress(talk(2), "RESERVE_GENERAL"),
          conversation_progress(talk(2), "RESERVE_GENERAL"))
    check("3번째부터 §10.2 검토를 덧붙인다",
          "§10.2" in conversation_progress(talk(2), "RESERVE_GENERAL"))
    check("2번째에는 덧붙이지 않는다",
          "§10.2" not in conversation_progress(talk(1), "RESERVE_GENERAL"))
    check("카테고리가 없는 이력에는 아무 말도 하지 않는다",
          conversation_progress([("customer", "q"), ("agent", "a")], "RESERVE_GENERAL") == "")

    # ── 근거 없는 수치를 내보내지 않는가 (정책 §10.3 · §0 원칙1)
    print("\n[근거 없는 수치 차단]")
    from routing_agent.agent import UNVERIFIED_ANSWER_TEXT
    from routing_agent.context import build_context
    from routing_agent.guardrail import check_guardrail

    ctx = build_context("VISIT_PICKUP")
    tool_out = ['{"carrier": "한진택배", "fee": 5000}']

    v_bad = check_guardrail("한진택배 기본 운임은 6,000원입니다.", tool_out, ctx, said="")
    check("조회 결과에 없는 금액을 잡는다", not v_bad["ok"] and 6000 in v_bad["unsupported"],
          str(v_bad["unsupported"]))

    v_ok = check_guardrail("한진택배 기본 운임은 5,000원입니다.", tool_out, ctx, said="")
    check("조회 결과에 있는 금액은 통과시킨다", v_ok["ok"])

    v_said = check_guardrail("예약번호 R-90410 으로 확인해 드리겠습니다.", [], ctx,
                             said="R-90410 취소해주세요")
    check("고객이 말한 번호는 지어낸 것으로 보지 않는다", v_said["ok"])

    # 차단 시 내보내는 문장은 매뉴얼이 정해 둔 것이어야 한다. 우리가 지어낸 문구면
    # "값을 모를 때 이렇게 말한다"는 정책이 코드와 어긋난다.
    policy = (Path(__file__).resolve().parent.parent / "docs" / "policy_courierhub.md").read_text(encoding="utf-8")
    check("차단 시 문구가 매뉴얼 §0 원칙1 에 있는 문장이다",
          UNVERIFIED_ANSWER_TEXT in policy, UNVERIFIED_ANSWER_TEXT)

    # ── 할 수 없는 일을 약속하지 않는가 (§2 — 예약은 고객이 예약 화면에서 한다)
    print("\n[할 수 없는 일]")
    from routing_agent.prompts import ANSWER_RULES, PLAN_RULES

    check("계획 규칙이 새 예약을 대신 넣지 말라고 적고 있다", "새 예약을 대신 넣지 않는다" in PLAN_RULES)
    check("답변 규칙이 할 수 없는 일을 말하지 말라고 적고 있다",
          "우리가 할 수 없는 일을 하겠다고 말하지 않는다" in ANSWER_RULES)

    print()
    if FAILED:
        print(f"FAIL check_policy — {len(FAILED)}건 실패: {', '.join(FAILED)}")
        return 1
    print("OK check_policy — 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
