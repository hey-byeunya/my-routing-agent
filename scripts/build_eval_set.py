"""카테고리 개수를 맞춘 라우팅 평가셋(data/eval_set.csv)을 만든다.

병합 뒤 BIZ_BULK 가 22건으로 몰려 있어 그대로 재면 그 카테고리 성적이
전체 수치를 끌고 간다. 과제가 "카테고리별 개수를 비슷하게 맞춘다"를
요구하므로 가장 적은 카테고리에 맞춰 하향 샘플링한다.

무작위로 뽑지 않는다. qa_id 순으로 등간격으로 집어 세부 주제가 한쪽으로
쏠리지 않게 하고, 다시 돌려도 같은 평가셋이 나오게 한다.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path


from routing_agent.dataset import eval_items
from routing_agent.schemas import ROUTE_LIST

OUT = Path(__file__).resolve().parent.parent / "data" / "eval_set.csv"


def evenly_spaced(items: list, k: int) -> list:
    """앞에서 k개를 자르지 않고 등간격으로 집는다. 세부 주제가 고루 섞이게."""
    if k >= len(items):
        return items
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


def main() -> int:
    by_route: dict[str, list] = defaultdict(list)
    for item in eval_items():
        by_route[item.route].append(item)
    for route in by_route:
        by_route[route].sort(key=lambda i: i.qa_id)

    per_route = min(len(v) for v in by_route.values())
    print(f"카테고리별 보유: {{{', '.join(f'{r}: {len(by_route[r])}' for r in ROUTE_LIST)}}}")
    print(f"가장 적은 카테고리에 맞춰 카테고리당 {per_route}건씩 뽑는다\n")

    picked = []
    for route in ROUTE_LIST:
        picked += evenly_spaced(by_route[route], per_route)
    picked.sort(key=lambda i: i.qa_id)

    with OUT.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["qa_id", "question", "route", "route_original", "source_split"])
        for item in picked:
            writer.writerow([item.qa_id, item.question, item.route, item.route_original, item.split])

    counts = Counter(i.route for i in picked)
    for route in ROUTE_LIST:
        print(f"  {route:<17} {counts[route]:>3}")
    print(f"  {'합계':<16} {len(picked):>3}건 → {OUT.relative_to(OUT.parent.parent)}")
    held_out = len(eval_items()) - len(picked)
    print(f"\n남긴 {held_out}건은 평가셋에 넣지 않았다. 균형을 맞추느라 뺀 것이지 버린 것이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
