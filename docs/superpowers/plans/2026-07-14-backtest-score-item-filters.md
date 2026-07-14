# Backtest Score and Item Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 백테스트 대상 종목을 종합점수 다중 선택과 스크리닝 항목 AND 조건으로 필터링하고, 가능한 64개 조합을 자동 검증한다.

**Architecture:** `app.py`에 API 경계 정규화 함수와 부작용 없는 후보 필터 함수를 두고 백테스트 작업이 이 함수를 공통 사용한다. 기존 단일 파일 Flask 템플릿 구조는 유지하면서 체크박스 선택을 안정 키 배열로 전송하고, `tests/test_app.py`에서 순수 함수의 전체 조합과 API·화면 연결을 검증한다.

**Tech Stack:** Python 3.11, Flask, `unittest`, 표준 라이브러리 `itertools`, HTML/CSS/JavaScript, uv, Ruff

## Global Constraints

- 종합점수 `3점`, `2점`, `1점`은 동시에 여러 개 선택할 수 있다.
- 종합점수를 하나도 선택하지 않으면 전체 `[3, 2, 1]`을 대상으로 한다.
- 화면 최초 기본 선택과 `scores` 필드 생략 시 API 기본값은 기존 동작을 보존하는 `[3, 2]`다.
- 항목을 여러 개 선택하면 선택 항목을 모두 만족해야 하고, 하나도 선택하지 않으면 항목 제한이 없다.
- API 항목 키는 `turnaround`, `supply`, `nps`이며 각각 `연간실적호전`, `순매수전환`, `국민연금 신규/추가매수`와 정확히 대응한다.
- 잘못된 필터 타입이나 값은 백그라운드 작업을 시작하지 않고 HTTP 400을 반환한다.
- 스크리닝 점수 계산, 일일 리포트 선택 규칙, DuckDB 스키마는 변경하지 않는다.
- 새로운 외부 의존성을 추가하지 않는다.
- 사용자 소유의 추적되지 않은 `get-pip.py`는 수정하거나 커밋하지 않는다.

---

### Task 1: 순수 후보 필터와 64개 조합 검증

**Files:**
- Modify: `tests/test_app.py:1-298`
- Modify: `app.py:31-37,231-254`

**Interfaces:**
- Consumes: 현재 스크리닝 행의 `종목명`, `종합점수`, 쉼표 구분 `출처` 필드
- Produces: `BACKTEST_SCORE_OPTIONS: tuple[int, ...]`, `DEFAULT_BACKTEST_SCORES: tuple[int, ...]`, `BACKTEST_ITEM_SOURCES: dict[str, str]`, `filter_backtest_candidates(results: list[dict], selected_scores: tuple[int, ...], required_items: tuple[str, ...]) -> list[dict]`

- [ ] **Step 1: 64개 조합을 모두 확인하는 실패 테스트 작성**

`tests/test_app.py`에 `itertools.combinations`를 import하고 다음 테스트 클래스를 추가한다.

```python
from itertools import combinations


class BacktestFilterTest(unittest.TestCase):
    @staticmethod
    def all_subsets(values):
        return [
            subset
            for size in range(len(values) + 1)
            for subset in combinations(values, size)
        ]

    def test_all_64_score_and_item_filter_combinations(self):
        item_keys = tuple(app_module.BACKTEST_ITEM_SOURCES)
        item_sources = tuple(app_module.BACKTEST_ITEM_SOURCES.values())
        rows = []
        for index, sources in enumerate(self.all_subsets(item_sources)[1:], start=1):
            rows.append(
                {
                    "종목명": f"종목{index}",
                    "종합점수": len(sources),
                    "출처": ", ".join(sources),
                }
            )

        case_count = 0
        for selected_scores in self.all_subsets(app_module.BACKTEST_SCORE_OPTIONS):
            for required_items in self.all_subsets(item_keys):
                effective_scores = set(
                    selected_scores or app_module.BACKTEST_SCORE_OPTIONS
                )
                required_sources = {
                    app_module.BACKTEST_ITEM_SOURCES[key] for key in required_items
                }
                expected = [
                    row
                    for row in rows
                    if row["종합점수"] in effective_scores
                    and required_sources.issubset(
                        {source.strip() for source in row["출처"].split(",")}
                    )
                ]

                with self.subTest(
                    selected_scores=selected_scores,
                    required_items=required_items,
                ):
                    self.assertEqual(
                        app_module.filter_backtest_candidates(
                            rows, selected_scores, required_items
                        ),
                        expected,
                    )
                case_count += 1

        self.assertEqual(case_count, 64)
```

