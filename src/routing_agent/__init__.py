"""택배중계 고객응대 라우팅 에이전트.

문의 하나를 받아 카테고리를 판정하고, 그 카테고리의 근거만 모아, 필요하면
조회 도구를 부르고, 근거만으로 답하고, 근거 없는 수치가 섞였는지 검사한다.

이 패키지는 **import 되는 코드**만 담는다. 사람이 돌리는 일회성 작업(검증·데이터
준비·리포트 생성)은 저장소 루트의 `scripts/` 에 있다.
"""

__all__ = ["agent", "context", "evaluate", "grader", "guardrail", "llm_backends",
           "mockdb", "prompts", "record", "schemas", "tools"]
