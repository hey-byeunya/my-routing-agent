"""hard_cases 를 dev / eval 로 가른다. 다시 돌려도 같은 결과가 나온다.

왜 가르는가 — 경계모호·다중의도 사례는 분류 지침을 벼리는 데 가장 좋은 재료인데,
그게 곧 평가셋이다. 예시로 쓰면 그 수치가 부풀려진다. 프롬프트를 고칠 때 보는 것과
점수를 재는 것을 갈라 둔다. routing_answers 의 fewshot/eval 분리와 같은 취지다.

되물음 유형(극단단답·문맥의존)은 가르지 않는다. 카테고리를 맞히는 문제가 아니라
"되물었는가" 로 재는 갈래이고 이미 1.000 이라, 지침을 벼릴 재료가 아니다.

무작위로 뽑지 않는다. 유형별로 qa_id 순 등간격으로 집는다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "hard_cases.csv"

# 라우팅으로 재는 유형만 가른다. 값은 dev 로 뺄 개수.
DEV_QUOTA = {"경계모호": 3, "다중의도": 2, "텍스트손상": 2, "분류체계밖": 2}


def evenly_spaced(items: list, k: int) -> list:
    if k >= len(items):
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def main() -> int:
    with PATH.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if "split" not in fields:
        fields.append("split")

    by_type: dict[str, list] = defaultdict(list)
    for row in rows:
        by_type[row["hard_type"]].append(row)

    dev_ids: set[str] = set()
    for hard_type, quota in DEV_QUOTA.items():
        picked = evenly_spaced(sorted(by_type[hard_type], key=lambda r: r["qa_id"]), quota)
        dev_ids.update(r["qa_id"] for r in picked)

    for row in rows:
        row["split"] = "dev" if row["qa_id"] in dev_ids else "eval"

    with PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{'유형':<12}{'전체':>4}{'dev':>5}{'eval':>6}")
    for hard_type in sorted(by_type):
        sub = by_type[hard_type]
        dev = sum(1 for r in sub if r["split"] == "dev")
        print(f"{hard_type:<12}{len(sub):>4}{dev:>5}{len(sub) - dev:>6}")
    counts = Counter(r["split"] for r in rows)
    print(f"{'합계':<12}{len(rows):>4}{counts['dev']:>5}{counts['eval']:>6}")
    print("\ndev 는 지침을 벼릴 때만 본다. 점수는 eval 로만 잰다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