- [ ] **Step 2: 테스트를 실행해 올바른 이유로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.BacktestFilterTest.test_all_64_score_and_item_filter_combinations -v`

Expected: `AttributeError: module 'app' has no attribute 'BACKTEST_ITEM_SOURCES'`

- [ ] **Step 3: 최소 상수와 순수 필터 함수 구현**

`app.py`의 전역 설정과 백테스트 섹션에 다음 계약을 구현한다.

```python
BACKTEST_SCORE_OPTIONS = (3, 2, 1)
DEFAULT_BACKTEST_SCORES = (3, 2)
BACKTEST_ITEM_SOURCES = {
    "turnaround": "연간실적호전",
    "supply": "순매수전환",
    "nps": "국민연금 신규/추가매수",
}


def filter_backtest_candidates(results, selected_scores, required_items):
    effective_scores = set(selected_scores or BACKTEST_SCORE_OPTIONS)
    required_sources = {
        BACKTEST_ITEM_SOURCES[item_key] for item_key in required_items
    }
    filtered = []
    for result in results:
        sources = {
            source.strip()
            for source in str(result.get("출처", "")).split(",")
            if source.strip()
        }
        if (
            result.get("종합점수") in effective_scores
            and required_sources.issubset(sources)
        ):
            filtered.append(result)
    return filtered
```

- [ ] **Step 4: 64개 조합 테스트가 통과하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.BacktestFilterTest.test_all_64_score_and_item_filter_combinations -v`

Expected: `Ran 1 test`와 `OK`; 내부 `case_count`는 정확히 `64`

- [ ] **Step 5: Task 1 변경을 Lore 형식으로 커밋**

```bash
git add app.py tests/test_app.py
git commit -m "Make backtest candidate rules deterministic" \
  -m "Constraint: Empty score selections mean all scores and selected item filters combine with AND
Rejected: Substring source matching | it can produce false positives for similarly named sources
Confidence: high
Scope-risk: narrow
Tested: All 64 score and item subset combinations"
```

### Task 2: API 검증과 백테스트 작업 연결

**Files:**
- Modify: `tests/test_app.py:22-99`
- Modify: `app.py:231-383,401-461`

**Interfaces:**
- Consumes: `POST /api/backtest/run`의 선택적 `scores: list[int]`, `items: list[str]`
- Produces: `normalize_backtest_filters(params: dict) -> tuple[tuple[int, ...], tuple[str, ...]]`; 확장된 `run_backtest_task(period_months, initial_capital, strategy, slippage_pct, commission_pct, tax_pct, score_filters, item_filters)`

- [ ] **Step 1: API 기본값·정규화·오류·빈 후보 실패 테스트 작성**

`FlaskApiTest`에 다음 테스트를 추가한다.

```python
    def test_backtest_api_passes_default_filters_to_worker(self):
        with patch.object(app_module.threading, "Thread") as thread:
            response = self.client.post("/api/backtest/run", json={})

        self.assertEqual(response.status_code, 200)
        args = thread.call_args.kwargs["args"]
        self.assertEqual(args[-2:], ((3, 2), ()))
        thread.return_value.start.assert_called_once_with()

    def test_backtest_api_normalizes_selected_filters(self):
        with patch.object(app_module.threading, "Thread") as thread:
            response = self.client.post(
                "/api/backtest/run",
                json={
                    "scores": [1, 3, 3],
                    "items": ["nps", "turnaround", "nps"],
                },
            )

        self.assertEqual(response.status_code, 200)
        args = thread.call_args.kwargs["args"]
        self.assertEqual(args[-2:], ((3, 1), ("turnaround", "nps")))

    def test_backtest_api_rejects_invalid_filters_before_starting_worker(self):
        invalid_requests = (
            {"scores": "3"},
            {"scores": [True]},
            {"scores": [0]},
            {"scores": [4]},
            {"items": "nps"},
            {"items": [1]},
            {"items": ["unknown"]},
        )
        for payload in invalid_requests:
            with self.subTest(payload=payload):
                with patch.object(app_module.threading, "Thread") as thread:
                    response = self.client.post("/api/backtest/run", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.get_json())
                thread.assert_not_called()

    def test_backtest_task_reports_when_filters_match_no_stocks(self):
        with app_module.data_lock:
            app_module.current_data["result"] = [
                {"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}
            ]

        app_module.run_backtest_task(
            6,
            100_000_000,
            "equal_weight",
            score_filters=(3,),
            item_filters=("nps",),
        )

        self.assertEqual(app_module.backtest_state["status"], "error")
        self.assertEqual(
            app_module.backtest_state["error_msg"],
            "선택한 필터 조건에 맞는 종목이 없습니다.",
        )
```

- [ ] **Step 2: 새 API 테스트를 실행해 필터가 아직 전달·검증되지 않아 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_api_passes_default_filters_to_worker tests.test_app.FlaskApiTest.test_backtest_api_normalizes_selected_filters tests.test_app.FlaskApiTest.test_backtest_api_rejects_invalid_filters_before_starting_worker tests.test_app.FlaskApiTest.test_backtest_task_reports_when_filters_match_no_stocks -v`

Expected: 스레드 인자에 필터가 없거나 잘못된 요청이 200을 반환하고, 빈 후보 메시지가 기존 `2점 이상 종목이 없습니다`여서 FAIL

- [ ] **Step 3: API 필터 정규화와 작업 전달 구현**

`app.py`에 다음 정규화 함수를 추가하고 `api_backtest_run()`에서 숫자 검증 뒤 호출한다.

```python
def normalize_backtest_filters(params):
    raw_scores = params.get("scores", list(DEFAULT_BACKTEST_SCORES))
    raw_items = params.get("items", [])
    if not isinstance(raw_scores, list):
        raise ValueError("종합점수 필터는 배열이어야 합니다.")
    if any(type(score) is not int or score not in BACKTEST_SCORE_OPTIONS
           for score in raw_scores):
        raise ValueError("종합점수는 1, 2, 3만 선택할 수 있습니다.")
    if not isinstance(raw_items, list):
        raise ValueError("항목별 필터는 배열이어야 합니다.")
    if any(type(item) is not str or item not in BACKTEST_ITEM_SOURCES
           for item in raw_items):
        raise ValueError("지원하지 않는 항목별 필터입니다.")
    scores = tuple(score for score in BACKTEST_SCORE_OPTIONS if score in raw_scores)
    items = tuple(key for key in BACKTEST_ITEM_SOURCES if key in raw_items)
    return scores, items
```

`run_backtest_task()`의 기본 필터는 `DEFAULT_BACKTEST_SCORES`, 빈 tuple로 유지해 기존 직접 호출도 보존한다. 작업 시작부의 `high_score` 계산을 `filter_backtest_candidates()`로 교체하고 빈 결과 메시지를 `선택한 필터 조건에 맞는 종목이 없습니다.`로 바꾼다. API가 생성하는 스레드 인자 끝에 `score_filters`, `item_filters`를 추가한다.

결과 설정에는 사용자가 실행 결과를 재현할 수 있도록 다음 값을 기록한다.

```python
"score_filters": list(score_filters or BACKTEST_SCORE_OPTIONS),
"item_filters": list(item_filters),
"item_filter_labels": [BACKTEST_ITEM_SOURCES[key] for key in item_filters],
```

- [ ] **Step 4: API·작업 테스트와 기존 Flask API 테스트를 실행해 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest -v`

