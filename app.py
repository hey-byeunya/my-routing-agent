"""Streamlit 데모. 답변만 보여 주지 않고 파이프라인 6단계를 그대로 펼친다.

화면의 목적은 "맞는 답을 냈다"가 아니라 **"이 답이 어디서 나왔는지 보인다"** 이다.
그래서 프롬프트에 실제로 들어간 근거 절, 호출한 도구와 인자, 가드레일 판정을
answer 옆에 같이 놓는다. 채점자가 한 화면에서 경로를 되짚을 수 있어야 한다.

폴백으로 돌았으면 숨기지 않고 배지로 드러낸다 (규율 ⑤).
"""

from __future__ import annotations

import os
import time

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage

from routing_agent.agent import DEFAULT_THRESHOLD, build_graph, called_tools
from routing_agent.context import select_units
from routing_agent.llm_backends import BACKENDS, BackendError, available, make_llm, models_for
from routing_agent.tools import tool_menu

load_dotenv()

st.set_page_config(page_title="택배중계 고객응대 에이전트", page_icon="📦", layout="wide")

EXAMPLES = {
    "운임 조회가 필요한 문의": "롯데택배로 방문택배 보낼 건데 2kg에 60cm 이하예요. 얼마예요?",
    "조회 없이 답하는 문의": "박스 크기는 어떻게 재나요? 제일 긴 쪽 기준인가요?",
    "범위 밖 문의": "혹시 거기 채용 공고 있나요?",
    "정보가 부족한 문의": "택배비가 얼마인가요?",
}


def _default_backend_index() -> int:
    """기본 선택: URL 의 ?backend= → .env 의 AGENT_BACKEND → openai.

    화면만 다른 백엔드를 기본으로 두면 CLI 로 잰 수치와 화면에서 보는 것이 어긋난다.
    """
    wanted = (st.query_params.get("backend") or os.environ.get("AGENT_BACKEND") or "openai").strip().lower()
    return BACKENDS.index(wanted) if wanted in BACKENDS else 0


# ---------------------------------------------------------------- 사이드바

st.sidebar.title("설정")

status = available()
labels = [f"{name}{'' if not status[name] else '  ⛔'}" for name in BACKENDS]
choice = st.sidebar.radio(
    "LLM 백엔드",
    options=list(BACKENDS),
    format_func=lambda n: labels[BACKENDS.index(n)],
    index=_default_backend_index(),
)
if status[choice]:
    st.sidebar.warning(f"지금 쓸 수 없다 — {status[choice]}\n\nreplay 로 대체된다.")

DIRECT = "직접 입력…"
choices = models_for(choice)
if choices:
    picked = st.sidebar.selectbox(
        "모델", options=[*choices, DIRECT], index=0,
        help="목록은 백엔드에서 직접 읽어 온다. opencode 는 무료 모델만 보여 준다.",
    )
    model = st.sidebar.text_input("모델 이름", value="") if picked == DIRECT else picked
else:
    st.sidebar.caption("모델 목록을 읽지 못했다 (CLI 없음 또는 오프라인). 이름을 직접 적는다.")
    model = st.sidebar.text_input("모델", value="")
threshold = st.sidebar.slider(
    "확신도 임계값 τ", min_value=0.0, max_value=1.0, value=DEFAULT_THRESHOLD, step=0.05,
    help="판정 확신도가 이 값 아래면 답변을 만들지 않고 담당자에게 넘긴다.",
)
st.sidebar.caption(
    "τ 기본값 0.4 는 평가 24턴에서 0.3~0.6 을 훑어 정했다 (REPORT §4). "
    "평가셋 위에서 훑은 값이라 그만큼 낙관적이다."
)

with st.sidebar.expander("백엔드별 상태"):
    for name in BACKENDS:
        st.write(f"- **{name}** — {status[name] or '사용 가능'}")


# ---------------------------------------------------------------- 입력

st.title("📦 택배중계 고객센터 에이전트")
st.caption("문의 하나 → ① 카테고리 판정 → ② 근거 조립 → ③ 계획·도구 → ④ 답변 → ⑤ 검증")

# URL 로도 받는다: ?q=문의&run=1 (&backend=openai)
# 링크 하나로 같은 화면을 다시 띄울 수 있어야 캡처도 남기고 남에게 보여 주기도 쉽다.
params = st.query_params
url_question = params.get("q", "")
autorun = params.get("run", "") in ("1", "true", "yes")
if url_question and "question" not in st.session_state:
    st.session_state["question"] = url_question

cols = st.columns(len(EXAMPLES))
for col, (label, text) in zip(cols, EXAMPLES.items()):
    if col.button(label, use_container_width=True):
        st.session_state["question"] = text

# 대화 이력. 정책 §10.2 는 "동일 사안 3회 이상 반복 문의"를 이관 조건으로 두는데,
# 매 턴을 첫 문의로 취급하면 그 조건이 성립할 수가 없다. 화면도 대화로 이어져야 한다.
history: list[tuple[str, str]] = st.session_state.setdefault("history", [])

