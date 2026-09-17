"""근거 조립 픽스처 검증. LLM 도 API 키도 쓰지 않는다.

루브릭이 묻는 것을 기계적으로 확인한다 —
"카테고리에 따라 서로 다른 근거만 프롬프트에 들어가도록 구현했는가".
실패하면 종료 코드 1. 이게 통과하기 전의 성능 수치는 믿지 않는다.
"""

from __future__ import annotations


from routing_agent.context import (
    ROUTE_SECTIONS,
    build_context,
    common_units,
    mapping_table,
    route_only_units,
    select_units,
    split_units,
)
from routing_agent.schemas import ROUTE_LIST

# 운임표는 카테고리를 가르는 핵심 근거다. 한 카테고리에만 들어가야 한다.
RATE_SECTIONS = {
    "4.1": "VISIT_PICKUP",
    "4.2": "BIZ_BULK",
    "4.3": "CVS_PICKUP",
    "4.4": "BIZ_BULK",
}

# 상담 내용이 아니라 문서 메타데이터라 일부러 어느 카테고리에도 넣지 않는다.
NOT_GUIDANCE = {"개정 이력"}

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if ok:
        print(f"  ok   {label}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
        failures.append(label)


def main() -> int:
    units = split_units()
    print(f"units: 절 단위 {len(units)}개, 본문 {sum(u.chars for u in units):,}자\n")

    print("[1] 모든 선택자가 실제 절로 풀린다")
    for route in ROUTE_LIST:
        try:
            picked = select_units(route)
            check(route, len(picked) > 0, f"{len(picked)}개 절")
        except KeyError as exc:
            check(route, False, str(exc))

    print("\n[2] 카테고리마다 고유 근거가 있다 (공통 절만으로 돌지 않는다)")
    for route in ROUTE_LIST:
        own = route_only_units(route)
        check(route, len(own) > 0, f"고유 {len(own)}개 / {sum(u.chars for u in own):,}자")

    print("\n[3] 운임표는 한 카테고리에만 들어간다")
    for key, owner in RATE_SECTIONS.items():
        holders = [r for r in ROUTE_LIST if key in {u.key for u in select_units(r)}]
        check(f"{key} → {owner}", holders == [owner], f"실제: {holders or '없음'}")

    print("\n[4] 범위 밖 카테고리에는 운임표가 들어가지 않는다")
    other_keys = {u.key for u in select_units("OTHER")}
    leaked = sorted(other_keys & set(RATE_SECTIONS))
    check("OTHER", not leaked, f"샌 절: {leaked}" if leaked else "운임표 없음")

    print("\n[5] 서로 다른 카테고리는 서로 다른 근거를 받는다")
    contexts = {r: build_context(r) for r in ROUTE_LIST}
    for i, a in enumerate(ROUTE_LIST):
        for b in ROUTE_LIST[i + 1 :]:
            check(f"{a} ≠ {b}", contexts[a] != contexts[b])

    print("\n[6] 공통 절에 상담 기본 원칙과 금지 사항이 들어 있다")
    common_keys = {u.key for u in common_units()}
    for required in ("0", "10.3", "11"):
        check(f"공통에 {required}", required in common_keys)

    print("\n[7] 어느 카테고리도 문서 전체를 통째로 받지 않는다")
    whole = sum(u.chars for u in units)
    for row in mapping_table():
        ratio = row["chars"] / whole
        check(row["route"], ratio < 0.95, f"{row['chars']:,}자 / 전체 {whole:,}자 = {ratio:.0%}")

    print("\n[8] 쓰이지 않는 절이 있는지 (있으면 카테고리 설계를 다시 본다)")
    used = {u.key for r in ROUTE_LIST for u in select_units(r)}
    orphans = sorted({u.key for u in units if u.body} - used - NOT_GUIDANCE)
    check("상담 지침 절이 전부 쓰인다", not orphans, f"미사용: {orphans}" if orphans else f"제외: {sorted(NOT_GUIDANCE)}")

    print()
    if failures:
        print(f"FAIL check_context — {checks}개 중 {len(failures)}개 실패")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK check_context — {checks}개 전부 통과")
    print(f"선택자 정의: {ROUTE_SECTIONS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