Expected: 모든 `FlaskApiTest`가 `OK`

- [ ] **Step 5: Task 2 변경을 Lore 형식으로 커밋**

```bash
git add app.py tests/test_app.py
git commit -m "Enforce one backtest filter contract at the API boundary" \
  -m "Constraint: Omitted scores preserve the legacy 2+ behavior while an explicit empty list means all scores
Rejected: UI-only validation | API clients must receive identical behavior
Confidence: high
Scope-risk: moderate
Directive: Keep stable item keys separate from Korean source labels
Tested: Flask API defaults, normalization, invalid inputs, and empty candidate handling"
```

### Task 3: 백테스트 화면, README, 전체 검증 및 게시

**Files:**
- Modify: `tests/test_app.py:22-99`
- Modify: `app.py:950-1200`
- Modify: `README.md:31-40,138-157`

**Interfaces:**
- Consumes: 체크된 `input[name="scoreFilter"]`, `input[name="itemFilter"]`
- Produces: `/api/backtest/run` 요청의 `scores: number[]`, `items: string[]`; 사용자 문서의 필터 기본값과 AND 의미

- [ ] **Step 1: 화면 컨트롤과 JSON 전송 실패 테스트 작성**

`FlaskApiTest`에 다음 템플릿 테스트를 추가한다.

```python
    def test_backtest_page_exposes_score_and_item_filters(self):
        template = app_module.BACKTEST_TEMPLATE

        self.assertEqual(template.count('name="scoreFilter"'), 3)
        self.assertEqual(template.count('name="itemFilter"'), 3)
        self.assertIn('name="scoreFilter" value="3" checked', template)
        self.assertIn('name="scoreFilter" value="2" checked', template)
        self.assertIn('name="scoreFilter" value="1"', template)
        self.assertIn('name="itemFilter" value="turnaround"', template)
        self.assertIn('name="itemFilter" value="supply"', template)
        self.assertIn('name="itemFilter" value="nps"', template)
        self.assertIn("미선택 시 전체", template)
        self.assertIn("여러 항목 선택 시 모두 만족", template)
        self.assertNotIn("스크리닝 2점 이상 종목", template)

    def test_backtest_page_sends_selected_filters(self):
        template = app_module.BACKTEST_TEMPLATE

        self.assertIn("input[name=\"scoreFilter\"]:checked", template)
        self.assertIn("input[name=\"itemFilter\"]:checked", template)
        self.assertIn("scores:", template)
        self.assertIn("items:", template)
```

- [ ] **Step 2: 템플릿 테스트를 실행해 체크박스가 없어 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_page_exposes_score_and_item_filters tests.test_app.FlaskApiTest.test_backtest_page_sends_selected_filters -v`

Expected: `name="scoreFilter"` 개수가 `0`이어서 FAIL

- [ ] **Step 3: 체크박스 UI와 요청 페이로드 구현**

`BACKTEST_TEMPLATE` 설정 영역에 종합점수와 항목별 필터 그룹을 추가한다.

```html
<div class="cfg-group filter-config">
    <label>종합점수 <span>미선택 시 전체</span></label>
    <div class="check-options">
        <label><input type="checkbox" name="scoreFilter" value="3" checked>3점</label>
        <label><input type="checkbox" name="scoreFilter" value="2" checked>2점</label>
        <label><input type="checkbox" name="scoreFilter" value="1">1점</label>
    </div>
</div>
<div class="cfg-group filter-config">
    <label>항목별 필터 <span>여러 항목 선택 시 모두 만족</span></label>
    <div class="check-options">
        <label><input type="checkbox" name="itemFilter" value="turnaround">연간실적호전</label>
        <label><input type="checkbox" name="itemFilter" value="supply">순매수전환</label>
        <label><input type="checkbox" name="itemFilter" value="nps">국민연금 매수</label>
    </div>
