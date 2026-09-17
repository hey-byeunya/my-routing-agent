"""LangGraph 파이프라인.

    START → classify → assemble_context ─(확신도 < τ)→ escalate ┐
                              │                                 │
                              └─(≥ τ)→ plan ─(도구 있음)────────→ tools → answer → verify → END
                                        └────(도구 없음)──────────────────↗

판정(LLM)과 이관 결정(파이썬 임계값)을 갈랐다. 과제가 "카테고리를 고르는 일과,
확신이 없을 때 넘기는 판단을 분리한다"를 요구한다.

도구 호출은 plan 노드가 JSON 으로 계획을 내놓고 ToolNode 가 실행한다. 네이티브
function calling 이 없는 백엔드(claude·opencode CLI)도 같은 경로로 돌아야
수치가 비교 가능하기 때문이다. "실제로 호출한 도구"는 계획이 아니라 상태에
쌓인 ToolMessage 에서 뽑는다.
"""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode

from routing_agent import prompts
from routing_agent.context import build_context, select_units
from routing_agent.llm_backends import BackendError, backend_of, make_llm
from routing_agent.schemas import AnswerPlan, RouteDecision
from routing_agent.tools import ALL_TOOLS, tool_menu, tools_for

# sweep 으로 정했다 (평가 24턴, gpt-4o-mini): 0.6→도구 0.875, 0.5→0.917,
# 0.4→1.000, 0.3→1.000. 0.3 과 0.4 가 같으므로 더 보수적인 쪽을 택한다.
# 주의: 평가셋 위에서 훑은 값이라 그만큼 낙관적이다 (REPORT §4 에 고지).
DEFAULT_THRESHOLD = 0.4
# 같은 사안을 이 횟수만큼 되묻고도 확신이 서지 않으면 사람에게 넘긴다.
# 정책 §10.2 가 "동일 사안 3회 이상"을 이관 조건으로 두는 것에 맞춘다.
ASK_LIMIT = 3

ESCALATE_FALLBACK_TEXT = (
    "죄송합니다. 문의하신 내용을 정확히 확인하기 어려워 담당자에게 연결해 드리겠습니다."
)


class AgentState(TypedDict, total=False):
    question: str
    history: list[tuple]  # (역할, 발화) 또는 (역할, 발화, 카테고리)
    route: str
    confidence: float
    reason: str
    context: str
    context_units: list[str]
    messages: Annotated[list, add_messages]
    action: str
    ask: list[str]
    answer: str
    verdict: dict
    fallback: str
    trace: list[dict]


def _history_text(history: list[tuple] | None) -> str:
    """이력을 프롬프트에 넣을 텍스트로. 항목은 (역할, 발화) 또는 (역할, 발화, 카테고리)."""
    if not history:
        return ""
    who = {"customer": "고객", "agent": "상담원"}
    return "\n".join(f"{who.get(item[0], item[0])}: {item[1]}" for item in history if item[1])


def asks_on_issue(history: list[tuple] | None, route: str | None) -> int:
    """이 사안으로 상담원이 이미 몇 번 답했는가.

    이력 항목에 카테고리가 없으면(옛 형식) 0 으로 본다 — 모르는 채로 "이미
    물어봤다"고 세면 첫 문의를 이관해 버린다.
    """
    if not history or not route:
        return 0
    return sum(1 for item in history
               if len(item) >= 3 and item[0] == "agent" and item[2] == route and item[1])


def should_escalate(route: str | None, confidence: float, history: list[tuple] | None,
                    threshold: float = DEFAULT_THRESHOLD, ask_limit: int = ASK_LIMIT) -> bool:
    """이 턴을 사람에게 넘길 것인가. LLM 없이 도는 순수 함수라 픽스처로 검증한다.

    규칙 세 줄이다.
      · 범위 밖(OTHER)은 넘기지 않는다 — 채널 안내로 끝낸다(§10.1).
      · 확신이 서면 넘기지 않는다.
      · 확신이 안 서면, 같은 사안을 ask_limit 번 되물은 뒤에 넘긴다(§10.2).
    """
    if route == "OTHER":
        return False
    if confidence >= threshold:
        return False
    return asks_on_issue(history, route) >= ask_limit


