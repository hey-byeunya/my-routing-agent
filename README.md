# 택배중계 고객응대 라우팅 에이전트

고객 문의 하나를 받아 **카테고리를 판정하고 → 그 카테고리의 근거만 모아 → 필요하면 조회 도구를 부르고 → 근거만으로 답하고 → 근거 없는 수치가 섞였는지 검사한다.** 확신이 서지 않으면 답을 지어내지 않고 담당자에게 넘긴다.

```mermaid
graph TD;
	__start__([start]) --> classify
	classify --> assemble_context
	assemble_context -. "확신도 < τ" .-> escalate
	assemble_context -. "≥ τ" .-> plan
	plan -. "도구 있음" .-> tools
	plan -. "도구 없음" .-> answer
	escalate --> tools
	tools --> answer
	answer --> verify
	verify --> __end__([end])
```

구조도는 코드에서 직접 뽑는다: `python -m src.agent --mermaid x`

> REPORT.md 는 **무엇을 왜 그렇게 만들었고 수치가 얼마인지**를 적는다. 이 문서는 **어떻게 돌리는지**만 적는다.

---

## 사전 요구사항

| 항목 | 실측값 | 비고 |
| --- | --- | --- |
| Python | 3.13.14 | 3.11 이상이면 된다 (`X \| None` 표기 사용) |
| 유료 키 | 없어도 된다 | `claude` / `opencode` / `ollama` 는 키를 쓰지 않는다 |
| 디스크 | 수십 MB | `runs/cache/` 에 LLM 응답을 캐시한다 |

## 설치부터 첫 실행까지

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 쓸 백엔드에 맞춰 채운다 (아래 표)
python scripts/check_context.py # LLM 없이 도는 점검 ①
python -m src.agent --dry-run "제주도인데 배송비 더 붙나요?"
```

`--dry-run` 은 LLM 을 부르지 않고 **어떤 카테고리로 가서 어떤 근거 절이 프롬프트에 들어가는지**까지만 보여 준다. 키가 하나도 없어도 여기까지는 반드시 돈다. 기록(`store/metrics.jsonl`)도 남기지 않는다 — 구경용 실행이 수치에 섞이면 안 되기 때문이다.

## 백엔드 네 갈래 + 재생 모드

`AGENT_BACKEND` 하나로 갈아끼운다. 코드는 어느 백엔드인지 모르는 채로 돈다.

| 백엔드 | 키 | 실측 속도 | 설정 |
| --- | --- | --- | --- |
| `openai` | **필요** (`OPENAI_API_KEY`) | 가장 빠름 | 기본값. 기본 모델 `gpt-4o-mini` |
| `claude` | 불필요 (구독 로그인) | 약 10초/건 | `claude --version` 으로 설치 확인. 기본 모델 `haiku` |
| `opencode` | 불필요 | 약 25초/건 | 무료 모델 `opencode/nemotron-3.5-lightning-free`, 비용 0 |
| `ollama` | 불필요 (로컬) | 9B 로 26.6초/건 (실측) | `ollama list` 로 서버 확인. **3B 는 구조화 출력을 못 낸다** — `AGENT_MODEL_OLLAMA=qwen3.5:9b` 처럼 9B 이상을 쓴다 |
| `replay` | 불필요 | 즉시 | `data/demo_cache.json` 에 녹화된 응답만 돌려준다 |

**키가 하나도 없을 때** — `replay` 로 돌린다. 녹화된 응답을 프롬프트 해시로 되돌려 주는 모드이고, **진짜 모델 호출이 아니다.** 녹화에 없는 질문에는 답하지 못한다. replay 로 돌았다는 사실은 로그·화면·`store/metrics.jsonl` 에 모두 드러난다 — 조용히 다른 모드로 돌지 않는다.

녹화본(`data/demo_cache.json`)은 **정식 런을 녹음해서** 만든다. 사람이 쓴 모범답안이 아니라 실제로 일어난 호출의 사본이다.

```bash
AGENT_RECORD_REPLAY=1 python -m src.agent --backend claude "편의점택배 접수하면 언제 수거해 가나요?"
python -m src.agent --backend replay "편의점택배 접수하면 언제 수거해 가나요?"   # 키 없이 같은 경로로
```

녹음은 CLI 백엔드(`claude`·`opencode`)에서만 걸린다. `openai`·`ollama` 는 CLI 를 지나지 않기 때문이다.

요청한 백엔드를 쓸 수 없으면 `replay` 로 떨어지고, 그 사유를 한 줄 출력한다. 파이프라인 안에서도 마찬가지다: 판정이 실패하면 규칙 기반으로, 답변이 실패하면 이관 문구로 떨어지되 **폴백으로 돌았다는 사실을 결과에 남긴다**(`fallback`, `fallback_error`).

## 점검 명령

앞 단계가 통과해야 뒤 수치를 믿는다.

```bash
python scripts/check_context.py          # ① 카테고리별 근거 절 매핑·크기·겹침
python scripts/check_mockdb.py           # ② 조회 도구가 무엇을 돌려주는가 (이름 해석·구간·할증)
python scripts/check_grader.py           # ③ 모범 답안이 채점기에서 전부 만점인가
python -m src.agent --dry-run "문의"      # ④ LLM 없이 앞단
python -m src.llm_backends --smoke --backend all   # ⑤ 백엔드가 같은 구조로 답하는가
```

①~③ 이 통과하기 전의 성능 수치는 믿지 않는다. ③ 이 떨어지면 에이전트가 아니라 **채점기나 정답셋이 틀린 것이다.**

## 평가

```bash
python -m src.evaluate --task routing --backend claude --name routing_v3 \
    --round "#3" --target "prompts.py route_guide" \
    --why "후속 턴이 앞 대화를 못 봐서 오분류" --what "classify 에 history 주입" \
    --memo "C-011#6 해결, C-003#2 남음"

