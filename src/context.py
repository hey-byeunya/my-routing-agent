"""근거 문서를 절 단위로 쪼개고, 카테고리별로 필요한 절만 조립한다.

장(`##`) 단위로만 쪼개면 "4. 운임 안내" 가 5개 카테고리 중 4개에 통째로 들어가
카테고리별 근거가 거의 겹친다. 이 문서는 4.1 방문 / 4.2 다량 / 4.3 편의점 /
4.4 소호 로 절이 나뉘어 있으므로 `###` 절 단위로 내려간다.

절을 고르면 그 절이 속한 장의 머리말이 자동으로 따라온다. 4장 머리말의
"[조회 필요]" 경고가 4.1 만 떼어 놓으면 사라지기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANUAL_PATH = BASE / "docs" / "policy_courierhub.md"

# 장 번호를 앞에 단 제목에서 번호만 뽑는다. "4.1 방문택배 (택배사별)" → "4.1"
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)?)[.)]?\s")


@dataclass(frozen=True)
class Unit:
    """조립 단위. 장 머리말이거나 절 하나다."""

    key: str  # 선택자에 쓰는 식별자. "4.1", "0", "이 매뉴얼을 쓰는 방법"
    chapter_key: str  # 이 단위가 속한 장의 key
    title: str  # 화면·리포트에 보일 제목
    body: str
    order: int  # 문서 내 등장 순서
    is_preamble: bool

    @property
    def chars(self) -> int:
        return len(self.body)


def _key_of(title: str) -> str:
    m = _NUMBERED.match(title)
    return m.group(1) if m else title


@lru_cache(maxsize=1)
def load_manual_text() -> str:
    return MANUAL_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def split_units() -> tuple[Unit, ...]:
    """매뉴얼을 장 머리말 + 절 단위로 쪼갠다. 문서 순서를 유지한다."""
    text = load_manual_text()
    units: list[Unit] = []
    order = 0

    # 첫 `## ` 앞은 표지·고지문이라 근거로 쓰지 않는다.
    chapters = re.split(r"(?m)^## ", text)[1:]
    for chunk in chapters:
        chapter_title, _, rest = chunk.partition("\n")
        chapter_title = chapter_title.strip()
        chapter_key = _key_of(chapter_title)

        parts = re.split(r"(?m)^### ", rest)
        preamble = parts[0].strip().rstrip("-").strip()
        units.append(
            Unit(
                key=chapter_key,
                chapter_key=chapter_key,
                title=chapter_title,
                body=preamble,
                order=order,
                is_preamble=True,
            )
        )
        order += 1

        for part in parts[1:]:
            section_title, _, section_body = part.partition("\n")
            section_title = section_title.strip()
            units.append(
                Unit(
                    key=_key_of(section_title),
                    chapter_key=chapter_key,
                    title=section_title,
                    body=section_body.strip().rstrip("-").strip(),
                    order=order,
                    is_preamble=False,
                )
            )
            order += 1

    return tuple(units)


# 카테고리 → 근거 절 매핑.
#   "X"    장 머리말만
#   "X.*"  장 머리말 + 그 장의 모든 절
#   "X.Y"  그 절 (+ 장 머리말 자동 포함)
ALWAYS: list[str] = ["이 매뉴얼을 쓰는 방법", "0.*", "10.3", "11"]

ROUTE_SECTIONS: dict[str, list[str]] = {
    "RESERVE_GENERAL": ["1.*", "2"],
    "VISIT_PICKUP": ["1.1", "2", "4.1"],
    "CVS_PICKUP": ["1.1", "2", "4.3", "5.3"],
    "BIZ_BULK": ["1.2", "4.2", "4.4", "8.*", "9"],
    "SPEC_SHIPPING": ["3.*", "5.1", "5.2", "6", "7"],
    "OTHER": ["10.1", "10.2"],
}


def _resolve(selector: str) -> list[Unit]:
    units = split_units()
    if selector.endswith(".*"):
        chapter = selector[:-2]
        return [u for u in units if u.chapter_key == chapter]

    picked = [u for u in units if u.key == selector]
    if not picked:
        raise KeyError(f"근거 문서에 '{selector}' 에 해당하는 절이 없다")

    # 절을 고르면 그 장의 머리말을 함께 끌고 온다 (4장 [조회 필요] 경고 등).
    out: list[Unit] = []
    for unit in picked:
        if not unit.is_preamble:
            out += [u for u in units if u.chapter_key == unit.chapter_key and u.is_preamble and u.body]
        out.append(unit)
    return out


def _select(selectors: list[str]) -> list[Unit]:
    seen: dict[int, Unit] = {}
    for selector in selectors:
        for unit in _resolve(selector):
            if unit.body:
                seen[unit.order] = unit
    return sorted(seen.values(), key=lambda u: u.order)


def common_units() -> list[Unit]:
    """모든 카테고리에 공통으로 들어가는 절."""
    return _select(ALWAYS)


def route_only_units(route: str) -> list[Unit]:
    """이 카테고리에만 들어가는 절. 공통 절은 뺀다."""
    common = {u.order for u in common_units()}
    return [u for u in _select(ROUTE_SECTIONS.get(route, [])) if u.order not in common]


def select_units(route: str) -> list[Unit]:
    """이 카테고리의 근거가 될 절들을 문서 순서로 돌려준다."""
    return _select(ALWAYS + ROUTE_SECTIONS.get(route, []))


def build_context(route: str) -> str:
    """카테고리에 필요한 절만 하나의 근거 문자열로 조립한다."""
    blocks = [f"## {u.title}\n\n{u.body}" for u in select_units(route)]
    return "\n\n---\n\n".join(blocks)


def count_tokens(text: str) -> int:
    """실측 토큰 수. tiktoken 이 없으면 -1 을 돌려준다 (추정치를 지어내지 않는다)."""
    try:
        import tiktoken
    except ImportError:
        return -1
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def mapping_table() -> list[dict]:
    """카테고리별 절 목록과 크기. REPORT 와 check_context 가 함께 쓴다."""
    from src.schemas import ROUTE_LIST

    rows = []
    for route in ROUTE_LIST:
        units = select_units(route)
        only = route_only_units(route)
        rows.append(
            {
                "route": route,
                "unit_keys": [u.key for u in units],
                "own_keys": [u.key for u in only],
                "own_titles": [u.title for u in only],
                "n_units": len(units),
                "n_own": len(only),
                "chars": sum(u.chars for u in units),
                "own_chars": sum(u.chars for u in only),
                "tokens": count_tokens(build_context(route)),
            }
        )
    return rows


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="카테고리별 근거 절 매핑을 출력한다")
    parser.add_argument("--show-mapping", action="store_true")
    parser.add_argument("--route", help="이 카테고리의 조립된 근거 전문을 출력")
    args = parser.parse_args()

    if args.route:
        print(build_context(args.route))
        return

    units = split_units()
    common = common_units()
    print(f"근거 문서: {MANUAL_PATH.relative_to(BASE)}")
    print(f"절 단위 {len(units)}개 (장 머리말 포함), 본문 {sum(u.chars for u in units):,}자")
    print(f"공통 절 {len(common)}개 {sum(u.chars for u in common):,}자 — {' · '.join(u.key for u in common)}\n")

    print(f"{'카테고리':<17}{'절':>3}{'토큰':>7}{'고유절':>7}{'고유자':>7}   카테고리 고유 근거")
    print("-" * 104)
    for row in mapping_table():
        print(
            f"{row['route']:<17}{row['n_units']:>3}{row['tokens']:>7,}"
            f"{row['n_own']:>7}{row['own_chars']:>7,}   {' · '.join(row['own_keys'])}"
        )
    print("\n고유 근거가 서로 겹치지 않아야 카테고리별로 다른 근거가 들어갔다고 말할 수 있다.")


if __name__ == "__main__":
    _main()
