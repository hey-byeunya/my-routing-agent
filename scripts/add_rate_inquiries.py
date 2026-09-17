"""운임 문의를 원천 데이터에 더한다. 다시 돌려도 같은 결과가 나온다.

왜 더하는가 — **평가셋 42건이 실제 결함을 한 번도 잡지 못했다.** #41 에서 품목
규칙을 넣었을 때 평가셋·하드케이스 수치는 그대로였는데, 화면에서는 "롯데택배로
방문택배 보낼 건데 2kg에 60cm 이하예요. 얼마예요?" 가 SPEC_SHIPPING 으로 새어
도구를 하나도 부르지 못했다. 데모가 증거로 싣고 있는 문의다.

원인은 평가셋의 빈자리였다. 원본 90건의 운임 문의는 "택배비 얼마예요?" 처럼
**서비스도 규격도 없는** 꼴이고, 서비스·규격이 이미 갖춰진 뒤 금액을 묻는 꼴 —
상담이 실제로 도구를 부르는 자리 — 이 없었다. 그 꼴을 카테고리마다 하나씩 더한다.

여섯 건 중 넷은 "서비스 + 규격 + 금액" 이고, 둘은 대조군이다.
  · SPEC_SHIPPING — 금액을 묻지만 배송조회 문의다
  · OTHER         — 금액을 묻지만 응대 범위 밖이다
금액이 나온다고 운임 카테고리로 끌어오지 않는지 함께 본다. 490005 를 더할 때와
같은 뜻이다("어디서"로 시작한다고 경로 문의가 아니다).

    python scripts/add_rate_inquiries.py
    python scripts/build_eval_set.py      # 그다음 평가셋을 다시 뽑는다
"""

from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# 490001~490005 다음 번호를 잇는다.
NEW = [
    ("490006", "택배예약", "운임", "2kg에 세 변 합 60cm인데 제일 싸게 보내려면 얼마예요?",
     "RESERVE_GENERAL", "RESERVE_GENERAL", "eval"),
    ("490007", "방문택배", "운임", "한진택배 방문택배로 5kg에 세 변 합 80cm면 얼마예요?",
     "VISIT_PICKUP", "VISIT_PICKUP", "eval"),
    ("490008", "편의점택배", "운임", "세븐일레븐 착한택배 5kg에 80cm 이하면 얼마예요?",
     "CVS_PICKUP", "CVS_PICKUP", "eval"),
    ("490009", "다량할인", "운임", "다량할인으로 10박스 보내는데 2kg에 60cm면 한 건당 얼마예요?",
     "BIZ_BULK", "BULK_DISCOUNT", "eval"),
    # 대조군 — 금액을 묻지만 운임 문의가 아니다.
    ("490010", "배송조회", "일반", "운송장번호로 조회하면 결제한 금액도 같이 나오나요?",
     "SPEC_SHIPPING", "SPEC_SHIPPING", "eval"),
    ("490011", "기타", "타채널", "오픈마켓에서 결제한 배송비는 얼마나 환불되나요?",
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
            "intent_full": f"{category}_{attribute}_질문", "sentiment": "m",
            "question": question, "answer": "", "product": "",
            "q_len": str(len(question)), "a_len": "0",
            "provenance": "합성(운임 문의 보강)",
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
