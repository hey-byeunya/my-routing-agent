"""목데이터 조회. 순수 파이썬 함수만 둔다 — LangChain 은 tools.py 가 얹는다.

두 가지 원칙을 지킨다.
  1. 없는 것은 없다고 답한다. 잘못된 식별자에는 분명한 오류를 돌려준다.
  2. 매뉴얼에 값이 없는 항목(소호사업자 운임)은 지어내지 않고 "조회 필요"로 돌려준다.
     정답셋 C-005 가 이 지점의 날조를 잡아내는 문항이다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# 저장소 루트. 패키지가 src/routing_agent/ 에 있으므로 두 단계 위가 아니라 세 단계 위다.
BASE = Path(__file__).resolve().parents[2]
DATA_PATH = BASE / "data" / "mockdata_courierhub.json"


@lru_cache(maxsize=1)
def _db() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _pick_tier(tiers: list[dict], weight_kg: float, size_cm: float) -> dict | None:
    """무게·세 변 합이 둘 다 들어가는 첫 구간. 구간표는 오름차순으로 적혀 있다."""
    for tier in tiers:
        if weight_kg <= tier["max_weight_kg"] and size_cm <= tier["max_size_cm"]:
            return tier
    return None


def _surcharge(carrier: dict, region: str | None) -> dict:
    if not region:
        return {}
    text = region.strip()
    if "제주" in text:
        fee = carrier.get("jeju_surcharge")
        return {"region": "제주", "surcharge": fee} if fee is not None else {"region": "제주", "surcharge": "조회 필요"}
    if any(word in text for word in ("도서", "섬", "울릉", "흑산")):
        if carrier.get("island_available") is False:
            return {"region": "도서", "available": False}
        fee = carrier.get("island_surcharge")
        return {"region": "도서", "surcharge": fee} if fee is not None else {"region": "도서", "surcharge": "조회 필요"}
    return {"region": text, "surcharge": 0}


def carrier_names(group: str) -> list[str]:
    """이 그룹에 등록된 이름. 도구 설명과 화면이 여기서 이름을 얻는다."""
    return sorted(_db()["carriers"].get(group, {}))


def _rate(group: str, carrier_name: str, weight_kg: float, size_cm: float, region: str | None) -> dict:
    carriers = _db()["carriers"][group]
    carrier = carriers.get(carrier_name)
    if carrier is None:
        # 고객은 "CU", "롯데" 처럼 줄여 말한다. 정확한 상품명만 받으면 조회가 통째로
        # 실패하고, 상담원은 값을 손에 쥐고도 "등록된 이름이 아니다"만 말하게 된다.
        # 부분 일치 후보를 **요금과 함께** 돌려준다. 어느 상품인지 고르는 일은
        # 답변 단계가 하되, 고를 근거(금액)를 같이 준다.
        key = carrier_name.replace(" ", "")
        candidates = [n for n in carriers if key and key in n.replace(" ", "")]
        if not candidates:
            return {
                "error": f"'{carrier_name}' 은(는) 등록된 이름이 아니다",
                "available": sorted(carriers),
            }
        if len(candidates) > 1:
            return {
                "query": carrier_name,
                "ambiguous": True,
                "note": "이름이 여러 상품에 걸린다. 상품별 운임을 함께 안내하고 어느 것인지 확인한다",
                "options": [
                    _rate(group, name, weight_kg, size_cm, region) for name in sorted(candidates)
                ],
            }
        carrier_name = candidates[0]
        carrier = carriers[carrier_name]
    tier = _pick_tier(carrier["tiers"], weight_kg, size_cm)
    if tier is None:
        return {
            "carrier": carrier_name,
            "error": "이 무게·크기를 담는 구간이 매뉴얼에 없다. 예약 화면에서 확인 필요",
            "tiers": carrier["tiers"],
            "note": carrier.get("note"),
        }
    result: dict[str, Any] = {
        "carrier": carrier_name,
        "matched_tier": f"{tier['max_weight_kg']}kg/{tier['max_size_cm']}cm 이하",
        "fee": tier["fee"],
        "currency": "KRW",
        "is_snapshot": True,
        # 이 문구는 모델이 고객 답변에 거의 그대로 옮긴다. "스냅샷"처럼 내부에서만
        # 쓰는 말을 넣으면 고객이 못 알아듣는다. 뜻은 같게, 말은 고객용으로.
        "note": "기본 운임 기준 금액이다. 확정 금액은 예약 화면에서 확인해야 한다",
    }
    if carrier.get("note"):
        result["carrier_note"] = carrier["note"]
    surcharge = _surcharge(carrier, region)
    if surcharge:
        result["region_surcharge"] = surcharge
    return result


# ---------------------------------------------------------------- 운임


def get_visit_rate(carrier: str, weight_kg: float, size_cm: float, region: str | None = None) -> dict:
    return _rate("visit_pickup", carrier, weight_kg, size_cm, region)


def get_cvs_rate(brand: str, weight_kg: float, size_cm: float, region: str | None = None) -> dict:
    return _rate("cvs_pickup", brand, weight_kg, size_cm, region)


def get_bulk_rate(box_count: int, weight_kg: float, size_cm: float, region: str | None = None) -> dict:
    """다량할인은 택배사가 아니라 발송 수량으로 상품이 갈린다."""
    products = _db()["carriers"]["bulk_discount"]
    matches = []
    for name, product in products.items():
        min_boxes = product.get("min_boxes")
        # min_boxes 가 null 인 상품은 최소 수량이 원본에 공개되지 않은 것이다.
        # 조건을 만족한다고 단정하지 않고, 조회 필요로 표시해 함께 보여 준다.
        if min_boxes is not None and box_count < min_boxes:
            continue
        tier = _pick_tier(product["tiers"], weight_kg, size_cm)
        if tier is None:
            continue
        row: dict[str, Any] = {
            "product": name,
            "min_boxes": min_boxes if min_boxes is not None else "미공개 — 조회 필요",
            "condition_confirmed": min_boxes is not None,
            "matched_tier": f"{tier['max_weight_kg']}kg/{tier['max_size_cm']}cm 이하",
            "fee": tier["fee"],
        }
        if product.get("note"):
            row["note"] = product["note"]
        surcharge = _surcharge(product, region)
        if surcharge:
            row["region_surcharge"] = surcharge
        matches.append(row)
    if not matches:
        return {
            "box_count": box_count,
            "error": "이 수량·규격에 맞는 다량할인 상품이 매뉴얼에 없다. 예약 화면에서 확인 필요",
            "products": sorted(products),
        }
    matches.sort(key=lambda r: r["fee"])
    # 최저가는 최소 수량 조건이 확인된 상품 중에서만 뽑는다.
    confirmed = [r for r in matches if r["condition_confirmed"]]
    return {
        "box_count": box_count,
        "cheapest_fee": confirmed[0]["fee"] if confirmed else None,
        "cheapest_product": confirmed[0]["product"] if confirmed else None,
        "options": matches,
        "is_snapshot": True,
        # 이 문구는 모델이 고객 답변에 거의 그대로 옮긴다. "스냅샷"처럼 내부에서만
        # 쓰는 말을 넣으면 고객이 못 알아듣는다. 뜻은 같게, 말은 고객용으로.
        "note": "기본 운임 기준 금액이다. 확정 금액은 예약 화면에서 확인해야 한다",
    }


# 아래 둘은 도구로 노출하지 않는다. 소호 운임과 반입 제한은 건별로 달라지는 값이
# 아니라 매뉴얼(§4.4·§7)이 답할 몫이기 때문이다. 그래도 남겨 두는 것은
# check_mockdb 가 이 값들로 **목데이터와 매뉴얼의 주장이 어긋나지 않는지**를
# 확인하기 때문이다 (현금 접수 불가, 소호 운임 미공개 표기).
def get_biz_rate() -> dict:
    """소호사업자 운임은 매뉴얼에 값이 없다. 지어내지 않고 조회 필요로 돌려준다."""
    return {
        "lookup_required": True,
        "reason": "전월 발송량에 따라 다음 달 구간이 달라지며, 구체적 구간·금액은 매뉴얼에 적혀 있지 않다",
        "where": "소호사업자택배 신청·이용 화면",
        "do_not": "구간·금액을 임의로 안내하지 말 것",
    }


# ---------------------------------------------------------------- 상태 조회


def get_reservation_status(reservation_id: str) -> dict:
    record = _db()["reservations"].get(reservation_id.strip().upper())
    if record is None:
        return {"error": f"예약번호 '{reservation_id}' 를 찾을 수 없다", "hint": "예약번호와 운송장번호는 다른 번호다"}
    return {"reservation_id": reservation_id.strip().upper(), **record}


def get_tracking_status(tracking_number: str) -> dict:
    record = _db()["tracking"].get(str(tracking_number).strip())
    if record is None:
        return {
            "error": f"운송장번호 '{tracking_number}' 로 조회되는 건이 없다",
            "hint": "수거·전산 등록 전에는 조회되지 않을 수 있다. 예약번호를 운송장번호로 착각한 경우도 흔하다",
        }
    return {"tracking_number": str(tracking_number).strip(), **record}


# ---------------------------------------------------------------- 규칙 조회


def get_restricted_items(item: str | None = None) -> dict:
    rules = _db()["restricted_items"]
    if not item:
        return rules
    text = item.strip()
    for name in rules["prohibited"]:
        if name in text or text in name:
            # 접수 자체가 불가한 그룹이다. 택배사 기준을 확인하라는 안내를 붙이면 안 된다.
            return {"item": text, "verdict": "접수 불가", "matched": name, "note": "택배사와 무관하게 접수할 수 없다"}
    for name in rules["restricted_by_carrier"]:
        if name in text or text in name:
            return {
                "item": text,
                "verdict": "택배사 기준에 따라 제한",
                "matched": name,
                "note": "택배사마다 기준이 다르다. 단정하지 말고 선택한 택배사 기준을 확인하도록 안내",
            }
    return {"item": text, "verdict": "목록에 없음", "note": "목록에 없다고 무조건 가능하다는 뜻은 아니다. 택배사 기준 확인 필요"}


def escalate_to_agent(reason: str, summary: str, sentiment: str = "neutral") -> dict:
    """2차 상담 이관. 사유와 지금까지 확인한 내용을 반드시 남긴다 (정책 §10.2)."""
    return {
        "escalated": True,
        "reason": reason,
        "summary": summary,
        "sentiment": sentiment,
        "next": "담당자에게 연결됨. 고객에게는 연결 사실과 사과를 전달한다",
    }
