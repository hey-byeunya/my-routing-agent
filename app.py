"""Streamlit 데모. 답변만 보여 주지 않고 파이프라인 6단계를 그대로 펼친다.

화면의 목적은 "맞는 답을 냈다"가 아니라 **"이 답이 어디서 나왔는지 보인다"** 이다.
그래서 프롬프트에 실제로 들어간 근거 절, 호출한 도구와 인자, 가드레일 판정을
답변 바로 아래에 접어 둔다. 채점자가 한 화면에서 경로를 되짚을 수 있어야 한다.

**채팅 화면이다.** 상담은 한 번 묻고 끝나지 않는다. 정책 §10.2 는 같은 사안을
반복해 묻는 경우를 이관 조건으로 두는데, 매 턴을 첫 문의로 취급하면 그 기준이
성립할 수가 없다. 앞선 턴은 화면에 그대로 남고 다음 판정의 이력으로 들어간다.
입력창은 아래에 고정되고, 새 답변만 아래에 붙는다 — 폼처럼 화면이 통째로
새로 그려지며 방금 친 것이 사라지지 않는다.

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

st.set_page_config(page_title="택배중계 고객센터 에이전트", page_icon="📦", layout="wide")

EXAMPLES = {
    "운임 조회가 필요한 문의": "롯데택배로 방문택배 보낼 건데 2kg에 60cm 이하예요. 얼마예요?",
    "조회 없이 답하는 문의": "박스 크기는 어떻게 재나요? 제일 긴 쪽 기준인가요?",
    "범위 밖 문의": "혹시 거기 채용 공고 있나요?",
    "예약 경로 문의": "택배 예약 어디서 해요? 처음이라 하나도 모르겠어요.",
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
    "τ 기본값 0.4 는 평가 29턴에서 0.3~0.6 을 훑어 정했다 (REPORT §4). "
    "평가셋 위에서 훑은 값이라 그만큼 낙관적이다."
)

turns: list[dict] = st.session_state.setdefault("turns", [])

with st.sidebar.expander("백엔드별 상태"):
    for name in BACKENDS:
        st.write(f"- **{name}** — {status[name] or '사용 가능'}")


# ---------------------------------------------------------------- 머리말

st.title("📦 택배중계 고객센터 에이전트")
st.caption("문의 하나 → ① 카테고리 판정 → ② 근거 조립 → ③ 계획·도구 → ④ 답변 → ⑤ 검증")

cols = st.columns(len(EXAMPLES))
for col, (label, text) in zip(cols, EXAMPLES.items()):
    # 예시는 입력창을 채우는 대신 그대로 보낸다. 채워 놓고 지우면 방금 친 것이
    # 날아간 것처럼 보이기 때문이다.
    if col.button(label, use_container_width=True):
        st.session_state["pending"] = text

if not turns:
    st.info(
        "문의를 입력하면 답변과 함께 **그 답이 나온 경로**를 펼쳐 볼 수 있다. "
        "이어서 물으면 앞 대화가 이력으로 들어간다 — 같은 사안을 반복하면 이관 기준(§10.2)이 걸린다."
    )


# ---------------------------------------------------------------- 한 턴 그리기


def draw_detail(turn: dict) -> None:
    """이 답이 어디서 나왔는지. 답변 아래에 접어 둔다."""
    state, route = turn["state"], turn["route"]
    verdict = state.get("verdict") or {}

    badges = [f"백엔드 `{turn['backend']}`", f"{turn['elapsed']:.1f}초",
              f"카테고리 **{route}** · 확신도 {turn['confidence']:.2f} (τ={turn['threshold']:.2f})",
              f"행동 {state.get('action')}"]
    if turn.get("backend_note"):
        badges.append(f"⚠️ {turn['backend_note']}")
    if state.get("fallback"):
        badges.append(f"⚠️ **폴백** — `{state['fallback']}` 단계")
    if verdict.get("ok"):
        badges.append(f"✅ 가드레일 통과 (근거 수치 {verdict.get('grounded_count', 0)}개)")
    st.caption(" · ".join(badges))
    if verdict and not verdict.get("ok"):
        st.error(f"❌ 근거 없는 수치: {verdict.get('unsupported')}")

    with st.expander("이 답이 나온 경로"):
        st.markdown(f"**① 판정** — {route} · 확신도 {turn['confidence']:.2f}\n\n{state.get('reason', '')}")
        if turn["confidence"] < turn["threshold"] and state.get("action") == "ESCALATE":
            st.info("확신도가 τ 아래라 답변을 만들지 않고 담당자에게 넘겼다.")
        elif turn["confidence"] < turn["threshold"]:
            st.info(f"확신도는 τ 아래지만 카테고리가 {route} 라 이관하지 않았다 — "
                    "범위 밖 문의는 담당자 이관이 아니라 채널 안내로 끝낸다 (정책 §10.1).")

        units = select_units(route)
        st.markdown(f"**② 근거** — 절 {len(units)}개 / {sum(u.chars for u in units):,}자")
        for unit in units:
            with st.expander(f"{unit.title}  ({unit.chars:,}자)"):
                st.markdown(unit.body)

        st.markdown(f"**③ 계획과 도구** — 행동 {state.get('action')} / 호출 {turn['tools'] or '없음'}")
        if state.get("ask"):
            st.write("되물을 항목:", state["ask"])
        calls = [c for m in state.get("messages", []) if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
        for call in calls:
            st.markdown(f"`{call['name']}`")
            st.json(call["args"])
        for message in state.get("messages", []):
            if isinstance(message, ToolMessage):
                st.markdown(f"↳ `{message.name}` 결과")
                st.code(str(message.content)[:2000])
        if not calls:
            st.caption("도구를 부르지 않았다. 이 카테고리에서 부를 수 있었던 도구:")
            st.code(tool_menu(route))

        st.markdown("**⑥ 실행 기록 (trace)**")
        st.json(state.get("trace", []))


for past in turns:
    with st.chat_message("user"):
        st.write(past["question"])
    with st.chat_message("assistant"):
        st.write(past["answer"])
        draw_detail(past)


# ---------------------------------------------------------------- 입력과 실행

# URL 로도 받는다: ?q=문의&run=1 (&backend=openai). 링크 하나로 같은 화면을 다시
# 띄울 수 있어야 캡처도 남기고 남에게 보여 주기도 쉽다. 새로고침마다 다시 보내지
# 않도록 한 번만 보낸다.
params = st.query_params
if params.get("q") and params.get("run", "") in ("1", "true", "yes") and not st.session_state.get("url_sent"):
    st.session_state["pending"] = params["q"]
    st.session_state["url_sent"] = True

question = st.session_state.pop("pending", None)

if question:
    with st.chat_message("user"):
        st.write(question)

    try:
        llm = make_llm(choice, model or None, quiet=True)
    except BackendError as exc:
        st.error(f"백엔드를 준비하지 못했다 — {exc}")
        st.stop()

    # 앞선 턴이 이력이 된다. 이것이 없으면 §10.2(반복 문의 이관)가 성립하지 않는다.
    # 각 발화에 그때의 카테고리를 함께 붙인다 — 한 대화에 여러 사안이 섞이므로,
    # 무엇을 몇 번 물었는지는 카테고리까지 봐야 알 수 있다.
    history = [(role, text, past["route"]) for past in turns
               for role, text in (("customer", past["question"]), ("agent", past["answer"]))]

    graph = build_graph(llm=llm, threshold=threshold)
    with st.chat_message("assistant"):
        started = time.monotonic()
        with st.spinner(f"{graph.backend_name} 로 파이프라인을 돌리는 중…"):
            state = graph.invoke(
                {"question": question, "history": history, "messages": [], "trace": []}
            )
        turn = {
            "question": question,
            "answer": state.get("answer", ""),
            "state": state,
            "route": state.get("route", "?"),
            "confidence": state.get("confidence", 0.0) or 0.0,
            "tools": called_tools(state),
            "elapsed": time.monotonic() - started,
            "backend": graph.backend_name,
            "backend_note": f"`{choice}` 대신 돌았다" if graph.backend_name != choice else "",
            "threshold": threshold,
        }
        turns = [*turns, turn]
        st.session_state["turns"] = turns
        st.write(turn["answer"])
        draw_detail(turn)


# ---------------------------------------------------------------- 입력줄

# 입력칸과 같은 줄 오른쪽에 새 대화 버튼을 둔다. 대화를 비우는 일은 입력 옆에
# 있어야 손이 간다 — 사이드바에 있으면 대화 중에 찾지 않는다.
#
# chat_input 은 최상단에 둘 때만 화면 아래에 붙는다. 컬럼 안에 넣으면 그 성질을
# 잃으므로 sticky 로 다시 붙이고, **대화를 다 그린 뒤** 마지막에 그린다 —
# sticky 는 문서 순서를 타기 때문에 위에 두면 대화 위로 올라간다.
st.markdown(
    """
    <style>
      .st-key-composer {
        position: sticky; bottom: 0; z-index: 50;
        background: var(--background-color);
        padding: 0.35rem 0 0.15rem 0;
      }
      /* 새 대화 버튼을 입력칸의 전송 버튼과 같은 모양·같은 줄에 둔다.
         값은 stChatInputSubmitButton 에서 그대로 읽어 온 것이다 (32x32, 반경 8px).
         margin-bottom 은 입력칸 안쪽 여백만큼으로, 두 버튼의 아랫변을 맞춘다. */
      .st-key-composer .stButton button {
        width: 32px; height: 32px; min-height: 32px;
        padding: 6px; border: none; border-radius: 8px;
        background: rgba(151, 166, 195, 0.15);
        color: rgba(49, 51, 63, 0.4);
        margin-bottom: 13px;
      }
      .st-key-composer .stButton button:hover:not(:disabled) {
        background: rgba(151, 166, 195, 0.3);
        color: inherit;
      }
      .st-key-composer .stButton button p { font-size: 15px; line-height: 1; margin: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.container(key="composer"):
    box, reset = st.columns([18, 1], vertical_alignment="bottom")
    with box:
        typed = st.chat_input("문의를 입력하세요 — 이어서 물으면 앞 대화가 이력으로 들어간다")
    with reset:
        if st.button("🔄", help="새 대화 시작 (지금까지의 대화를 비운다)",
                     use_container_width=True, disabled=not turns):
            st.session_state["turns"] = []
            st.session_state.pop("pending", None)
            st.rerun()

# 보낸 문의는 다음 실행에서 처리한다. 그래야 대화가 먼저 그려지고 입력줄이
# 그 아래에 남는다.
if typed:
    st.session_state["pending"] = typed
    st.rerun()
