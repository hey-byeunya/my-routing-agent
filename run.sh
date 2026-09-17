#!/usr/bin/env bash
# 로컬에서 바로 해보기. 인자 없이 실행하면 데모 화면을 띄운다.
#
#   ./run.sh              데모 (Streamlit)
#   ./run.sh check        LLM 없이 도는 점검 세 개
#   ./run.sh ask "문의"    한 건만 터미널에서
#   ./run.sh dry "문의"    LLM 없이 앞단만 (키 없어도 된다)
#   ./run.sh eval         두 지표 평가 (LLM 호출 약 100회)
#   ./run.sh table        회차별 기록표
#
# 처음이면 .venv 를 만들고 의존성을 깔고 .env 를 준비하는 것까지 알아서 한다.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python

if [ ! -x "$PY" ]; then
  echo "▸ .venv 가 없다. 만든다 (한 번만)"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "▸ 설치 완료"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "▸ .env 를 만들었다. 유료 키 없이 돌리려면 .env 에서 AGENT_BACKEND 를"
  echo "  claude / opencode / ollama 중 하나로 바꾼다 (openai 만 키가 필요하다)."
fi

backend=$(grep -E '^AGENT_BACKEND=' .env | cut -d= -f2- | tr -d ' ')
key=$(grep -E '^OPENAI_API_KEY=.+' .env || true)
if [ "${backend:-openai}" = "openai" ] && [ -z "$key" ]; then
  echo "⚠ AGENT_BACKEND=openai 인데 OPENAI_API_KEY 가 비어 있다."
  echo "  .env 에 키를 넣거나, AGENT_BACKEND 를 claude/opencode/ollama 로 바꾼다."
  echo "  키 없이 볼 수 있는 것: ./run.sh check, ./run.sh dry \"문의\""
  echo
fi

cmd="${1:-demo}"
case "$cmd" in
  demo)
    echo "▸ 데모를 띄운다 → http://localhost:8601"
    echo "  URL 로도 된다: 'http://localhost:8601/?q=제주도인데 배송비 더 붙나요?&run=1'"
    echo "  멈추려면 Ctrl+C"
    # 설정에서 headless 로 두었다(첫 실행 이메일 프롬프트를 없애려고). 그래서
    # 브라우저는 여기서 연다 — 서버가 응답하기 시작하면.
    (
      for _ in $(seq 1 40); do
        if curl -sf -o /dev/null http://localhost:8601/ 2>/dev/null; then
          command -v open >/dev/null && open http://localhost:8601
          break
        fi
        sleep 0.5
      done
    ) &
    exec .venv/bin/streamlit run app.py
    ;;
  check)
    $PY scripts/check_context.py
    echo
    $PY scripts/check_mockdb.py
    echo
    $PY scripts/check_grader.py
    ;;
  ask)   shift; exec $PY -m src.agent "$@" ;;
  dry)   shift; exec $PY -m src.agent --dry-run "$@" ;;
  eval)  shift; exec $PY -m src.evaluate --task answer "$@" ;;
  table) exec $PY scripts/report_table.py ;;
  *)
    echo "모르는 명령: $cmd"
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac
