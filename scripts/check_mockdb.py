"""조회 도구 픽스처 검증. LLM 을 부르지 않는다.

도구가 무엇을 돌려주는지가 답변의 상한이다 — 도구가 "등록된 이름이 아니다"만
돌려주면 답변도 그 말밖에 못 한다. 그래서 이름 해석·구간 선택·지역 할증처럼
답변을 좌우하는 길목을 여기서 고정한다.

    python scripts/check_mockdb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import mockdb  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(label)


def main() -> int:
    print("조회 도구 픽스처 검증\n")

    # ── 이름 해석 ────────────────────────────────────────────────
    exact = mockdb.get_cvs_rate("CU편의점택배", 2, 80, "제주")
    check("정확한 상품명은 그대로 조회된다", exact.get("fee") == 3490, str(exact)[:120])
    check("지역 할증이 함께 온다", exact.get("region_surcharge", {}).get("surcharge") == 3000)

    partial = mockdb.get_cvs_rate("CU", 2, 80, "제주")
    check("줄여 말한 이름은 후보로 풀린다", partial.get("ambiguous") is True, str(partial)[:120])
    fees = [o.get("fee") for o in partial.get("options", [])]
    check("후보마다 운임이 함께 온다 (고를 근거)", all(isinstance(f, int) for f in fees) and len(fees) >= 2)

    one = mockdb.get_visit_rate("롯데", 2, 60)
    check("후보가 하나면 그 상품으로 조회된다", one.get("carrier") == "롯데택배" and one.get("fee") == 3800)

    unknown = mockdb.get_cvs_rate("없는브랜드", 2, 80)
    check("모르는 이름은 후보 목록과 함께 오류", "error" in unknown and unknown.get("available"))

    # ── 구간 선택 ────────────────────────────────────────────────
    heavy = mockdb.get_visit_rate("롯데택배", 30, 200)
    check("매뉴얼에 없는 구간은 단정하지 않는다", "error" in heavy, str(heavy)[:120])

    bulk = mockdb.get_bulk_rate(12, 2, 80)
    check("다량할인은 박스 수량으로 갈린다", bulk.get("cheapest_fee") and bulk.get("box_count") == 12)
    check("최소 수량이 미공개인 상품은 조회 필요로 표시한다",
          any(not o.get("condition_confirmed") or o.get("min_boxes") for o in bulk.get("options", [])))

    biz = mockdb.get_biz_rate()
    check("소호 운임은 금액을 지어내지 않는다", biz.get("lookup_required") is True, str(biz)[:120])

    # ── 반입 제한 ────────────────────────────────────────────────
    cash = mockdb.get_restricted_items("현금")
    check("현금은 접수 불가로 판정된다", "불가" in str(cash.get("verdict", "")), str(cash)[:120])

    # ── 데이터 무결성 (택배사·브랜드를 새로 넣었을 때 여기서 걸린다) ──────
    db = mockdb._db()
    for group, carriers in db["carriers"].items():
        for name, row in carriers.items():
            tiers = row.get("tiers")
            check(f"[{group}] {name} 에 운임 구간이 있다", bool(tiers))
            if not tiers:
                continue
            bad = [t for t in tiers
                   if not all(k in t for k in ("max_weight_kg", "max_size_cm", "fee"))]
            check(f"[{group}] {name} 구간에 무게·크기·요금이 다 있다", not bad, str(bad)[:120])
            probe = mockdb._rate(group, name, 1, 60, "제주") if group != "bulk_discount" else {}
            if group != "bulk_discount":
                check(f"[{group}] {name} 을 이름으로 조회할 수 있다",
                      "error" not in probe or "구간" in str(probe.get("error", "")), str(probe)[:120])

    print()
    if FAILED:
        print(f"FAIL check_mockdb — {len(FAILED)}건 실패: {', '.join(FAILED)}")
        return 1
    print("OK check_mockdb — 전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
