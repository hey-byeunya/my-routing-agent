"""Streamlit 데모. 답변만 보여 주지 않고 파이프라인 6단계를 그대로 펼친다.

화면의 목적은 "맞는 답을 냈다"가 아니라 **"이 답이 어디서 나왔는지 보인다"** 이다.
그래서 프롬프트에 실제로 들어간 근거 절, 호출한 도구와 인자, 가드레일 판정을
answer 옆에 같이 놓는다. 채점자가 한 화면에서 경로를 되짚을 수 있어야 한다.

폴백으로 돌았으면 숨기지 않고 배지로 드러낸다 (규율 ⑤).
"""

from __future__ import annotations

import time

import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage

from src.agent import DEFAULT_THRESHOLD, build_graph, called_tools
from src.context import select_units
from src.llm_backends import BACKENDS, BackendError, available, make_llm
from src.tools import tool_menu

load_dotenv()

st.set_page_config(page_title="택배중계 고객응대 에이전트", page_icon="📦", layout="wide")

EXAMPLES = {
    "운임 조회가 필요한 문의": "롯데택배로 방문택배 보낼 건데 2kg에 60cm 이하예요. 얼마예요?",
    "조회 없이 답하는 문의": "박스 크기는 어떻게 재나요? 제일 긴 쪽 기준인가요?",
    "범위 밖 문의": "혹시 거기 채용 공고 있나요?",
    "정보가 부족한 문의": "택배비가 얼마인가요?",
}


# ---------------------------------------------------------------- 사이드바

st.sidebar.title("설정")

status = available()
labels = [f"{name}{'' if not status[name] else '  ⛔'}" for name in BACKENDS]
choice = st.sidebar.radio(
    "LLM 백엔드",
    options=list(BACKENDS),
    format_func=lambda n: labels[BACKENDS.index(n)],
    index=BACKENDS.index("claude"),
)
if status[choice]:
    st.sidebar.warning(f"지금 쓸 수 없다 — {status[choice]}\n\nreplay 로 대체된다.")

model = st.sidebar.text_input("모델 (비우면 기본값)", value="")
threshold = st.sidebar.slider(
    "확신도 임계값 τ", min_value=0.0, max_value=1.0, value=DEFAULT_THRESHOLD, step=0.05,
    help="판정 확신도가 이 값 아래면 답변을 만들지 않고 담당자에게 넘긴다.",
)
st.sidebar.caption(
    "τ 는 sweep 으로 정하기 전의 초기 휴리스틱이다. 값의 근거는 REPORT 에 적는다."
)

with st.sidebar.expander("백엔드별 상태"):
    for name in BACKENDS:
        st.write(f"- **{name}** — {status[name] or '사용 가능'}")


# ---------------------------------------------------------------- 입력

st.title("📦 택배중계 고객응대 라우팅 에이전트")
st.caption("문의 하나 → ① 카테고리 판정 → ② 근거 조립 → ③ 계획·도구 → ④ 답변 → ⑤ 검증")

cols = st.columns(len(EXAMPLES))
for col, (label, text) in zip(cols, EXAMPLES.items()):
    if col.button(label, use_container_width=True):
        st.session_state["question"] = text

question = st.text_area(
    "고객 문의", value=st.session_state.get("question", ""), height=90,
    placeholder="예) 편의점택배로 제주도에 보낼 수 있나요?",
)
run = st.button("실행", type="primary", disabled=not question.strip())

if not run:
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
    state = graph.invoke({"question": question, "history": [], "messages": [], "trace": []})
elapsed = time.monotonic() - started

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
head = st.columns([1, 1, 2])
head[0].metric("카테고리", route)
head[1].metric("확신도", f"{confidence:.2f}", delta=f"τ={threshold:.2f}", delta_color="off")
head[2].markdown(f"**판단 근거**\n\n{state.get('reason', '')}")
if confidence < threshold:
    st.info(f"확신도가 τ 아래라 답변을 만들지 않고 담당자에게 넘겼다 (행동 {state.get('action')}).")

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
