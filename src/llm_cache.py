"""디스크에 남는 LLM 캐시.

langchain 1.4 에는 InMemoryCache 만 있다 (SQLiteCache 는 langchain-classic 으로
빠졌다). 프로세스가 끝나면 캐시가 사라지는데, opencode 백엔드는 건당 25초라
평가를 다시 돌릴 때마다 그 시간을 다시 태우게 된다. BaseCache 의 세 메서드만
파일로 구현한다.

ChatGeneration 을 통째로 직렬화한다. text 만 저장하면 복원 시 tool_calls 가
유실되어 "실제로 호출한 도구" 측정이 깨진다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from langchain_core.caches import BaseCache
from langchain_core.load import dumps, loads
from langchain_core.outputs import ChatGeneration, Generation

BASE = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = BASE / "runs" / "cache"


class FileCache(BaseCache):
    """프롬프트+모델 식별자 해시를 파일 하나로 저장한다."""

    def __init__(self, directory: Path | str = DEFAULT_CACHE_DIR) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, prompt: str, llm_string: str) -> Path:
        digest = hashlib.sha256(f"{llm_string}\x00{prompt}".encode()).hexdigest()
        return self.directory / f"{digest}.json"

    def lookup(self, prompt: str, llm_string: str) -> list[Generation] | None:
        path = self._path(prompt, llm_string)
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            # 메시지만 저장하고 ChatGeneration 은 여기서 다시 씌운다.
            # 그래야 allowed_objects 를 메시지로 좁혀 역직렬화 범위를 제한할 수 있다.
            generations = [
                ChatGeneration(message=loads(m, allowed_objects="messages")) for m in payload["messages"]
            ]
        except Exception:
            # 캐시가 깨졌으면 없는 것으로 친다. 캐시 때문에 실행이 멈추면 안 된다.
            self.misses += 1
            return None
        self.hits += 1
        return generations

    def update(self, prompt: str, llm_string: str, return_val: list[Generation]) -> None:
        messages = [getattr(g, "message", None) for g in return_val]
        if any(m is None for m in messages):
            return  # 메시지가 없는 생성물은 캐시하지 않는다 (복원 시 tool_calls 가 사라진다)
        payload: dict[str, Any] = {
            "llm_string": llm_string,
            "prompt_head": prompt[:400],
            "messages": [dumps(m) for m in messages],
        }
        tmp = self._path(prompt, llm_string).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(prompt, llm_string))

    def clear(self, **kwargs: Any) -> None:
        for path in self.directory.glob("*.json"):
            path.unlink()

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "files": len(list(self.directory.glob("*.json")))}


_installed: FileCache | None = None


def install(directory: Path | str = DEFAULT_CACHE_DIR) -> FileCache:
    """전역 LLM 캐시로 등록한다. 커스텀 BaseChatModel 에도 자동으로 먹는다."""
    global _installed
    import warnings

    from langchain_core._api.beta_decorator import LangChainBetaWarning
    from langchain_core.globals import set_llm_cache

    # loads() 가 beta 라 캐시 적중마다 경고를 찍는다. 의도한 사용이라 여기서만 끈다.
    warnings.filterwarnings("ignore", category=LangChainBetaWarning, module="src.llm_cache")

    if _installed is None:
        _installed = FileCache(directory)
        set_llm_cache(_installed)
    return _installed