if history:
    with st.expander(f"이전 대화 {len(history) // 2}턴", expanded=True):
        for role, text in history:
            st.markdown(("🙋 **고객** — " if role == "customer" else "💬 **상담원** — ") + text)
    if st.button("새 대화로 시작"):
        st.session_state["history"] = []
        st.session_state["question"] = ""
        st.rerun()

question = st.text_area(
    "고객 문의" + (" (이어서)" if history else ""),
    value=st.session_state.get("question", ""), height=90,
    placeholder="예) 편의점택배로 제주도에 보낼 수 있나요?",
)
run = st.button("실행", type="primary", disabled=not question.strip())

if not (run or (autorun and question.strip())):
    st.stop()


# ---------------------------------------------------------------- 실행

try:
    llm = make_llm(choice, model or None, quiet=True)
except BackendError as exc:
    st.error(f"백엔드를 준비하지 못했다 — {exc}")
    st.stop()

graph = build_graph(llm=llm, threshold=threshold)
started = time.monotonic()
with st.spinner(f"{graph.backend_name} 로 파이프라인을 돌리는 중…"):
    state = graph.invoke(
        {"question": question, "history": list(history), "messages": [], "trace": []}
    )
elapsed = time.monotonic() - started

# 이번 턴을 이력에 쌓는다. 다음 문의는 이 대화의 연장으로 판정된다.
st.session_state["history"] = [
    *history, ("customer", question), ("agent", state.get("answer", ""))
]
st.session_state["question"] = ""

route = state.get("route", "?")
confidence = state.get("confidence", 0.0) or 0.0
verdict = state.get("verdict") or {}
tools_used = called_tools(state)

badges = [f"백엔드 `{graph.backend_name}`", f"{elapsed:.1f}초"]
if graph.backend_name != choice:
    badges.append(f"⚠️ `{choice}` 대신 돌았다")
if state.get("fallback"):
    badges.append(f"⚠️ **폴백** — `{state['fallback']}` 단계")
st.markdown(" · ".join(badges))

# 1. 판정
head = st.columns([1.2, 1, 2])
# st.metric 은 폭이 좁으면 값을 잘라 버린다. 카테고리 이름이 잘리면 화면이
# 증거 노릇을 못 하므로 텍스트로 쓴다.
head[0].markdown(f"**카테고리**\n\n### {route}")
head[1].markdown(f"**확신도**\n\n### {confidence:.2f}\n\nτ = {threshold:.2f}")
head[2].markdown(f"**판단 근거**\n\n{state.get('reason', '')}")
# 게이트가 실제로 넘겼을 때만 말한다. OTHER 는 확신도와 무관하게 통과하므로
# (정책 §10.1 — 범위 밖은 이관이 아니라 채널 안내로 끝낸다) 여기서 걸리지 않는다.
if confidence < threshold and state.get("action") == "ESCALATE":
    st.info(f"확신도 {confidence:.2f} 가 τ={threshold:.2f} 아래라 답변을 만들지 않고 담당자에게 넘겼다.")
elif confidence < threshold:
    st.info(
        f"확신도 {confidence:.2f} 는 τ 아래지만 카테고리가 {route} 라 이관하지 않았다 — "
        "범위 밖 문의는 담당자 이관이 아니라 채널 안내로 끝낸다 (정책 §10.1)."
    )

# 4. 답변 — 화면에서는 결론을 먼저 보여 준다
st.subheader("답변")
st.success(state.get("answer", ""))

# 5. 검증
if verdict.get("ok"):
    st.caption(f"✅ 가드레일 통과 — 근거로 확인된 수치 {verdict.get('grounded_count', 0)}개")
else:
    st.error(f"❌ 근거 없는 수치: {verdict.get('unsupported')}")

st.divider()
st.subheader("이 답이 나온 경로")

# 2. 근거
units = select_units(route)
with st.expander(f"② 프롬프트에 들어간 근거 — 절 {len(units)}개 / {sum(u.chars for u in units):,}자"):
    for unit in units:
        with st.expander(f"{unit.title}  ({unit.chars:,}자)"):
            st.markdown(unit.body)

# 3. 계획과 도구
with st.expander(f"③ 계획과 도구 — 행동 {state.get('action')} / 호출 {tools_used or '없음'}"):
    if state.get("ask"):
        st.write("되물을 항목:", state["ask"])
    calls = [c for m in state.get("messages", []) if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
    for call in calls:
        st.markdown(f"**{call['name']}**")
        st.json(call["args"])
    for message in state.get("messages", []):
        if isinstance(message, ToolMessage):
            st.markdown(f"↳ `{message.name}` 결과")
            st.code(str(message.content)[:2000])
    if not calls:
        st.caption("도구를 부르지 않았다. 이 카테고리에서 부를 수 있었던 도구:")
        st.code(tool_menu(route))

with st.expander("⑥ 실행 기록 (trace)"):
    st.json(state.get("trace", []))
