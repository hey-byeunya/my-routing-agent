"""개선 시도별 종합 기록표를 store/metrics.jsonl 에서 뽑는다.

과제가 REPORT 에 요구한 "개선 시도별 기록표"다. 손으로 쓰지 않는다 — 손으로 쓰면
수치와 설명이 따로 놀고, 나중에 어느 회차에서 무엇이 좋아졌는지 복원할 수 없다.
기록 파일이 한 벌의 진실이고 이 스크립트는 그것을 표로 옮기기만 한다.

한 회차(=한 번의 변경)에 라우팅 평가와 답변 평가가 각각 붙을 수 있으므로
`round` 필드로 묶는다. 옛 레코드에는 `round` 가 없다(세대차) — 그때는 시간순으로
#0, #1 … 을 매기고 표 아래에 그 사실을 적는다.

`stage: "note"` 줄은 바로 앞 회차의 메모로 붙인다. 무효 선언이 그런 줄이다.

    python scripts/report_table.py              # 마크다운 (REPORT.md 에 붙인다)
    python scripts/report_table.py --format html
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.record import read_all  # noqa: E402

COLUMNS = ["회차", "변경 대상", "핵심 변경 이유 (Why)", "핵심 변경 내용 (What)",
           "라우팅 (정확도 / macro F1)", "답변 (도구 / 적절성)", "성과 및 오답 메모"]


def _rounds(rows: list[dict]) -> list[dict]:
    """eval 레코드를 회차로 묶는다. note 줄은 직전 회차에 메모로 붙인다."""
    order: list[str] = []
    by_key: dict[str, dict] = {}
    auto = 0

    for row in rows:
        stage = row.get("stage", "")
        if stage == "note":
            if order:
                by_key[order[-1]]["memos"].append(row.get("detail", ""))
            continue
        if not stage.startswith("eval_"):
            continue

        key = row.get("round")
        if not key:
            key = f"#{auto}"
            auto += 1
            legacy = True
        else:
            legacy = False
        if key not in by_key:
            order.append(key)
            by_key[key] = {"round": key, "legacy": legacy, "memos": [], "routing": None, "answer": None,
                           "target": "", "why": "", "what": ""}
        entry = by_key[key]
        for field in ("target", "why", "what"):
            if row.get(field):
                entry[field] = row[field]
        if row.get("memo"):
            entry["memos"].append(row["memo"])
        if not entry["what"] and row.get("detail"):
            entry["what"] = row["detail"]
        entry["routing" if stage == "eval_routing" else "answer"] = row

    return [by_key[k] for k in order]


def _fmt(value, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _cells(entry: dict) -> list[str]:
    routing, answer = entry["routing"], entry["answer"]
    if routing:
        left = f"{_fmt(routing.get('accuracy'))} / {_fmt(routing.get('macro_f1'))} ({routing.get('count')}건)"
    else:
        left = "—"
    if answer:
        right = f"{_fmt(answer.get('tool_score'))} / {_fmt(answer.get('answer_score'))} ({answer.get('count')}턴)"
    else:
        right = "—"
    backend = (routing or answer or {}).get("backend", "")
    model = (routing or answer or {}).get("model") or ""
    label = entry["round"] + (f"<br>`{backend}{'/' + model if model else ''}`" if backend else "")
    return [
        label,
        f"`{entry['target']}`" if entry["target"] else "—",
        entry["why"] or "—",
        entry["what"] or "—",
        left,
        right,
        "<br>".join(m for m in entry["memos"] if m) or "—",
    ]


def as_markdown(entries: list[dict]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "|".join([" --- "] * len(COLUMNS)) + "|"]
    for entry in entries:
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in _cells(entry)) + " |")
    if any(e["legacy"] for e in entries):
        lines.append("")
        lines.append(
            "> 세대차: `round` 필드가 없던 시기의 레코드는 시간순으로 #0, #1 … 을 매겼다. "
            "그 회차의 '변경 내용'은 실행 당시 `--note` 문구를 그대로 옮긴 것이고, "
            "'변경 이유'는 기록에 없어 비어 있다. 옛 줄은 고치지 않는다."
        )
    return "\n".join(lines)


def as_html(entries: list[dict]) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in COLUMNS)
    body = ""
    for entry in entries:
        cells = "".join(f"<td>{c}</td>" for c in _cells(entry))
        body += f"<tr>{cells}</tr>"
    return (
        "<table border='1' cellspacing='0' cellpadding='6' style=\"border-collapse:collapse;font-size:14px\">"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="개선 시도별 종합 기록표")
    parser.add_argument("--format", choices=["md", "html"], default="md")
    parser.add_argument("--out", default=None, help="파일로 저장 (비우면 화면 출력)")
    args = parser.parse_args()

    entries = _rounds(read_all())
    if not entries:
        print("평가 기록이 없다. src.evaluate 를 먼저 돌린다.")
        return 1

    text = as_markdown(entries) if args.format == "md" else as_html(entries)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"{len(entries)}회차 → {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