def conversation_progress(history: list[tuple] | None, route: str | None = None) -> str:
    """**같은 사안을** 몇 번째 묻고 있는지. 대화가 길다는 것과는 다르다.

    정책 §10.2 의 이관 조건은 "동일 사안 3회 이상"이다. 한 대화에 여러 사안이
    섞이는 것이 보통이라("편의점 접수가 안 돼요" 다음에 "방문택배 내일 오나요?"),
    대화의 턴 수를 그대로 세면 다른 것을 물었는데도 반복 문의로 몰려 이관된다.
    그래서 **이번 턴과 같은 카테고리인 지난 발화만** 센다.

    이력에 카테고리가 없으면(옛 형식) 세지 않는다 — 모르는 채로 반복이라고
    말하는 것보다 아무 말도 하지 않는 편이 낫다.
    """
    if not history or not route:
        return ""
    tagged = [item for item in history if len(item) >= 3 and item[0] == "customer"]
    if not tagged:
        return ""
    same = sum(1 for item in tagged if item[2] == route)
    if same == 0:
        return "이 사안은 이번 대화에서 처음 나왔다. 앞 턴이 있다고 반복 문의는 아니다."
    line = f"고객이 이 사안({route})을 묻는 것은 이번이 {same + 1}번째다."
    if same + 1 >= 3:
        line += " §10.2 의 반복 문의 이관 기준을 검토하라."
    return line


def _tool_results_text(messages: list) -> str:
    rows = []
    for message in messages:
        if isinstance(message, ToolMessage):
            rows.append(f"{message.name}: {message.content}")
    return "\n".join(rows)


def called_tools(state: dict) -> list[str]:
    """실제로 실행된 도구. 계획이 아니라 상태에 쌓인 ToolMessage 에서 뽑는다."""
    return [m.name for m in state.get("messages", []) if isinstance(m, ToolMessage)]


