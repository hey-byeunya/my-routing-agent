"""데모 화면 캡처. REPORT 에 넣을 증거 이미지를 손이 아니라 스크립트가 만든다.

Streamlit 은 웹소켓으로 화면을 채우기 때문에 `chrome --screenshot` 은 로딩
껍데기만 찍는다. 그래서 헤드리스 크롬을 CDP 로 붙잡고, **답변이 실제로 화면에
나타난 것을 확인한 뒤** 찍는다. 확인 없이 기다리는 시간만 늘리면 캡처가 조용히
빈 화면이 되고, 리포트에 증거가 아니라 그림이 실린다.

    python scripts/capture_demo.py                 # 기본 4장
    python scripts/capture_demo.py --port 8601

앱이 떠 있어야 한다 (`./run.sh` 또는 `streamlit run app.py --server.port 8601`).
추가 설치는 없다 — 크롬은 시스템 것, 통신은 websockets(streamlit 의존성)를 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "docs" / "img"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]

SHOTS = [
    ("demo_rate_lookup", "롯데택배로 방문택배 보낼 건데 2kg에 60cm 이하예요. 얼마예요?",
     "운임 조회가 필요한 문의 — 도구를 부르고 그 금액으로 답한다"),
    ("demo_no_lookup", "박스 크기는 어떻게 재나요? 제일 긴 쪽 기준인가요?",
     "조회가 필요 없는 문의 — 근거 문서만으로 답한다"),
    ("demo_out_of_scope", "혹시 거기 채용 공고 있나요?",
     "응대 범위 밖 — 담당자 이관이 아니라 채널 안내로 끝낸다"),
    ("demo_ask", "택배비가 얼마인가요?",
     "정보가 부족한 문의 — 어떤 서비스인지부터 되묻는다"),
]


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if path and Path(path).exists():
            return path
    print("크롬을 찾지 못했다. 캡처는 건너뛴다.", file=sys.stderr)
    raise SystemExit(2)


def app_is_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


async def capture(ws_url: str, page_url: str, out: Path, timeout_s: int = 90) -> None:
    async with websockets.connect(ws_url, max_size=100 * 1024 * 1024) as ws:
        msg_id = 0

        async def send(method: str, params: dict | None = None) -> dict:
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            while True:
                data = json.loads(await ws.recv())
                if data.get("id") == msg_id:
                    return data.get("result", {})

        await send("Page.enable")
        await send("Page.navigate", {"url": page_url})

        # 답변 블록이 나타날 때까지 기다린다. 시간이 아니라 화면을 본다.
        deadline = time.monotonic() + timeout_s
        ready = False
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            result = await send("Runtime.evaluate", {
                "expression": "document.body.innerText.includes('이 답이 나온 경로')",
                "returnByValue": True,
            })
            if result.get("result", {}).get("value") is True:
                ready = True
                break
        if not ready:
            raise RuntimeError(f"답변이 화면에 나타나지 않았다 (>{timeout_s}초): {page_url}")

        # 사이드바가 접힘 상태로 렌더되면 왼쪽이 잘린 채 찍힌다. 펼쳐 놓고 찍는다 —
        # 어느 백엔드로 어떤 τ 에서 돌았는지가 캡처에 남아야 증거가 된다.
        await send("Runtime.evaluate", {"expression": """
            (() => {
              const bar = document.querySelector('[data-testid="stSidebar"]');
              if (bar) {
                bar.style.transform = 'none';
                bar.style.visibility = 'visible';
                bar.setAttribute('aria-expanded', 'true');
              }
              const collapsed = document.querySelector('[data-testid="stSidebarCollapsedControl"]');
              if (collapsed) collapsed.style.display = 'none';
            })()
        """})
        await asyncio.sleep(1.5)  # 펼침 애니메이션이 끝나게

        # 페이지가 실제로 차지한 크기를 재서 그 영역만 자른다. captureBeyondViewport
        # 만 켜면 가로 위치가 밀려 사이드바 왼쪽이 잘린 채로 찍힌다.
        metrics = await send("Page.getLayoutMetrics")
        size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
        clip = {
            "x": 0, "y": 0,
            "width": max(int(size.get("width", 1600)), 1200),
            "height": min(int(size.get("height", 1400)), 4000),
            "scale": 1,
        }
        shot = await send("Page.captureScreenshot",
                          {"format": "png", "captureBeyondViewport": True, "clip": clip})
        out.write_bytes(base64.b64decode(shot["data"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="데모 화면 캡처")
    parser.add_argument("--port", type=int, default=8601, help="Streamlit 포트")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    if not app_is_up(args.port):
        print(f"localhost:{args.port} 에 앱이 없다. './run.sh' 로 먼저 띄운다.", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome()
    profile = tempfile.mkdtemp(prefix="capture-demo-")

    proc = subprocess.Popen(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--window-size=1600,1400", "--remote-debugging-port=9222",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = ""
        for _ in range(40):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen("http://localhost:9222/json", timeout=2) as r:
                    tabs = json.load(r)
                pages = [t for t in tabs if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                continue
        if not ws_url:
            print("크롬 디버깅 포트에 붙지 못했다.", file=sys.stderr)
            return 3

        failed = 0
        for name, question, caption in SHOTS:
            url = (f"http://localhost:{args.port}/?q={urllib.parse.quote(question)}&run=1")
            path = out_dir / f"{name}.png"
            try:
                asyncio.run(capture(ws_url, url, path))
                print(f"  ok   {path.relative_to(BASE)}  — {caption}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {exc}", file=sys.stderr)
        return 1 if failed else 0
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
