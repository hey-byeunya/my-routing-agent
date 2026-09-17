"""원본 데이터셋(8구분)을 이 프로젝트의 5+OTHER 로 옮긴다. 다시 돌려도 같은 결과가 나온다.

하는 일 세 가지.
  1. routing_answers.csv 에 route_v2 컬럼을 붙인다. 원본 route 는 남긴다 —
     REPORT 에서 "왜 병합했는가"를 보일 때 필요하다.
  2. hard_cases.csv 에 route_expected_v2 를 붙인다.
  3. answer_goldenset.json 의 대화별 route 를 병합하고 route_original 을 남긴다.

eval_set.csv(카테고리 균등 평가셋)는 build_eval_set.py 가 따로 만든다.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


from routing_agent.schemas import ROUTE_LIST, merge_route

DATA = Path(__file__).resolve().parent.parent / "data"


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    # 원본 파일에 BOM 이 있다. utf-8-sig 로 읽어야 첫 컬럼명이 깨지지 않는다.
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def add_column(path: Path, src_col: str, new_col: str) -> Counter:
    fields, rows = _read_csv(path)
    if new_col not in fields:
        fields = fields + [new_col]
    counts: Counter = Counter()
    for row in rows:
        merged = merge_route(row[src_col])
        row[new_col] = merged
        counts[merged] += 1
    _write_csv(path, fields, rows)
    return counts


def reserve_other_fewshot(path: Path, n: int = 2) -> list[str]:
    """OTHER 몇 건을 예시용으로 뺀다.

    원본은 OTHER 를 전부 split=outscope 로 두어 예시가 한 건도 없었다. 그러면
    분류 프롬프트에 범위 밖 예시가 없어, 범위 밖 문의를 업무 카테고리로
    끌어오는 실패가 늘어난다. qa_id 순으로 앞의 n건을 예시용으로 돌린다.
    """
    fields, rows = _read_csv(path)
    others = sorted((r for r in rows if r["route_v2"] == "OTHER"), key=lambda r: r["qa_id"])
    moved = []
    for row in others[:n]:
        if row["split"] != "fewshot":
            row["split"] = "fewshot"
        moved.append(row["qa_id"])
    _write_csv(path, fields, rows)
    return moved


def merge_goldenset(path: Path) -> Counter:
    data = json.loads(path.read_text(encoding="utf-8"))
    counts: Counter = Counter()
    for conv in data["conversations"]:
        original = conv.get("route_original") or conv["route"]
        conv["route_original"] = original
        conv["route"] = merge_route(original)
        counts[conv["route"]] += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


def _show(title: str, counts: Counter, total_label: str) -> None:
    print(f"\n{title}")
    for route in ROUTE_LIST:
        if counts.get(route):
            print(f"  {route:<17} {counts[route]:>3}")
    print(f"  {'합계':<16} {sum(counts.values()):>3} {total_label}")


def main() -> int:
    _show("routing_answers.csv → route_v2", add_column(DATA / "routing_answers.csv", "route", "route_v2"), "건")
    moved = reserve_other_fewshot(DATA / "routing_answers.csv")
    print(f"\nOTHER 예시용으로 이동: {', '.join(moved)} (분류 프롬프트에 범위 밖 예시를 넣기 위해)")
    _show(
        "hard_cases.csv → route_expected_v2",
        add_column(DATA / "hard_cases.csv", "route_expected", "route_expected_v2"),
        "건",
    )
    _show("answer_goldenset.json → route", merge_goldenset(DATA / "answer_goldenset.json"), "대화")
    print("\nOK prepare_data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