def build_graph(
    llm=None,
    threshold: float = DEFAULT_THRESHOLD,
    verbose: bool = False,
):
    llm = llm or make_llm()
    backend = backend_of(llm)

    def log(step: str, text: str) -> None:
        if verbose:
            print(f"  [{step}] {text}", file=sys.stderr)

    def _note(state: AgentState, stage: str, **fields) -> list[dict]:
        return [*state.get("trace", []), {"stage": stage, **fields}]

    # ------------------------------------------------------------ 1/6 판정
    def classify(state: AgentState) -> dict:
        question = state["question"]
        # 이전 대화를 함께 준다. 후속 턴은 그것만 떼어 놓으면 뜻이 없다 —
        # "다시 해봐도 그대로예요" 같은 발화는 앞 대화 없이는 어느 카테고리인지 알 수 없다.
        history = _history_text(state.get("history"))
        prompt = prompts.route_guide()
        if history:
            prompt += f"\n\n[이전 대화]\n{history}"
        prompt += f'\n\n고객 문의: "{question}"'
        try:
            chain = llm.with_structured_output(RouteDecision).with_retry(
                retry_if_exception_type=(OutputParserException, BackendError),
                stop_after_attempt=2,
                wait_exponential_jitter=False,
            )
            decision: RouteDecision = chain.invoke(prompt)
            log("1/6 판정", f"{decision.route} conf={decision.confidence:.2f}")
            return {
                "route": decision.route,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "trace": _note(state, "classify", route=decision.route, confidence=decision.confidence),
            }
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 폴백으로 흘려보낸다
            route = prompts.rule_route(question)
            log("1/6 판정", f"실패 → 규칙 폴백 {route} ({type(exc).__name__})")
            return {
                "route": route,
                "confidence": 0.0,
                "reason": f"LLM 판정 실패로 규칙 기반 폴백 ({type(exc).__name__})",
                "fallback": "classify",
                "trace": _note(state, "classify", route=route, confidence=0.0, fallback=str(exc)[:200]),
            }

    # ------------------------------------------------------------ 2/6 근거 조립
    def assemble_context(state: AgentState) -> dict:
        route = state["route"]
        units = select_units(route)
        log("2/6 근거", f"{route} — 절 {len(units)}개 {sum(u.chars for u in units):,}자")
        return {
            "context": build_context(route),
            "context_units": [u.title for u in units],
            "trace": _note(state, "context", route=route, units=len(units)),
        }

    def gate(state: AgentState) -> Literal["plan", "escalate"]:
        """확신이 없으면 넘긴다 — 단, **되물어서 풀릴 일이면 먼저 되묻는다.**

        확신도가 낮은 데에는 두 가지가 섞여 있다. 고객이 아직 정보를 덜 준 것과,
        줄 만큼 줬는데도 우리가 못 푸는 것. 앞의 것은 사람에게 넘길 일이 아니다 —
        "얼마에요?" 한 마디에 담당자를 부르면 고객은 되물음 한 번이면 끝날 일에
        상담원을 기다린다. 정책 §10.2 의 이관 조건에도 "분류 확신도가 낮음"은 없다.

        그래서 같은 사안에서 **ASK_LIMIT 번까지는 되묻고**, 그러고도 확신이 서지
        않으면 넘긴다. OTHER 는 확신도와 무관하게 넘기지 않는다 — 범위 밖은
        이관이 아니라 채널 안내로 끝낸다(§10.1).
        """
        return "escalate" if should_escalate(
            state.get("route"), state.get("confidence", 0.0) or 0.0, state.get("history"), threshold
        ) else "plan"

    # ------------------------------------------------------------ 3/6 계획
    def plan(state: AgentState) -> dict:
        route = state["route"]
        prompt = prompts.plan_prompt(
            route=route,
            context=state["context"],
            tool_menu=tool_menu(route),
            history=_history_text(state.get("history")),
            question=state["question"],
            progress=conversation_progress(state.get("history"), route),
        )
        allowed = {t.name for t in tools_for(route)}
        try:
            chain = llm.with_structured_output(AnswerPlan).with_retry(
                retry_if_exception_type=(OutputParserException, BackendError),
                stop_after_attempt=2,
                wait_exponential_jitter=False,
            )
            decided: AnswerPlan = chain.invoke(prompt)
            action, ask = decided.action, list(decided.ask)
            # 카테고리 메뉴에 없는 도구는 버린다. 없는 도구를 부르면 ToolNode 가 터진다.
            calls = [c for c in decided.tools if c.name in allowed]
            dropped = [c.name for c in decided.tools if c.name not in allowed]
            if dropped:
                log("3/6 계획", f"메뉴에 없는 도구 무시: {dropped}")
            fallback = None
        except Exception as exc:  # noqa: BLE001
            log("3/6 계획", f"실패 → 되묻기로 폴백 ({type(exc).__name__})")
            action, ask, calls, fallback = "ASK", ["문의하신 내용을 조금 더 자세히"], [], "plan"

        message = AIMessage(
            content="",
            tool_calls=[
                {"name": c.name, "args": dict(c.args), "id": f"call_{i}_{abs(hash(c.name)) % 10**6}"}
                for i, c in enumerate(calls)
            ],
        )
        log("3/6 계획", f"{action} tools={[c.name for c in calls] or '없음'}")
        out: dict[str, Any] = {
            "action": action,
            "ask": ask,
            "messages": [message],
            "trace": _note(state, "plan", action=action, tools=[c.name for c in calls]),
        }
        if fallback:
            out["fallback"] = fallback
        return out

    def escalate(state: AgentState) -> dict:
        log("3/6 계획", f"확신도 {state.get('confidence', 0.0):.2f} < {threshold} → 이관")
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "escalate_to_agent",
                    "args": {
                        "reason": "확신도 미달",
                        "summary": f"자동 분류 확신도 {state.get('confidence', 0.0):.2f} 로 임계값 {threshold} 미달. 문의: {state['question']}",
                        "sentiment": "neutral",
                    },
                    "id": "call_escalate_gate",
                }
            ],
        )
        return {
            "action": "ESCALATE",
            "ask": [],
            "messages": [message],
            "trace": _note(state, "gate", escalated=True, confidence=state.get("confidence", 0.0)),
        }

    def has_tools(state: AgentState) -> Literal["tools", "answer"]:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "answer"

    # ------------------------------------------------------------ 5/6 답변
    def answer(state: AgentState) -> dict:
        results = _tool_results_text(state.get("messages", []))
        prompt = prompts.answer_prompt(
            route=state["route"],
            context=state["context"],
            action=state.get("action", "ANSWER"),
            ask=state.get("ask", []),
            tool_results=results,
            history=_history_text(state.get("history")),
            question=state["question"],
        )
        try:
            text = str(llm.invoke(prompt).content).strip()
            if not text:
                raise BackendError("빈 답변")
            log("5/6 답변", f"{len(text)}자")
            return {"answer": text, "trace": _note(state, "answer", chars=len(text))}
        except Exception as exc:  # noqa: BLE001
            log("5/6 답변", f"실패 → 이관 문구 ({type(exc).__name__})")
            return {
                "answer": ESCALATE_FALLBACK_TEXT,
                "fallback": "answer",
                "trace": _note(state, "answer", fallback=str(exc)[:200]),
            }

    # ------------------------------------------------------------ 6/6 검증
    def verify(state: AgentState) -> dict:
        from routing_agent.guardrail import check_guardrail

        verdict = check_guardrail(
            answer=state.get("answer", ""),
            tool_results=[m.content for m in state.get("messages", []) if isinstance(m, ToolMessage)],
            context=state.get("context", ""),
            # 고객이 직접 말한 수는 근거가 있는 수다. 예약번호를 되읽어 주는 것을
            # "지어낸 수치"로 잡으면 오탐이 된다.
            said=f"{state.get('question', '')}\n{_history_text(state.get('history'))}",
        )
        log("6/6 검증", "통과" if verdict["ok"] else f"근거 없는 수치 {verdict['unsupported']}")
        return {"verdict": verdict, "trace": _note(state, "verify", ok=verdict["ok"])}

    graph = StateGraph(AgentState)
    graph.add_node("classify", classify)
    graph.add_node("assemble_context", assemble_context)
    graph.add_node("plan", plan)
    graph.add_node("escalate", escalate)
    graph.add_node("tools", ToolNode(ALL_TOOLS, handle_tool_errors=True))
    graph.add_node("answer", answer)
    graph.add_node("verify", verify)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "assemble_context")
    graph.add_conditional_edges("assemble_context", gate, {"plan": "plan", "escalate": "escalate"})
    graph.add_conditional_edges("plan", has_tools, {"tools": "tools", "answer": "answer"})
    graph.add_edge("escalate", "tools")
    graph.add_edge("tools", "answer")
    graph.add_edge("answer", "verify")
    graph.add_edge("verify", END)

    compiled = graph.compile()
    compiled.backend_name = backend  # evaluate·app 이 어느 백엔드로 돌았는지 적을 때 쓴다
    return compiled


