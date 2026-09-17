"""두 가지를 잰다 — 라우팅 분류 성능, 그리고 답변 파이프라인의 두 지표.

결과는 runs/<이름>.json 에 원본 그대로 남기고, 요약 한 줄을 store/metrics.jsonl 에
덧붙인다. 원본을 남겨 두면 채점 기준을 고쳤을 때 API 를 다시 부르지 않고 다시 잴 수 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

# 저장소 루트. 패키지가 src/routing_agent/ 에 있으므로 두 단계 위가 아니라 세 단계 위다.
BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from routing_agent import record  # noqa: E402
from routing_agent.agent import DEFAULT_THRESHOLD, MIN_MARGIN, build_graph, called_tools  # noqa: E402
from routing_agent.dataset import assert_split_disjoint, gold_eval_turns  # noqa: E402
from routing_agent.grader import failure_reason, grade_turn, summarize  # noqa: E402
from routing_agent.llm_backends import backend_of, make_llm  # noqa: E402
from routing_agent.prompts import route_guide  # noqa: E402
from routing_agent.schemas import ROUTE_LIST, RouteDecision  # noqa: E402

RUNS = BASE / "runs"


def _load_eval_set() -> list[dict]:
    import csv

    path = BASE / "data" / "eval_set.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- 라우팅


def run_routing(llm, concurrency: int, limit: int | None, rule_only: bool = False) -> dict:
    rows = _load_eval_set()[: limit or None]

    started = time.monotonic()
    if rule_only:
        # LLM 없이 도는 기준선. LLM 성적을 이 수치와 견줘야 "나아졌다"고 말할 수 있다.
        from routing_agent.prompts import rule_route

        outputs = [RouteDecision(route=rule_route(r["question"]), confidence=0.0, reason="규칙 기반") for r in rows]
    else:
        chain = llm.with_structured_output(RouteDecision)
        prompts = [f'{route_guide()}\n\n고객 문의: "{row["question"]}"' for row in rows]
        outputs = chain.batch(prompts, config={"max_concurrency": concurrency}, return_exceptions=True)
    elapsed = time.monotonic() - started

    results = []
    for row, out in zip(rows, outputs):
        # 구조화 출력은 예외 말고 None 으로도 실패한다 — 작은 모델이 스키마에 맞는
        # JSON 을 못 내면 파서가 조용히 None 을 돌려준다. 그대로 두면 평가가 통째로
        # 죽어 "약한 백엔드는 얼마나 못하는가"를 아예 잴 수 없다. 실패로 세고 계속 간다.
        if isinstance(out, Exception) or out is None:
            error = f"{type(out).__name__}: {out}"[:200] if out is not None else "구조화 출력 없음(None)"
            results.append(
                {"qa_id": row["qa_id"], "question": row["question"], "gold": row["route"],
                 "pred": None, "confidence": 0.0, "error": error}
            )
            continue
        results.append(
            {"qa_id": row["qa_id"], "question": row["question"], "gold": row["route"],
             "pred": out.route, "confidence": out.confidence, "reason": out.reason}
        )

    scored = [r for r in results if r["pred"]]
    correct = sum(r["pred"] == r["gold"] for r in scored)
    metrics: dict = {
        "n": len(results),
        "errors": len(results) - len(scored),
        "accuracy": correct / len(results) if results else 0.0,
        "elapsed_s": round(elapsed, 1),
    }

    if not scored:
        # 백엔드가 한 건도 못 냈다. 그것도 결과다 — 0건으로 기록하고 끝낸다.
        # (여기서 죽으면 "이 백엔드는 쓸 수 없다"는 사실이 기록에 남지 않는다.)
        metrics["macro_f1"] = 0.0
        metrics["report"] = "유효한 판정 0건 — 이 백엔드는 구조화 출력을 내지 못했다."
        return {"task": "routing", "metrics": metrics, "results": results}

    try:
        from sklearn.metrics import classification_report, confusion_matrix, f1_score

        gold = [r["gold"] for r in scored]
        pred = [r["pred"] for r in scored]
        present = [r for r in ROUTE_LIST if r in set(gold)]
        metrics["macro_f1"] = float(f1_score(gold, pred, labels=present, average="macro", zero_division=0))
        metrics["report"] = classification_report(gold, pred, labels=present, zero_division=0)
        metrics["confusion"] = {
            "labels": present,
            "matrix": confusion_matrix(gold, pred, labels=present).tolist(),
        }
    except ImportError:
        metrics["macro_f1"] = None

    return {"task": "routing", "metrics": metrics, "results": results}


def print_routing(payload: dict) -> None:
    m = payload["metrics"]
    print(f"\n라우팅 {m['n']}건 — 정확도 {m['accuracy']:.3f}"
          + (f" · macro F1 {m['macro_f1']:.3f}" if m.get("macro_f1") is not None else "")
          + f" · {m['elapsed_s']}초"
          + (f" · 호출 실패 {m['errors']}건" if m["errors"] else ""))
    if m.get("report"):
        print("\n" + m["report"])
    if m.get("confusion"):
        labels = m["confusion"]["labels"]
        print("혼동 행렬 (행=정답, 열=예측)")
        print(" " * 18 + "".join(f"{l[:7]:>9}" for l in labels))
        for label, row in zip(labels, m["confusion"]["matrix"]):
            print(f"{label:<18}" + "".join(f"{v:>9}" for v in row))
    wrong = [r for r in payload["results"] if r["pred"] and r["pred"] != r["gold"]]
    if wrong:
        print(f"\n틀린 {len(wrong)}건")
        for r in wrong[:12]:
            print(f"  {r['gold']:<16} → {r['pred']:<16} conf={r['confidence']:.2f}  {r['question'][:44]}")



# ---------------------------------------------------------------- 하드케이스


def _load_hard_cases() -> list[dict]:
    import csv

    path = BASE / "data" / "hard_cases.csv"
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


# 라벨이 OTHER 지만 뜻이 "응대 범위 밖"이 아닌 유형.
# expected_behavior 가 "되물어야 한다"·"앞 턴을 봐야 안다"고 적고 있다. 우리 OTHER 는
# 채널 안내로 대화를 끝내는 칸이라 뜻이 정반대다 — 이 15건을 라우팅 정확도로 재면
# "얼마예요?" 에 '도와드리기 어렵습니다' 라고 답하는 것을 정답으로 세게 된다.
# 그래서 이 유형은 카테고리가 아니라 **되물었는가(action==ASK)** 로 잰다.
ASK_EXPECTED_TYPES = {"극단단답", "문맥의존"}


def run_hardcases(llm, concurrency: int, limit: int | None, rule_only: bool = False,
                  threshold: float = DEFAULT_THRESHOLD) -> dict:
    """분류기가 틀리기 쉬운 자리만 모은 45건. 유형마다 재는 것이 다르다.

    평가셋 42건은 평범한 문의라 수치가 후하게 나온다. 여기는 여섯 유형이고,
    **유형별로 갈라 봐야 "어떤 종류의 문의에 약한가"가 드러난다.**

    두 갈래로 나눠 잰다.
      · 라우팅 30건 (경계모호·다중의도·텍스트손상·분류체계밖) — 카테고리를 맞히는가.
        경계모호·다중의도는 데이터가 대안 라우트를 인정하므로 엄격/관대 둘 다 낸다.
      · 되물음 15건 (극단단답·문맥의존) — 카테고리가 아니라 되물었는가로 잰다.
        파이프라인을 끝까지 돌려 action 을 본다.
    """
    rows = _load_hard_cases()[: limit or None]
    routing_rows = [r for r in rows if r["hard_type"] not in ASK_EXPECTED_TYPES]
    ask_rows = [r for r in rows if r["hard_type"] in ASK_EXPECTED_TYPES]

    started = time.monotonic()

    # ── 라우팅 갈래
    if rule_only:
        from routing_agent.prompts import rule_route

        outs = [RouteDecision(route=rule_route(r["question"]), confidence=0.0, reason="규칙 기반") for r in routing_rows]
    else:
        chain = llm.with_structured_output(RouteDecision)
        prompts = [f'{route_guide()}\n\n고객 문의: "{r["question"]}"' for r in routing_rows]
        outs = chain.batch(prompts, config={"max_concurrency": concurrency}, return_exceptions=True)

    results = []
    for row, out in zip(routing_rows, outs):
        gold, alt = row["route_expected_v2"], (row.get("route_alt_v2") or "").strip()
        base = {"qa_id": row["qa_id"], "hard_type": row["hard_type"], "measure": "route",
                "question": row["question"], "gold": gold, "alt": alt,
                "expected_behavior": row.get("expected_behavior", "")}
        if isinstance(out, Exception) or out is None:
            reason = f"{type(out).__name__}: {out}"[:200] if isinstance(out, Exception) else "구조화 출력 실패(None)"
            results.append({**base, "pred": None, "confidence": 0.0, "ok": False, "lenient": False, "error": reason})
            continue
        ok = out.route == gold
        margin = (1.0 if out.alt_route == out.route
                  else max(0.0, out.confidence - out.alt_confidence))
        results.append({**base, "pred": out.route, "confidence": out.confidence, "reason": out.reason,
                        "alt_route": out.alt_route, "alt_confidence": out.alt_confidence,
                        "margin": margin,
                        "ok": ok, "lenient": ok or (bool(alt) and out.route == alt)})

    # ── 되물음 갈래 (파이프라인을 끝까지 돌려야 action 이 나온다)
    if ask_rows:
        if rule_only:
            for row in ask_rows:
                results.append({"qa_id": row["qa_id"], "hard_type": row["hard_type"], "measure": "ask",
                                "question": row["question"], "gold": "ASK", "alt": "",
                                "expected_behavior": row.get("expected_behavior", ""),
                                "pred": None, "confidence": 0.0, "ok": False, "lenient": False,
                                "error": "규칙 기준선은 파이프라인을 돌리지 않는다"})
        else:
            graph = build_graph(llm=llm, threshold=threshold)
            inputs = [{"question": r["question"], "history": [], "messages": [], "trace": []} for r in ask_rows]
            states = graph.batch(inputs, config={"max_concurrency": concurrency}, return_exceptions=True)
            for row, st in zip(ask_rows, states):
                base = {"qa_id": row["qa_id"], "hard_type": row["hard_type"], "measure": "ask",
                        "question": row["question"], "gold": "ASK", "alt": "",
                        "expected_behavior": row.get("expected_behavior", "")}
                if isinstance(st, Exception):
                    results.append({**base, "pred": None, "confidence": 0.0, "ok": False, "lenient": False,
                                    "error": f"{type(st).__name__}: {st}"[:200]})
                    continue
                action = st.get("action")
                results.append({**base, "pred": action, "route_pred": st.get("route"),
                                "confidence": st.get("confidence", 0.0), "answer": st.get("answer", ""),
                                "ok": action == "ASK", "lenient": action == "ASK"})

    elapsed = time.monotonic() - started

    by_type: dict[str, dict] = {}
    for r in results:
        t = by_type.setdefault(r["hard_type"], {"n": 0, "ok": 0, "lenient": 0, "conf": [], "measure": r["measure"]})
        t["n"] += 1
        t["ok"] += r["ok"]
        t["lenient"] += r["lenient"]
        if r["pred"]:
            t["conf"].append(r["confidence"])
    for t in by_type.values():
        t["acc"] = t["ok"] / t["n"]
        t["lenient_acc"] = t["lenient"] / t["n"]
        t["mean_conf"] = round(sum(t["conf"]) / len(t["conf"]), 2) if t["conf"] else 0.0
        t.pop("conf")

    route_res = [r for r in results if r["measure"] == "route"]
    ask_res = [r for r in results if r["measure"] == "ask"]
    correct = [r["confidence"] for r in route_res if r["ok"] and r["pred"]]
    wrong = [r["confidence"] for r in route_res if not r["ok"] and r["pred"]]
    metrics = {
        "n": len(results),
        "errors": sum(1 for r in results if not r["pred"]),
        "route_n": len(route_res),
        "route_accuracy": sum(r["ok"] for r in route_res) / (len(route_res) or 1),
        "route_lenient_accuracy": sum(r["lenient"] for r in route_res) / (len(route_res) or 1),
        "ask_n": len(ask_res),
        "ask_rate": sum(r["ok"] for r in ask_res) / (len(ask_res) or 1),
        "elapsed_s": round(elapsed, 1),
        "by_type": by_type,
        # 확신도가 난이도를 알아채는가. 틀린 건의 확신도가 낮아야 게이트(τ)가 걸러낸다.
        "mean_conf_correct": round(sum(correct) / len(correct), 3) if correct else None,
        "mean_conf_wrong": round(sum(wrong) / len(wrong), 3) if wrong else None,
        "wrong_below_tau": sum(1 for r in route_res if not r["ok"] and r["pred"] and r["confidence"] < threshold),
        "wrong_total": len(wrong),
        # 마진이 난이도를 알아채는가 — 확신도가 못 하던 일이다
        "mean_margin_correct": (lambda v: round(sum(v) / len(v), 3) if v else None)(
            [r["margin"] for r in route_res if r["ok"] and r.get("margin") is not None]),
        "mean_margin_wrong": (lambda v: round(sum(v) / len(v), 3) if v else None)(
            [r["margin"] for r in route_res if not r["ok"] and r.get("margin") is not None]),
        "wrong_caught_by_margin": sum(
            1 for r in route_res if not r["ok"] and r.get("margin") is not None and r["margin"] < MIN_MARGIN),
        "correct_flagged_by_margin": sum(
            1 for r in route_res if r["ok"] and r.get("margin") is not None and r["margin"] < MIN_MARGIN),
        "tau": threshold,
    }
    return {"task": "hardcases", "metrics": metrics, "results": results}


def print_hardcases(payload: dict) -> None:
    m = payload["metrics"]
    print(f"\n하드케이스 {m['n']}건 · {m['elapsed_s']}초"
          + (f" · 호출 실패 {m['errors']}건" if m["errors"] else ""))
    print(f"  라우팅 {m['route_n']}건 — 엄격 {m['route_accuracy']:.3f} · 대안 인정 {m['route_lenient_accuracy']:.3f}")
    print(f"  되물음 {m['ask_n']}건 — 되물은 비율 {m['ask_rate']:.3f}  (카테고리가 아니라 행동으로 잰다)")

    print(f"\n{'유형':<12}{'재는 것':<8}{'건수':>4}{'정답':>8}{'대안인정':>9}{'평균 확신도':>11}")
    for t, v in sorted(m["by_type"].items(), key=lambda kv: kv[1]["acc"]):
        label = "되물음" if v["measure"] == "ask" else "카테고리"
        print(f"{t:<12}{label:<8}{v['n']:>4}{v['acc']:>8.3f}{v['lenient_acc']:>9.3f}{v['mean_conf']:>11.2f}")

    if m["mean_conf_correct"] is not None:
        print(f"\n확신도(라우팅 갈래): 맞은 건 평균 {m['mean_conf_correct']} · 틀린 건 평균 {m['mean_conf_wrong']}")
        print(f"틀린 {m['wrong_total']}건 중 확신도 τ({m['tau']}) 미만은 {m['wrong_below_tau']}건")
    if m.get("mean_margin_correct") is not None:
        print(f"마진(1·2순위 차): 맞은 건 평균 {m['mean_margin_correct']} · 틀린 건 평균 {m['mean_margin_wrong']}")
        print(f"  마진 {MIN_MARGIN} 미만으로 잡힌 것 — 틀린 건 {m['wrong_caught_by_margin']}/{m['wrong_total']}, "
              f"맞은 건 {m['correct_flagged_by_margin']} (이건 괜한 되물음이 되는 몫)")

    wrong = [r for r in payload["results"] if r["pred"] and not r["ok"]]
    if wrong:
        print(f"\n틀린 {len(wrong)}건")
        for r in sorted(wrong, key=lambda r: (r["measure"], r["hard_type"])):
            mark = "대안" if r["lenient"] else "  "
            print(f"  {mark} {r['hard_type']:<10} {r['gold']:<10} → {str(r['pred']):<16} conf={r['confidence']:.2f}  {r['question'][:34]}")


# ---------------------------------------------------------------- 답변


def run_answer(llm, threshold: float, concurrency: int, limit: int | None, judge_llm=None) -> dict:
    turns = gold_eval_turns()[: limit or None]
    graph = build_graph(llm=llm, threshold=threshold)
    inputs = [
        {"question": t.question, "history": list(t.history), "messages": [], "trace": []}
        for t in turns
    ]

    started = time.monotonic()
    states = graph.batch(inputs, config={"max_concurrency": concurrency}, return_exceptions=True)
    elapsed = time.monotonic() - started

    results = []
    graded = []
    for turn, state in zip(turns, states):
        if isinstance(state, Exception):
            results.append({"item_id": turn.item_id, "route_gold": turn.route,
                            "error": f"{type(state).__name__}: {state}"[:300]})
            graded.append({"tools_ok": False, "answer_ok": False, "action_ok": False,
                           "expected_tools": sorted(turn.expect.get("tools", [])), "actual_tools": [],
                           "missing": [], "violated": [], "unasked": [], "judged_by_llm": []})
            continue

        actual = called_tools(state)
        result = grade_turn(
            expect=turn.expect,
            answer=state.get("answer", ""),
            called=actual,
            llm=judge_llm,
            action=state.get("action"),
        )
        graded.append(result)
        results.append(
            {
                "item_id": turn.item_id,
                "question": turn.question,
                "route_gold": turn.route,
                "route_pred": state.get("route"),
                "confidence": state.get("confidence"),
                "action_gold": turn.expect.get("action"),
                "action_pred": state.get("action"),
                "answer": state.get("answer", ""),
                "reference": turn.expect.get("reference", ""),
                "called_tools": actual,
                "expected_tools": sorted(turn.expect.get("tools", [])),
                "guardrail": state.get("verdict"),
                "fallback": state.get("fallback"),
                # 폴백이 인프라 실패인지 에이전트 판단인지 가리려면 예외 문구가 남아야 한다.
                "fallback_error": next(
                    (n["fallback"] for n in state.get("trace", []) if n.get("fallback")), None
                ),
                "grade": result,
                "reason": failure_reason(result),
            }
        )

    stats = summarize(graded)
    stats["elapsed_s"] = round(elapsed, 1)
    stats["route_accuracy"] = (
        sum(1 for r in results if r.get("route_pred") == r.get("route_gold")) / (len(results) or 1)
    )
    stats["guardrail_violations"] = sum(
        1 for r in results if r.get("guardrail") and not r["guardrail"].get("ok")
    )
    stats["fallbacks"] = sum(1 for r in results if r.get("fallback"))
    return {"task": "answer", "metrics": stats, "results": results}


def print_answer(payload: dict) -> None:
    m = payload["metrics"]
    print(
        f"\n답변 {m['n']}턴 — 도구 호출 적절성 {m['tool_score']:.3f} · 답변 적절성 {m['answer_score']:.3f}"
        f" · 둘 다 {m['both']:.3f} · {m['elapsed_s']}초"
    )
    print(
        f"  (참고) 행동 일치 {m['action_score']:.3f} · 카테고리 정확도 {m['route_accuracy']:.3f}"
        f" · 가드레일 위반 {m['guardrail_violations']} · 폴백 {m['fallbacks']}"
    )

    reasons = Counter(r["reason"] for r in payload["results"] if r.get("reason") and r["reason"] != "통과")
    if reasons:
        print("\n실패 원인")
        for reason, n in reasons.most_common():
            print(f"  {reason:<18} {n}")

    failed = [r for r in payload["results"] if r.get("grade") and not (r["grade"]["tools_ok"] and r["grade"]["answer_ok"])]
    if failed:
        print(f"\n틀린 {len(failed)}턴 — 수치만 세지 않고 직접 읽는다")
        for r in failed:
            print(f"\n  {r['item_id']} [{r['route_gold']}] {r['reason']}")
            print(f"    문의    : {r['question'][:70]}")
            print(f"    행동    : 기대 {r['action_gold']} / 실제 {r['action_pred']}")
            if not r["grade"]["tools_ok"]:
                print(f"    도구    : 기대 {r['expected_tools']} / 실제 {r['called_tools']}")
            if r["grade"]["missing"]:
                print(f"    빠진 사실: {r['grade']['missing']}")
            if r["grade"]["violated"]:
                print(f"    금지 위반: {r['grade']['violated']}")
            if r["grade"]["unasked"]:
                print(f"    안 물음  : {r['grade']['unasked']}")
            print(f"    답변    : {r['answer'][:110]}")
            print(f"    모범    : {r['reference'][:110]}")


# ---------------------------------------------------------------- CLI


def main() -> int:
    load_dotenv(BASE / ".env")
    parser = argparse.ArgumentParser(description="라우팅 에이전트 평가")
    parser.add_argument("--task", choices=["routing", "answer", "hardcases"], default="answer")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--judge-backend", default=None, help="답변 적절성 LLM 판정기. 비우면 문자열 매칭만")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--rule-baseline", action="store_true", help="LLM 없이 규칙 기준선만 잰다")
    parser.add_argument("--name", default=None, help="runs/<이름>.json 으로 저장")
    parser.add_argument("--note", default="", help="이번 시도에서 무엇을 바꿨는지. 기록표에 남는다")
    # 아래 다섯은 "개선 시도별 기록표"의 칸을 그대로 채운다 (scripts/report_table.py).
    # 수치만 쌓으면 나중에 왜 바꿨는지 복원할 수 없다. 잴 때 같이 적는다.
    parser.add_argument("--round", default=None, help="회차 (예: #2). 비우면 시간순으로 매긴다")
    parser.add_argument("--target", default="", help="변경 대상 (예: prompts.py route_guide)")
    parser.add_argument("--why", default="", help="핵심 변경 이유")
    parser.add_argument("--what", default="", help="핵심 변경 내용")
    parser.add_argument("--memo", default="", help="성과 및 오답 메모")
    args = parser.parse_args()

    assert_split_disjoint()
    llm = None if args.rule_baseline else make_llm(args.backend, args.model)
    backend = "rule" if llm is None else backend_of(llm)
    # opencode 는 전역 SQLite·파일 락을 공유해 많이 띄우면 경합한다. claude 는 구독 한도가 먼저 걸린다.
    concurrency = args.concurrency or {"opencode": 2, "claude": 3, "ollama": 2}.get(backend, 6)

    judge = make_llm(args.judge_backend, quiet=True) if args.judge_backend else None

    print(f"백엔드 {backend} · 모델 {getattr(llm, 'model_name', '?')} · 동시 실행 {concurrency}"
          + (f" · 판정기 {backend_of(judge)}" if judge else " · 판정기 없음(문자열 매칭만)"))

    if args.task == "hardcases":
        payload = run_hardcases(llm, concurrency, args.limit,
                                rule_only=args.rule_baseline, threshold=args.threshold)
        print_hardcases(payload)
        summary = {"route_accuracy": payload["metrics"]["route_accuracy"],
                   "route_lenient_accuracy": payload["metrics"]["route_lenient_accuracy"],
                   "ask_rate": payload["metrics"]["ask_rate"]}
    elif args.task == "routing":
        payload = run_routing(llm, concurrency, args.limit, rule_only=args.rule_baseline)
        print_routing(payload)
        summary = {"accuracy": payload["metrics"]["accuracy"], "macro_f1": payload["metrics"].get("macro_f1")}
    else:
        payload = run_answer(llm, args.threshold, concurrency, args.limit, judge)
        print_answer(payload)
        summary = {
            "tool_score": payload["metrics"]["tool_score"],
            "answer_score": payload["metrics"]["answer_score"],
        }

    payload["config"] = {
        "backend": backend,
        "model": getattr(llm, "model_name", None) if llm else None,
        "threshold": args.threshold,
        "judge": backend_of(judge) if judge else None,
        "note": args.note,
        "round": args.round,
        "target": args.target,
        "why": args.why,
        "what": args.what,
        "memo": args.memo,
    }

    name = args.name or f"{args.task}_{backend}"
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{name}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n원본 → {out.relative_to(BASE)}")

    record.append(
        f"eval_{args.task}",
        payload["metrics"]["n"],
        detail=args.note or name,
        backend=backend,
        model=getattr(llm, "model_name", None) if llm else None,
        threshold=args.threshold,
        judge=backend_of(judge) if judge else None,
        run=name,
        round=args.round,
        target=args.target,
        why=args.why,
        what=args.what,
        memo=args.memo,
        **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in summary.items()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