python -m src.evaluate --task answer --backend claude --judge-backend claude --name answer_v3
python scripts/report_table.py           # 회차별 종합 기록표 (REPORT.md 에 붙인다)
```

`--round` 부터의 다섯 옵션은 **왜·무엇을 바꿨는지를 수치와 같은 줄에 남긴다.** 나중에 손으로 표를 쓰면 수치와 설명이 따로 놀기 때문에, `store/metrics.jsonl` 한 벌만 진실로 둔다.

## 데모

```bash
streamlit run app.py
```

사이드바에서 백엔드·모델·확신도 임계값 τ 를 고른다. 본문은 답변만 보여 주지 않고 **프롬프트에 실제로 들어간 근거 절, 호출한 도구와 인자, 도구 결과, 가드레일 판정**을 함께 펼친다. 폴백으로 돌았으면 상단에 배지가 뜬다.

## 자주 막히는 지점

| 증상 | 1순위 원인 |
| --- | --- |
| `claude 오류: You've hit your session limit` | 구독 사용량 한도. 시간이 지나야 풀린다. 그 사이 수치는 **폴백에 오염되므로 무효 처리**한다 |
| 분류가 전부 한 카테고리로 쏠린다 | 작은 로컬 모델이 구조화 출력에 실패해 규칙 폴백으로 떨어진 것. 화면의 `⚠️ 폴백` 배지를 본다 |
| 도구 호출이 0건 | 카테고리가 틀렸을 가능성이 먼저다. 카테고리마다 부를 수 있는 도구가 다르다 (`tool_menu`) |
| `opencode` 가 멈춰 있다 | 첫 줄 시한 90초. 전역 SQLite 를 공유하므로 동시 실행을 2 이하로 둔다 |
| replay 가 답하지 못한다 | 녹화에 없는 질문이다. 녹화는 정식 런에서만 쌓인다 |
| Streamlit 화면이 비어 있다 | 실행 버튼을 누르기 전에는 입력만 보인다 (`st.stop()`) |

## 구조 한눈에

| 파일 | 하는 일 |
| --- | --- |
| `src/llm_backends.py` | 백엔드 5종을 하나의 인터페이스로. CLI 백엔드에 구조화 출력을 얹는다 |
| `src/llm_cache.py` | 프롬프트 해시 파일 캐시. 재채점 때 API 를 다시 부르지 않는다 |
| `src/prompts.py` | 분류 지침·예시, 계획/답변 프롬프트, 규칙 기반 폴백 분류 |
| `src/context.py` | 매뉴얼을 절 단위로 쪼개고 카테고리별로 고를 절을 정한다 |
| `src/mockdb.py` · `src/tools.py` | 조회 로직과 그 LangChain 도구 래퍼 |
| `src/agent.py` | LangGraph 파이프라인 6단계 + CLI |
| `src/guardrail.py` | 근거에 없는 수치가 답변에 섞였는지 기계적으로 검사 |
| `src/grader.py` · `src/evaluate.py` | 두 지표 채점과 평가 실행기 |
| `src/record.py` | `store/metrics.jsonl` 에 1줄 1레코드로 덧붙인다 |
| `scripts/check_*.py` | LLM 없이 도는 픽스처 검증 (근거 매핑 · 조회 도구 · 채점기) |
| `scripts/report_table.py` | 그 기록에서 회차별 종합 기록표를 뽑는다 |
| `app.py` | Streamlit 데모 |