def run_once(
    question: str,
    history: list[tuple[str, str]] | None = None,
    llm=None,
    threshold: float = DEFAULT_THRESHOLD,
    verbose: bool = False,
) -> dict:
    graph = build_graph(llm=llm, threshold=threshold, verbose=verbose)
    state = graph.invoke({"question": question, "history": history or [], "messages": [], "trace": []})
    state["called_tools"] = called_tools(state)
    state["backend"] = graph.backend_name
    return state


# ---------------------------------------------------------------- CLI


def _dry_run(question: str) -> int:
    """LLM 을 부르지 않고 앞단만 보여 준다. 기록도 남기지 않는다."""
    route = prompts.rule_route(question)
    units = select_units(route)
    print(f'문의: "{question}"\n')
    print(f"[1/6 판정] 규칙 기반 → {route}  (--dry-run 은 LLM 을 부르지 않는다)")
    print(f"[2/6 근거] 절 {len(units)}개 / {sum(u.chars for u in units):,}자")
    common = {u.key for u in select_units("OTHER")} & {u.key for u in select_units("VISIT_PICKUP")}
    for unit in units:
        mark = " " if unit.key in common else "*"
        print(f"   {mark} {unit.title}  ({unit.chars:,}자)")
    print("\n   * 는 이 카테고리 고유 근거 (공통 절이 아닌 것)")
    print(f"\n[3/6 계획] 부를 수 있는 도구\n{tool_menu(route)}")
    print("\n여기서 멈춘다. 답변·검증은 LLM 이 필요하다.")
    return 0


def _main() -> int:
    import argparse

    from dotenv import load_dotenv

    from routing_agent import record

    load_dotenv(BASE_ENV := (__import__("pathlib").Path(__file__).resolve().parents[2] / ".env"))
    _ = BASE_ENV

    parser = argparse.ArgumentParser(description="라우팅 에이전트 한 건 실행")
    parser.add_argument("question")
    parser.add_argument("--dry-run", action="store_true", help="LLM 없이 분류·근거 조립까지만")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--mermaid", action="store_true", help="파이프라인 구조도를 Mermaid 로 출력")
    args = parser.parse_args()

    if args.mermaid:
        # 구조도는 그래프 모양만 그린다. 백엔드가 하나도 없어도 나와야 한다.
        from routing_agent.llm_backends import ReplayChat

        print(build_graph(llm=ReplayChat()).get_graph().draw_mermaid())
        return 0

    if args.dry_run:
        return _dry_run(args.question)

    llm = make_llm(args.backend, args.model)
    state = run_once(args.question, llm=llm, threshold=args.threshold, verbose=True)

    print(f"\n카테고리 : {state['route']}  (확신도 {state.get('confidence', 0):.2f})")
    print(f"판단 근거 : {state.get('reason', '')}")
    print(f"행동     : {state.get('action')}")
    print(f"호출 도구 : {state['called_tools'] or '없음'}")
    print(f"\n답변\n{state.get('answer', '')}")
    verdict = state.get("verdict", {})
    print(f"\n검증     : {'통과' if verdict.get('ok') else '근거 없는 수치 ' + str(verdict.get('unsupported'))}")
    if state.get("fallback"):
        print(f"폴백     : {state['fallback']} 단계에서 폴백으로 돌았다")

    record.append(
        "run_once",
        1,
        detail=state["route"],
        backend=state["backend"],
        action=state.get("action"),
        tools=state["called_tools"],
        fallback=state.get("fallback"),
        guardrail_ok=verdict.get("ok"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
