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

# 빈자리를 메우려고 저작한 문의는 **반드시 평가셋에 넣는다.**
# 등간격 샘플링은 qa_id 순으로 집으므로, 뒤에 붙인 문의는 그 카테고리에 원본이 많으면
# 그대로 떨어져 나간다. 실제로 그렇게 됐다 — 운임 문의 6건을 더하고 다시 뽑았더니
# 넷이 빠지고 둘만 남았다. 메우려던 자리가 그대로 비어 있는 셈이라, 평가셋을 늘린
# 뜻이 없어진다. 그래서 먼저 집고, 남은 자리를 등간격으로 채운다.
#
# **꼭 필요한 것만 고정한다.** 경로 문의(490001~490005)도 같은 뜻으로 저작한 것이지만
# 고정하지 않는다 — 고정하지 않아도 샘플러가 집고, 고정하면 정원에 밀려 원본 5건이
# 빠져 나가 이전 회차와 같은 문항으로 견줄 수 없게 된다. 지금 평가셋 48건은 옛 42건을
# **그대로 품은 상위집합**이다.
PINNED = {
    "490006", "490007", "490008", "490009", "490010", "490011",  # 운임 문의 (서비스+규격+금액)
}


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
        pinned = [i for i in by_route[route] if i.qa_id in PINNED]
        if len(pinned) > per_route:
            raise SystemExit(
                f"{route}: 고정 문항 {len(pinned)}건이 카테고리 정원 {per_route}건을 넘는다. "
                "정원을 늘리거나 고정을 줄여야 한다"
            )
        rest = [i for i in by_route[route] if i.qa_id not in PINNED]
        picked += pinned + evenly_spaced(rest, per_route - len(pinned))
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
    print(f"고정 문항 {sum(1 for i in picked if i.qa_id in PINNED)}건은 빈자리를 메우려고 저작한 것이라 "
          "등간격 샘플링에서 빠지지 않게 먼저 집었다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
