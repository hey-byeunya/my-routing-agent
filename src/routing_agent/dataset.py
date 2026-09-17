"""평가셋·예시셋 로딩. 둘을 가르는 일을 코드로 강제한다.

과제가 "프롬프트에 예시로 쓸 것과 점수를 잴 것을 갈라 둔다"를 요구한다.
사람이 지키는 규칙으로 두면 언젠가 샌다. fewshot 은 fewshot_examples() 로만,
eval 은 eval_items() 로만 나가게 하고, 두 집합이 겹치면 예외를 던진다.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# 저장소 루트. 패키지가 src/routing_agent/ 에 있으므로 두 단계 위가 아니라 세 단계 위다.
BASE = Path(__file__).resolve().parents[2]
DATA = BASE / "data"


@dataclass(frozen=True)
class Inquiry:
    qa_id: str
    question: str
    route: str  # 병합된 5+OTHER 기준 정답
    route_original: str
    split: str  # eval | fewshot | outscope


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def inquiries() -> tuple[Inquiry, ...]:
    questions = {row["qa_id"]: row["question"] for row in _read_csv(DATA / "customer_inquiries.csv")}
    items: list[Inquiry] = []
    for row in _read_csv(DATA / "routing_answers.csv"):
        qa_id = row["qa_id"]
        if qa_id not in questions:
            continue
        items.append(
            Inquiry(
                qa_id=qa_id,
                question=questions[qa_id],
                route=row["route_v2"],
                route_original=row["route"],
                split=row["split"],
            )
        )
    return tuple(items)


def fewshot_examples() -> list[Inquiry]:
    """프롬프트 예시 전용. 점수를 재는 데 쓰면 안 된다."""
    return [i for i in inquiries() if i.split == "fewshot"]


def eval_items() -> list[Inquiry]:
    """점수 전용. eval 과 outscope(범위 밖 전용)를 함께 쓴다."""
    return [i for i in inquiries() if i.split in ("eval", "outscope")]


def assert_split_disjoint() -> None:
    few = {i.qa_id for i in fewshot_examples()}
    ev = {i.qa_id for i in eval_items()}
    overlap = few & ev
    if overlap:
        raise AssertionError(f"예시셋과 평가셋이 겹친다: {sorted(overlap)}")


# ---------------------------------------------------------------- 답변 정답셋


@dataclass(frozen=True)
class GoldTurn:
    conv_id: str
    turn: int
    route: str
    history: tuple[tuple[str, str, str], ...]  # 이 턴 직전까지의 (역할, 발화, 카테고리)
    question: str  # 직전 고객 발화
    expect: dict
    split: str

    @property
    def item_id(self) -> str:
        return f"{self.conv_id}#{self.turn}"


@lru_cache(maxsize=1)
def gold_turns() -> tuple[GoldTurn, ...]:
    """정답셋을 '채점 가능한 에이전트 턴' 단위로 펼친다."""
    payload = json.loads((DATA / "answer_goldenset.json").read_text(encoding="utf-8"))
    out: list[GoldTurn] = []
    for conv in payload["conversations"]:
        # 이력 항목에 그 대화의 카테고리를 함께 싣는다. 정답셋은 한 대화가 한 사안이라
        # 대화의 route 를 그대로 쓰면 된다. 이게 있어야 "같은 사안을 몇 번째 묻는가"를
        # 셀 수 있다 (agent.conversation_progress).
        history: list[tuple[str, str, str]] = []
        last_customer = ""
        for turn in conv["turns"]:
            if turn["role"] == "customer":
                last_customer = turn["text"]
                history.append(("customer", turn["text"], conv["route"]))
                continue
            expect = turn.get("expect")
            if expect:
                out.append(
                    GoldTurn(
                        conv_id=conv["conv_id"],
                        turn=turn["turn"],
                        route=conv["route"],
                        history=tuple(history[:-1]),
                        question=last_customer,
                        expect=expect,
                        split=conv.get("split", "eval"),
                    )
                )
            # 이어지는 턴의 이력에는 모범 답안을 넣는다. 실행 결과가 아니라 정답을 물려야
            # 뒤 턴의 채점이 앞 턴 성패에 오염되지 않는다.
            history.append(("agent", (expect or {}).get("reference", ""), conv["route"]))
    return tuple(out)


def gold_eval_turns() -> list[GoldTurn]:
    return [t for t in gold_turns() if t.split == "eval"]


def gold_fewshot_turns() -> list[GoldTurn]:
    return [t for t in gold_turns() if t.split == "fewshot"]


def _main() -> None:
    from collections import Counter

    assert_split_disjoint()
    print("라우팅 데이터")
    for split in ("fewshot", "eval", "outscope"):
        rows = [i for i in inquiries() if i.split == split]
        counts = Counter(i.route for i in rows)
        print(f"  {split:<9} {len(rows):>3}건  {dict(counts)}")

    print("\n답변 정답셋 (에이전트 턴)")
    for split in ("fewshot", "eval"):
        rows = [t for t in gold_turns() if t.split == split]
        counts = Counter(t.route for t in rows)
        print(f"  {split:<9} {len(rows):>3}턴  {dict(counts)}")
    convs = {t.conv_id for t in gold_turns()}
    print(f"  대화 {len(convs)}개")


if __name__ == "__main__":
    _main()
