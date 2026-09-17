"""LLM 백엔드 다섯 갈래를 하나의 인터페이스로 묶는다.

    openai    ChatOpenAI            네이티브. 기본값이자 비교 기준선
    claude    claude -p             구독 인증. API 크레딧을 쓰지 않는다
    opencode  opencode run          무료 모델. 비용 0
    ollama    ChatOpenAI(base_url)  로컬. 오프라인
    replay    data/demo_cache.json  키 0개로 도는 채점자용 재생 모드

claude·opencode 는 subprocess 로 텍스트만 주고받는다. 네이티브 function calling 이
없어서 `with_structured_output()` 이 그냥은 안 된다. langchain-core 1.6.3 의
`with_structured_output` 은 `bind_tools` 를 재정의했을 때만 동작하므로
(재정의가 없으면 NotImplementedError), `bind_tools` 를 재정의해 스키마를
프롬프트에 주입하고 응답 JSON 을 AIMessage.tool_calls 로 조립한다.
그러면 호출부는 어느 백엔드인지 모르는 채로 돌고, 수치가 서로 비교 가능해진다.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.utils.json import parse_json_markdown

BASE = Path(__file__).resolve().parent.parent
BACKENDS = ("openai", "claude", "opencode", "ollama", "replay")

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "claude": "haiku",
    "opencode": "opencode/nemotron-3.5-lightning-free",
    "ollama": "qwen2.5:3b",
    "replay": "recorded",
}

# 폭주 방지 (REPORT 의 표와 같은 값). 근거는 README·REPORT 참조.
CLAUDE_TIMEOUT_S = 120
OPENCODE_TIMEOUT_S = 300
OPENCODE_FIRST_LINE_S = 90

CLAUDE_BLOCKED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit"


class BackendError(RuntimeError):
    """백엔드 호출이 끝내 실패했다. 호출부가 폴백으로 넘어갈 신호."""


# ---------------------------------------------------------------- 프롬프트 평탄화


def flatten(messages: Sequence[BaseMessage]) -> str:
    """대화 메시지를 CLI 에 넘길 한 덩어리 텍스트로 만든다."""
    parts: list[str] = []
    for message in messages:
        text = getattr(message, "text", None) or str(message.content)
        if not text.strip():
            continue
        role = {"system": "[지침]", "human": "[사용자]", "ai": "[assistant]", "tool": "[도구 결과]"}.get(
            message.type, f"[{message.type}]"
        )
        parts.append(f"{role}\n{text.strip()}")
    return "\n\n".join(parts)


def _schema_instruction(specs: list[dict]) -> str:
    """스키마를 프롬프트로 주입한다. 네이티브 tool calling 자리를 대신한다."""
    if len(specs) == 1:
        fn = specs[0]["function"]
        schema = json.dumps(fn.get("parameters", {}), ensure_ascii=False)
        return (
            "\n\n[출력 형식]\n"
            f"아래 JSON 스키마를 만족하는 JSON 객체 **하나만** 출력해라.\n"
            f"설명·인사말·코드펜스 밖의 어떤 텍스트도 덧붙이지 마라.\n"
            f"스키마: {schema}"
        )
    names = ", ".join(s["function"]["name"] for s in specs)
    listed = json.dumps(specs, ensure_ascii=False)
    return (
        "\n\n[출력 형식]\n"
        f'{{"name": "<{names} 중 하나>", "args": {{...}}}} 형태의 JSON 객체 하나만 출력해라.\n'
        f"도구 정의: {listed}"
    )


# ---------------------------------------------------------------- subprocess 실행


def _run_lines(
    cmd: list[str],
    env: dict[str, str],
    cwd: str | None,
    total_timeout: int,
    first_line_timeout: int | None = None,
) -> tuple[list[str], str, int]:
    """줄 단위로 읽으면서 두 가지 시한을 잰다 — 첫 줄까지, 그리고 전체.

    stdin 은 반드시 닫아 준다. 열어 두면 opencode 부트스트랩이 거기서 멈춰 선다.
    """
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    collected: list[str] = []
    import time

    deadline = time.monotonic() + total_timeout
    first_deadline = time.monotonic() + (first_line_timeout or total_timeout)
    while True:
        budget = (first_deadline if not collected else deadline) - time.monotonic()
        if budget <= 0:
            proc.kill()
            which = "첫 줄" if not collected else "전체"
            raise BackendError(f"{cmd[0]} {which} 시한 초과 — {total_timeout}초 안에 끝나지 않았다")
        try:
            line = lines.get(timeout=min(budget, 1.0))
        except queue.Empty:
            if proc.poll() is not None and lines.empty():
                break
            continue
        if line is None:
            break
        if line.strip():
            collected.append(line)

    proc.wait(timeout=10)
    stderr = proc.stderr.read() if proc.stderr else ""
    return collected, stderr, proc.returncode


# ---------------------------------------------------------------- 공통 베이스


class SubprocessChatModel(BaseChatModel):
    """CLI 를 텍스트 왕복으로 쓰는 백엔드의 공통 뼈대.

    자식 클래스는 `_call_cli(prompt) -> (텍스트, 메타)` 하나만 구현한다.
    """

    model_name: str = ""
    temperature: float = 0.0

    @property
    def _identifying_params(self) -> dict[str, Any]:
        # 기본값(lc_attributes)은 거의 비어 있어 백엔드끼리 캐시 키가 겹친다.
        return {"backend": self._llm_type, "model": self.model_name, "temperature": self.temperature}

    def _call_cli(self, prompt: str) -> tuple[str, dict]:  # pragma: no cover - 자식이 구현
        raise NotImplementedError

    def bind_tools(
        self,
        tools: Sequence[dict | type | Callable | Any],
        *,
        tool_choice: Any | None = None,
        **kwargs: Any,
    ) -> Runnable:
        """스키마를 프롬프트로 주입한다. 이걸 재정의해야 with_structured_output 이 산다.

        `**kwargs` 는 반드시 받아 넘긴다 — with_structured_output 이
        `ls_structured_output_format` 을 항상 끼워 넣는다.
        """
        specs = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(_tool_specs=specs, _tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        specs: list[dict] | None = kwargs.get("_tool_specs")
        prompt = flatten(messages)
        if specs:
            prompt += _schema_instruction(specs)

        text, meta = self._call_cli(prompt)

        if not specs:
            message = AIMessage(content=text, response_metadata=meta)
            return ChatResult(generations=[ChatGeneration(message=message)])

        parsed = _parse_json(text)
        if len(specs) == 1:
            name, args = specs[0]["function"]["name"], parsed
        else:
            name = parsed.get("name", specs[0]["function"]["name"])
            args = parsed.get("args", {})
        if not isinstance(args, dict):
            raise BackendError(f"구조화 출력이 객체가 아니다: {type(args).__name__}")

        message = AIMessage(
            content="",
            # id 키는 필수이고 값이 문자열이어야 한다. None 이면 ToolMessage 검증이 깨진다.
            tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}],
            response_metadata=meta,
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _parse_json(text: str) -> Any:
    """코드펜스·잡설을 걷어내고 JSON 을 꺼낸다."""
    try:
        return parse_json_markdown(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise BackendError(f"JSON 을 찾지 못했다: {text[:200]!r}")


# ---------------------------------------------------------------- claude 구독


_claude_cwd: str | None = None


def _isolated_cwd() -> str:
    """빈 디렉터리에서 돌린다. 프로젝트 CLAUDE.md 같은 컨텍스트를 안 읽게 해 호출이 짧아진다."""
    global _claude_cwd
    if _claude_cwd is None:
        _claude_cwd = tempfile.mkdtemp(prefix="routing-agent-claude-")
    return _claude_cwd


class ClaudeCliChat(SubprocessChatModel):
    """`claude -p`. 구독 로그인을 그대로 쓰므로 API 키가 필요 없다."""

    model_name: str = DEFAULT_MODELS["claude"]
    timeout_s: int = CLAUDE_TIMEOUT_S

    @property
    def _llm_type(self) -> str:
        return "claude-cli"

    def _call_cli(self, prompt: str) -> tuple[str, dict]:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--model",
            self.model_name,
            "--output-format",
            "json",
            "--disallowed-tools",
            CLAUDE_BLOCKED_TOOLS,
        ]
        lines, stderr, code = _run_lines(cmd, dict(os.environ), _isolated_cwd(), self.timeout_s)
        raw = "\n".join(lines)
        if code != 0 and not raw.strip():
            raise BackendError(f"claude 종료 코드 {code}: {stderr[:300]}")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackendError(f"claude 출력이 JSON 이 아니다: {raw[:200]!r}") from exc
        if payload.get("is_error"):
            raise BackendError(f"claude 오류: {str(payload.get('result'))[:300]}")
        usage = payload.get("usage") or {}
        meta = {
            "backend": "claude",
            "model": self.model_name,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            # 구독으로 돌면 API 크레딧이 차감되지 않는다. 정가 환산값이라 비용으로 읽지 않는다.
            "list_cost_usd": payload.get("total_cost_usd", 0.0),
        }
        return str(payload.get("result", "")), meta


# ---------------------------------------------------------------- opencode 무료


_opencode_dbs: dict[int, str] = {}
_opencode_lock = threading.Lock()


def _opencode_db() -> str:
    """스레드마다 DB 를 가른다. 안 가르면 전역 SQLite 와 파일 락에서 경합해 수 분씩 잔다."""
    tid = threading.get_ident()
    with _opencode_lock:
        if tid not in _opencode_dbs:
            _opencode_dbs[tid] = str(Path(tempfile.mkdtemp(prefix="routing-agent-oc-")) / "opencode.db")
        return _opencode_dbs[tid]


class OpencodeCliChat(SubprocessChatModel):
    """`opencode run`. 무료 모델로 비용 0."""

    model_name: str = DEFAULT_MODELS["opencode"]
    timeout_s: int = OPENCODE_TIMEOUT_S
    first_line_s: int = OPENCODE_FIRST_LINE_S

    @property
    def _llm_type(self) -> str:
        return "opencode-cli"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                # 사용자 전역 설정을 계속 읽기 때문에, 켜 둔 MCP 서버가 호출마다 뜬다.
                "OPENCODE_PURE": "1",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_DB": _opencode_db(),
                "OPENCODE_PERMISSION": json.dumps({"edit": "deny", "bash": "deny", "webfetch": "deny"}),
                "OPENCODE_CONFIG_CONTENT": json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "permission": {"edit": "deny", "bash": "deny", "webfetch": "deny"},
                        "mcp": {},
                    }
                ),
            }
        )
        return env

    def _call_cli(self, prompt: str) -> tuple[str, dict]:
        binary = os.environ.get("OPENCODE_BIN", "opencode")
        cmd = [binary, "run", prompt, "--format", "json", "-m", self.model_name]
        lines, stderr, code = _run_lines(
            cmd, self._env(), _isolated_cwd(), self.timeout_s, self.first_line_s
        )

        text = ""
        errors: list[str] = []
        meta = {"backend": "opencode", "model": self.model_name, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # 모르는 줄은 흘린다. 버전이 올라 이벤트가 늘어도 깨지지 않게.
            part = event.get("part") or {}
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                text = part["text"]
            elif event.get("type") == "step_finish":
                tokens = part.get("tokens") or {}
                meta["input_tokens"] += tokens.get("input", 0)
                meta["output_tokens"] += tokens.get("output", 0)
                meta["cost_usd"] += part.get("cost", 0) or 0
            elif event.get("type") == "error":
                err = event.get("error") or {}
                data = err.get("data") or {}
                errors.append(f"{err.get('name', 'error')}: {data.get('message', '')}"[:300])

        if not text.strip():
            detail = " / ".join(errors) or stderr[:300] or f"종료 코드 {code}"
            raise BackendError(f"opencode 가 텍스트를 내놓지 않았다 — {detail}")
        return text, meta


# ---------------------------------------------------------------- replay (키 0개)


class ReplayChat(SubprocessChatModel):
    """녹화된 응답을 프롬프트 해시로 되돌려 준다. 채점자가 키 없이 전체를 돌려볼 수 있게.

    진짜 모델이 아니다. 로그·화면·metrics 에 replay 임을 드러낸다.
    """

    model_name: str = DEFAULT_MODELS["replay"]
    cache_path: str = str(BASE / "data" / "demo_cache.json")

    @property
    def _llm_type(self) -> str:
        return "replay"

    def _table(self) -> dict[str, str]:
        path = Path(self.cache_path)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8")).get("responses", {})

    def _call_cli(self, prompt: str) -> tuple[str, dict]:
        import hashlib

        key = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        table = self._table()
        if key not in table:
            raise BackendError(
                f"녹화되지 않은 질문이다 (키 {key}). replay 는 data/demo_cache.json 에 있는 것만 답한다"
            )
        return table[key], {"backend": "replay", "model": "recorded", "replayed": True}


def record_replay(prompt: str, response: str, path: Path | None = None) -> None:
    """정식 런의 응답을 replay 캐시에 적어 둔다."""
    import hashlib

    path = path or (BASE / "data" / "demo_cache.json")
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "$comment": "replay 백엔드용 녹화 응답. 키 없는 환경에서 데모·평가를 돌리기 위한 것이며 진짜 모델 호출이 아니다.",
        "responses": {},
    }
    payload["responses"][hashlib.sha256(prompt.encode()).hexdigest()[:16]] = response
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 팩토리


def available() -> dict[str, str]:
    """각 백엔드가 지금 쓸 수 있는지. 못 쓰면 그 이유를 한 줄로."""
    status = {}
    status["openai"] = "" if os.environ.get("OPENAI_API_KEY") else "OPENAI_API_KEY 없음"
    status["claude"] = "" if shutil.which("claude") else "claude CLI 없음"
    status["opencode"] = "" if shutil.which(os.environ.get("OPENCODE_BIN", "opencode")) else "opencode CLI 없음"
    status["ollama"] = "" if shutil.which("ollama") else "ollama 없음"
    cache = BASE / "data" / "demo_cache.json"
    status["replay"] = "" if cache.exists() else "data/demo_cache.json 없음"
    return status


def resolve_backend(requested: str | None = None) -> tuple[str, str]:
    """쓸 백엔드를 정하고, 요청과 달라졌으면 그 사유를 함께 돌려준다."""
    wanted = (requested or os.environ.get("AGENT_BACKEND") or "openai").strip().lower()
    if wanted not in BACKENDS:
        raise ValueError(f"모르는 백엔드: {wanted}. 가능한 값: {', '.join(BACKENDS)}")
    status = available()
    if not status[wanted]:
        return wanted, ""
    if not status["replay"]:
        return "replay", f"{wanted} 를 쓸 수 없어 replay 로 돌린다 ({status[wanted]})"
    raise BackendError(f"{wanted} 를 쓸 수 없고 replay 캐시도 없다 ({status[wanted]})")


def make_llm(backend: str | None = None, model: str | None = None, *, quiet: bool = False) -> BaseChatModel:
    """어느 백엔드든 같은 인터페이스로 돌려준다."""
    from src import llm_cache

    llm_cache.install()

    name, note = resolve_backend(backend)
    if note and not quiet:
        print(f"  [백엔드] {note}", file=sys.stderr)
    model = model or os.environ.get("AGENT_MODEL") or DEFAULT_MODELS[name]

    if name == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0, timeout=60, max_retries=1)
    if name == "ollama":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",
            temperature=0,
            timeout=180,
            max_retries=1,
        )
    if name == "claude":
        return ClaudeCliChat(model_name=model)
    if name == "opencode":
        return OpencodeCliChat(model_name=model)
    return ReplayChat(model_name=model)


def backend_of(llm: BaseChatModel) -> str:
    """리포트·metrics 에 적을 백엔드 이름."""
    mapping = {"claude-cli": "claude", "opencode-cli": "opencode", "replay": "replay"}
    llm_type = getattr(llm, "_llm_type", "")
    if llm_type in mapping:
        return mapping[llm_type]
    base_url = str(getattr(llm, "openai_api_base", "") or "")
    return "ollama" if "11434" in base_url else "openai"


# ---------------------------------------------------------------- 스모크


def _smoke(backends: Iterable[str]) -> int:
    """같은 질문에 백엔드들이 같은 구조로 답하는지 본다."""
    import time

    from src.schemas import RouteDecision

    question = "롯데택배로 방문택배 보내려는데 제주도면 배송비가 더 붙나요?"
    guide = (
        "너는 택배중계서비스 상담 문의를 분류한다. "
        "RESERVE_GENERAL(예약 일반) / VISIT_PICKUP(방문택배) / CVS_PICKUP(편의점택배) / "
        "BIZ_BULK(사업자·대량) / SPEC_SHIPPING(규격·배송·취소) / OTHER(범위 밖) 중 하나를 고른다."
    )
    failed = 0
    status = available()
    for name in backends:
        if status[name]:
            print(f"{name:<10} 건너뜀 — {status[name]}")
            continue
        started = time.monotonic()
        try:
            llm = make_llm(name, quiet=True).with_structured_output(RouteDecision)
            result = llm.invoke(f"{guide}\n\n고객 문의: \"{question}\"")
            elapsed = time.monotonic() - started
            print(f"{name:<10} {elapsed:6.1f}초  route={result.route:<16} conf={result.confidence:.2f}  {result.reason[:40]}")
        except Exception as exc:  # noqa: BLE001 - 스모크는 모든 실패를 보여 주는 게 목적이다
            failed += 1
            print(f"{name:<10} {time.monotonic() - started:6.1f}초  실패 — {type(exc).__name__}: {str(exc)[:160]}")
    return 1 if failed else 0


def _main() -> int:
    import argparse

    from dotenv import load_dotenv

    load_dotenv(BASE / ".env")

    parser = argparse.ArgumentParser(description="LLM 백엔드 점검")
    parser.add_argument("--smoke", action="store_true", help="같은 질문을 백엔드마다 돌려 본다")
    parser.add_argument("--backend", default="all", help="all 또는 " + ", ".join(BACKENDS))
    args = parser.parse_args()

    if not args.smoke:
        print(f"{'백엔드':<10} {'기본 모델':<38} 상태")
        for name in BACKENDS:
            reason = available()[name]
            print(f"{name:<10} {DEFAULT_MODELS[name]:<38} {reason or '사용 가능'}")
        return 0

    targets = BACKENDS if args.backend == "all" else tuple(args.backend.split(","))
    return _smoke(targets)


if __name__ == "__main__":
    raise SystemExit(_main())
