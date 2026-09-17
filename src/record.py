"""실행 기록. store/metrics.jsonl 에 1줄 1레코드로 덧붙인다.

지우지 않고 쌓는다. 개발 과정 전체가 시간순으로 남아야 "개선 시도별 기록표"를
나중에 뽑을 수 있다. 형식이 바뀌면 옛 줄을 고치지 말고 REPORT 에 세대차를 적는다.

--dry-run 은 기록하지 않는다. 구경용 실행이 기록에 섞이면 수치를 믿을 수 없다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
METRICS_PATH = BASE / "store" / "metrics.jsonl"


def append(stage: str, count: int | float, detail: str = "", **extra: Any) -> dict:
    """한 줄 덧붙인다. 끄고 싶으면 환경변수 AGENT_NO_METRICS=1."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "count": count,
        "detail": detail,
        **extra,
    }
    if os.environ.get("AGENT_NO_METRICS") == "1":
        return record
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_all() -> list[dict]:
    if not METRICS_PATH.exists():
        return []
    rows = []
    for line in METRICS_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 옛 형식이 섞여 있어도 읽기가 멈추지 않게
    return rows


def _main() -> None:
    rows = read_all()
    if not rows:
        print("기록 없음")
        return
    print(f"{len(rows)}줄  {rows[0]['ts']} → {rows[-1]['ts']}\n")
    print(f"{'ts':<21}{'stage':<18}{'backend':<10}{'count':>8}  detail")
    for row in rows[-30:]:
        print(
            f"{row['ts']:<21}{row.get('stage', ''):<18}{row.get('backend', '-'):<10}"
            f"{row.get('count', ''):>8}  {str(row.get('detail', ''))[:60]}"
        )


if __name__ == "__main__":
    _main()
