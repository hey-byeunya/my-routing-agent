"""화면 경로 문의를 원천 데이터에 더한다. 다시 돌려도 같은 결과가 나온다.

왜 더하는가 — 원본 90건은 "무엇이 얼마인가"에 쏠려 있고 **"어디서 하는가"가 없다.**
실제 상담에서는 예약 경로·예약 내역 조회·배송조회 경로 문의가 큰 비중을 차지한다.
매뉴얼에 §2(예약 경로)와 §5.4(예약현황·3개월 보관)를 넣은 뒤, 그 근거로 답할 수
있는 문의를 원천에 더한다.

OTHER 를 한 건 더하는 이유는 따로 있다 — 카테고리당 7건을 뽑으려면 가장 적은
카테고리가 7건이어야 하는데 OTHER 가 6건이었다. 더한 문의("세금계산서는 어디서
발급받나요?")는 **"어디서"로 시작하지만 응대 범위 밖**이라, 경로 문의라고 무조건
업무 카테고리로 끌어오지 않는지 보는 문항이기도 하다.

    python scripts/add_navigation_inquiries.py
    python scripts/build_eval_set.py      # 그다음 평가셋을 다시 뽑는다
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# qa_id 는 원본(400001~)과 겹치지 않게 490001 부터 쓴다.
NEW = [
    ("490001", "택배예약", "일반", "예약 어디서 해요?",
     "RESERVE_GENERAL", "RESERVE_GENERAL", "eval"),
    ("490002", "택배예약", "일반", "전화로 예약되나요?",
     "RESERVE_GENERAL", "RESERVE_GENERAL", "eval"),
    ("490003", "배송조회", "일반", "예약한 내역을 확인할 수가 없어요.",
     "SPEC_SHIPPING", "SPEC_SHIPPING", "eval"),
    ("490004", "배송조회", "일반", "배송 조회는 어디서 해요?",
     "SPEC_SHIPPING", "SPEC_SHIPPING", "eval"),
    ("490005", "기타", "행정", "세금계산서는 어디서 발급받나요?",
     "OTHER", "OTHER", "outscope"),
]


def _rows(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    q_path, r_path = DATA / "customer_inquiries.csv", DATA / "routing_answers.csv"
    q_fields, q_rows = _rows(q_path)
    r_fields, r_rows = _rows(r_path)
    have = {row["qa_id"] for row in q_rows}

    added = 0
    for qa_id, category, attribute, question, route_v2, route, split in NEW:
        if qa_id in have:
            continue
        q_row = {f: "" for f in q_fields}
        q_row.update({
            "qa_id": qa_id, "category": category, "attribute": attribute,
            "intent_full": f"{category}_경로_질문", "sentiment": "m",
            "question": question, "answer": "", "product": "",
            "q_len": str(len(question)), "a_len": "0",
            # 원본과 같은 표기를 쓰되, 이 프로젝트에서 저작한 것임을 남긴다.
            "provenance": "합성(경로 문의 보강)",
        })
        q_rows.append(q_row)

        r_row = {f: "" for f in r_fields}
        r_row.update({"qa_id": qa_id, "route": route, "split": split, "route_v2": route_v2})
        r_rows.append(r_row)
        added += 1

    _write(q_path, q_fields, q_rows)
    _write(r_path, r_fields, r_rows)
    print(f"문의 {added}건 추가 (이미 있으면 건너뛴다)")
    if added:
        print("  다음: python scripts/build_eval_set.py 로 평가셋을 다시 뽑는다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
