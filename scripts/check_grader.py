"""채점기 자체 검증.

정답셋의 모범 답안(reference)과 기대 도구를 그대로 넣어 전부 만점이 나오는지 본다.
모범 답안이 떨어지면 채점기가 틀린 것이지 에이전트가 틀린 것이 아니다.
여기가 통과하기 전의 성능 수치는 믿지 않는다.

기본은 LLM 없이 문자열 매칭만으로 돈다(--llm 을 주면 판정기까지 함께 검증).
LLM 없이도 통과해야 채점의 바닥이 단단하다고 말할 수 있다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dataset import gold_turns  # noqa: E402
from src.grader import failure_reason, grade_turn, summarize  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="채점기 자체 검증")
    parser.add_argument("--llm", action="store_true", help="LLM 판정기까지 함께 검증한다")
    parser.add_argument("--backend", default=None)
    args = parser.parse_args()

    llm = None
    if args.llm:
        from src.llm_backends import make_llm

        llm = make_llm(args.backend)

    turns = list(gold_turns())
    results = []
    failures = []
    for turn in turns:
        expect = turn.expect
        result = grade_turn(
            expect=expect,
            answer=expect.get("reference", ""),
            called=list(expect.get("tools", [])),
            llm=llm,
            action=expect.get("action"),
        )
        results.append(result)
        if not (result["tools_ok"] and result["answer_ok"]):
            failures.append((turn, result))

    stats = summarize(results)
    mode = "LLM 판정 포함" if llm else "문자열 매칭만"
    print(f"모범 답안 {stats['n']}턴 채점 ({mode})")
    print(f"  도구 호출 적절성 {stats['tool_score']:.3f}")
    print(f"  답변 적절성      {stats['answer_score']:.3f}")

    if failures:
        print(f"\nFAIL check_grader — 모범 답안 {len(failures)}턴이 떨어졌다. 채점기를 고쳐야 한다.\n")
        for turn, result in failures:
            print(f"  {turn.item_id} [{turn.route}] {failure_reason(result)}")
            if result["missing"]:
                print(f"     못 찾은 필수 사실: {result['missing']}")
            if result["violated"]:
                print(f"     금지어 오탐:       {result['violated']}")
            if result["unasked"]:
                print(f"     못 찾은 되물음:    {result['unasked']}")
            if not result["tools_ok"]:
                print(f"     도구: 기대 {result['expected_tools']} vs 실제 {result['actual_tools']}")
            print(f"     모범 답안: {turn.expect.get('reference', '')[:110]}")
        return 1

    judged = Counter(f for r in results for f in r["judged_by_llm"])
    print(f"\nOK check_grader — 모범 답안 {stats['n']}턴 전부 통과")
    if judged:
        print(f"  문자열로는 못 찾고 LLM 판정으로 통과한 사실 {sum(judged.values())}건: {list(judged)[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
