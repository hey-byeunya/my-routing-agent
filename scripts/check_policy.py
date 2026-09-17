"""이관·반복 판단을 LLM 없이 검증한다.

"언제 사람에게 넘기는가"는 이 에이전트에서 가장 조용히 틀리는 자리다. 틀려도
답변은 그럴듯하게 나오고, 평가 수치도 한두 칸밖에 안 움직인다. 실제로 두 번
틀렸다 — 범위 밖 문의를 이관했고(#5), 다른 사안을 물었는데 반복 문의로
이관했다(#23). 그래서 판단을 순수 함수로 꺼내 여기서 고정한다.

    python scripts/check_policy.py
"""

from __future__ import annotations

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

    print()
    if FAILED:
        print(f"FAIL check_policy — {len(FAILED)}건 실패: {', '.join(FAILED)}")
        return 1
    print("OK check_policy — 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