</div>
```

체크박스는 기존 입력 CSS와 충돌하지 않도록 `.check-options` 전용 규칙을 사용한다. `runBacktest()`의 요청 본문에 다음 배열을 추가한다.

```javascript
scores: Array.from(
    document.querySelectorAll('input[name="scoreFilter"]:checked'),
    input => +input.value,
),
items: Array.from(
    document.querySelectorAll('input[name="itemFilter"]:checked'),
    input => input.value,
),
```

응답이 HTTP 400이면 폴링을 시작하지 않고 서버의 `error` 메시지를 토스트로 표시하도록 `response.ok`를 확인한다. 헤더 설명은 `종합점수와 항목 조건으로 선택한 종목의 과거 성과를 시뮬레이션합니다`로 바꾼다.

- [ ] **Step 4: README의 백테스트 대상 설명과 사용법 갱신**

`README.md`에서 웹 백테스트가 항상 2점 이상을 사용한다는 문장을 제거하고 다음 규칙을 명시한다.

```markdown
웹 백테스트에서는 종합점수 `3점`, `2점`, `1점`을 동시에 선택할 수 있습니다. 최초 기본값은 기존 동작과 같은 `3점 + 2점`이며, 점수를 하나도 선택하지 않으면 전체 점수를 대상으로 합니다.

`연간실적호전`, `순매수전환`, `국민연금 매수` 항목도 동시에 선택할 수 있습니다. 여러 항목을 선택하면 선택한 항목을 **모두 만족하는 종목만** 포함하고, 아무 항목도 선택하지 않으면 항목 제한을 적용하지 않습니다. 점수 조건과 항목 조건도 함께 만족해야 합니다.
```

GitHub Actions 일일 리포트의 2점 이상 규칙은 이번 변경 범위가 아니므로 그대로 유지한다.

- [ ] **Step 5: 화면 테스트와 전체 회귀 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_page_exposes_score_and_item_filters tests.test_app.FlaskApiTest.test_backtest_page_sends_selected_filters -v`

Expected: `Ran 2 tests`와 `OK`

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v`

Expected: 전체 테스트 `OK`, 실패 `0`, 오류 `0`

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py`

Expected: exit code `0`, 출력 없음

Run: `uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests`

Expected: `All checks passed!`

Run: `git diff --check`

Expected: exit code `0`, 출력 없음

- [ ] **Step 6: 실행 중인 앱을 새 코드로 재시작하고 HTTP 스모크 테스트**

기존 `python app.py` 프로세스가 있으면 정상 종료한 뒤 README의 한 줄 `uv run` 명령으로 재시작한다. `GET /backtest`가 HTTP 200이고 새 체크박스 문구가 포함되는지, 잘못된 `scores` 요청이 HTTP 400인지 확인한다. 실제 장기 가격 수집은 자동화된 후보 필터·API 테스트로 대체하며 외부 데이터를 변경하지 않는다.

```bash
curl -fsS http://127.0.0.1:5000/backtest | rg '미선택 시 전체|여러 항목 선택 시 모두 만족'
curl -sS -o /tmp/backtest-invalid.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"scores":[4]}' \
  http://127.0.0.1:5000/api/backtest/run
```

Expected: 첫 명령에 두 안내 문구가 출력되고 두 번째 명령의 상태 코드는 `400`

- [ ] **Step 7: 문서·화면 변경을 커밋하고 푸시 후 원격 SHA 확인**

```bash
git add app.py tests/test_app.py README.md docs/superpowers/plans/2026-07-14-backtest-score-item-filters.md
git commit -m "Let users define the full backtest candidate set" \
  -m "Constraint: Score filters are multi-select and item filters require every selected signal
Rejected: A single minimum-score dropdown | it cannot represent arbitrary score combinations
Confidence: high
Scope-risk: moderate
Directive: Keep the web backtest filters independent from the scheduled daily-report 2+ rule
Tested: Full unittest suite, 64 filter combinations, py_compile, Ruff, HTTP UI and validation smoke tests"
git push origin master
git rev-parse HEAD
git ls-remote origin refs/heads/master
```

Expected: 로컬 `HEAD`와 `origin/master` SHA가 일치하고 `get-pip.py`만 추적되지 않은 상태로 남는다.
