# 국민연금 신규·추가매수 신호 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 기존의 무기한 국민연금 보유 점수를 최근 신규·추가매수일부터 달력 기준 3개월 동안만 유지되는 1점 신호로 교체한다.

**Architecture:** 새 nps_tracker.py가 날짜 계산, 상태 검증, 이벤트 병합을 순수 로직으로 담당한다. screening.py는 FnGuide Snapshot과 ShareAnalysis를 수집·검증한 뒤 후보 상태를 만들고 세 원천이 모두 성공한 경우에만 상태를 원자적으로 저장한다. 웹·정적 HTML·텔레그램은 공통 결과를 표시하고 GitHub Actions는 상태 파일을 캐시한다.

**Tech Stack:** Python 3.11 표준 라이브러리, requests, Flask, pandas, unittest, GitHub Actions cache v4

## Global Constraints

- 기존 국민연금 보유 점수를 대체하고 종합점수 최댓값 3을 유지한다.
- event_date <= as_of_date < event_date + 3 calendar months 동안만 1점이다.
- 만료는 음수 카테고리가 아니라 국민연금 1점과 카테고리 제거다.
- 추가매수는 점수를 누적하지 않고 최신 매수일 기준으로 만료일을 갱신한다.
- 주식 수 증가가 없는 지분율 변화는 추가매수로 판정하지 않는다.
- 현재 Snapshot에서 국민연금 보유 행이 사라지면 신호도 제거한다.
- FnGuide 공개 주요주주 범위만 탐지하며 전체 주문 내역이라고 표현하지 않는다.
- 새 런타임 의존성을 추가하지 않는다.
- 불완전한 수집은 기존 nps_state.json과 cache_data.json을 덮어쓰지 않는다.

---

## 파일 책임

- Create: nps_tracker.py — 달력 날짜, 상태 스키마, 순수 상태 전이
- Create: tests/test_nps_tracker.py — 네트워크 없는 경계·상태 테스트
- Modify: screening.py — Snapshot/ShareAnalysis 파서, 병렬 수집, 상태 커밋
- Modify: tests/test_screening.py — 파서·수집·점수 통합 테스트
- Modify: app.py, tests/test_app.py — 캐시 버전과 대시보드 문구
- Modify: stock_screener.py, daily_report.py — 정적/텔레그램 표현
- Create: tests/test_stock_screener.py — 정적 HTML 계약
- Modify: tests/test_daily_report.py — 텔레그램 계약
- Modify: .github/workflows/daily_report.yml, .gitignore, README.md
- Create: tests/test_workflow.py — Actions 상태 캐시 계약

---

### Task 1: 순수 국민연금 상태 전이

**Files:**
- Create: nps_tracker.py
- Create: tests/test_nps_tracker.py

**Interfaces:**
- Produces: add_calendar_months(value: date, months: int = 3) -> date
- Produces: kst_today() -> date
- Produces: load_nps_state(path: str) -> dict | None
- Produces: save_nps_state(path: str, state: dict) -> None
- Produces: reconcile_nps_signals(holdings: list[dict], events: list[dict], previous_state: dict | None, *, as_of: date) -> tuple[list[dict], dict]

- [ ] **Step 1: 날짜·초기화 실패 테스트 작성**

    from datetime import date
    import unittest

    from nps_tracker import add_calendar_months, reconcile_nps_signals


    class NpsTrackerTest(unittest.TestCase):
        def test_month_end_expiry(self):
            self.assertEqual(
                add_calendar_months(date(2026, 1, 31)),
                date(2026, 4, 30),
            )

        def test_bootstrap_scores_only_confirmed_recent_event(self):
            holdings = [
                {"종목코드": "1", "종목명": "기존", "보통주": "1,000",
                 "지분율(%)": "5.0", "최종변동일": "2025/01/01"},
                {"종목코드": "2", "종목명": "신규", "보통주": "2,000",
                 "지분율(%)": "6.0", "최종변동일": "2026/07/01"},
            ]
            events = [
                {"종목코드": "2", "종목명": "신규",
                 "변동일": "2026-07-01", "변동사유": "신규주요주주(+)",
                 "변동전": 1500, "증감": 500, "변동후": 2000,
                 "지분율(%)": 6.0}
            ]
            active, state = reconcile_nps_signals(
                holdings, events, None, as_of=date(2026, 7, 12)
            )
            self.assertEqual([row["종목코드"] for row in active], ["2"])
            self.assertEqual(active[0]["매수구분"], "신규매수")
            self.assertEqual(active[0]["만료일"], "2026-10-01")
            self.assertEqual(set(state["holdings"]), {"1", "2"})

- [ ] **Step 2: Red 확인**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_nps_tracker.py' -v

Expected: ModuleNotFoundError for nps_tracker

- [ ] **Step 3: 날짜·상태 기반 구현**

Create nps_tracker.py with:

    from calendar import monthrange
    from datetime import date, datetime, timedelta, timezone
    import json
    import os
    import tempfile

    STATE_VERSION = 1
    KST = timezone(timedelta(hours=9), name="Asia/Seoul")

    class NpsStateError(ValueError):
        pass

    def parse_int(value):
        return int(str(value or "").replace(",", "").strip() or 0)

    def parse_float(value):
        return float(str(value or "").replace(",", "").strip() or 0)

    def parse_date(value):
        text = str(value or "").strip().replace("/", "-").replace(".", "-")
        try:
            return date.fromisoformat(text) if text else None
        except ValueError:
            return None

    def add_calendar_months(value, months=3):
        index = value.month - 1 + months
        year, month = value.year + index // 12, index % 12 + 1
        return date(year, month, min(value.day, monthrange(year, month)[1]))

    def kst_today():
        return datetime.now(KST).date()

    def load_nps_state(path):
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise NpsStateError(f"국민연금 상태 파일 오류: {exc}") from exc
        if state.get("version") != STATE_VERSION:
            raise NpsStateError("지원하지 않는 국민연금 상태 버전입니다")
        if not isinstance(state.get("holdings"), dict) or not isinstance(
            state.get("signals"), dict
        ):
            raise NpsStateError("국민연금 상태 구조가 올바르지 않습니다")
        return state

    def save_nps_state(path, state):
        directory = os.path.dirname(os.path.abspath(path))
        fd, temporary = tempfile.mkstemp(
            prefix=".nps-state-", suffix=".json", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        except Exception:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

Implement reconcile_nps_signals with these exact rules:

1. Normalize holdings by 종목코드; 보통주 is int, 지분율 is float, 최종변동일 is ISO date.
2. Start from previous signals only for codes still in current holdings.
3. Ignore events whose code is not currently held, whose date is invalid, or whose 증감 is <= 0.
4. Classify as 신규매수 when reason starts with 신규 or 변동전 is 0; otherwise 추가매수.
5. Merge the latest event per code; same date prefers 신규매수.
6. When previous_state exists, a newly appearing holding creates 신규매수 and a later Snapshot date plus increased shares creates 추가매수.
7. Keep only 매수일 <= as_of < 만료일.
8. Return display rows with 매수구분, 매수일, 만료일, 변동사유, 변동전, 증감, 변동후 and a version-1 state.

- [ ] **Step 4: 경계 테스트 확장**

Add tests proving:

- 2026-04-12 signal is active 2026-07-11 and absent 2026-07-12.
- 2026-06-30 additional buy resets expiry to 2026-09-30 without a second point.
- negative event leaves the original buy date unchanged.
- disappeared holding removes its signal.
- initial state does not mark every current holding as new.
- invalid JSON raises NpsStateError.
- atomic save/load round trip preserves the state.

- [ ] **Step 5: Green 확인**

Run the Task 1 test command.

Expected: all tracker tests OK

- [ ] **Step 6: Lore 커밋**

    git add nps_tracker.py tests/test_nps_tracker.py
    git commit -m "Keep pension buy signals for their exact calendar window" \
      -m "Constraint: Signals expire after three calendar months and never stack" \
      -m "Confidence: high" \
      -m "Scope-risk: moderate" \
      -m "Tested: NPS tracker unit tests"

---

### Task 2: FnGuide 변동내역 파서와 수집

**Files:**
- Modify: screening.py:25-274
- Modify: tests/test_screening.py:100-160

**Interfaces:**
- Produces: parse_nps_share_events(html: str, *, expected_code: str, stock_name: str) -> list[dict]
- Produces: fetch_nps_share_events(holdings: list[dict], *, require_coverage: bool, max_workers: int = 12, timeout: float = 15) -> list[dict]

- [ ] **Step 1: HTML 파서 실패 테스트**

Use a ShareAnalysis fixture whose sharebody contains 국민연금 신규 +200, 국민연금 매도 -100, and another institution +5. Assert two rows only, normalized ISO dates and integer share values. Add a ticker mismatch test returning an empty list. Update the existing Snapshot expectation to include:

    "종목코드": "005930"

- [ ] **Step 2: Red 확인**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_screening.py' -v

Expected: import or assertion failure for the new parser and code field

- [ ] **Step 3: 파서 구현**

Add:

    SHARE_ANALYSIS_URL = "https://wcomp.fnguide.com/CompanyInfo/ShareAnalysis"
    _SHARE_BODY_RE = re.compile(
        r'<tbody[^>]*id=["\\']sharebody["\\'][^>]*>(.*?)</tbody>',
        re.I | re.S,
    )
    _HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
    _HTML_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)

parse_nps_share_events validates _snapshot_ticker, reads ten cells in this order, filters 국민연금공단 and 보통주, and returns:

    {
        "종목코드": expected_code.upper(),
        "종목명": normalize_stock_name(stock_name),
        "변동일": cells[3].replace(".", "-").replace("/", "-"),
        "변동사유": cells[4],
        "주식종류": cells[5],
        "변동전": int(cells[6].replace(",", "")),
        "증감": int(cells[7].replace(",", "")),
        "변동후": int(cells[8].replace(",", "")),
        "지분율(%)": float(cells[9].replace(",", "")),
    }

Add 종목코드 to parse_nps_holding.

- [ ] **Step 4: 병렬 수집 테스트와 구현**

Test valid coverage and a below-80-percent failure by patching _fetch_nps_share_one with max_workers=1. The low-coverage case must raise when require_coverage=True and return the successfully parsed rows with a warning when require_coverage=False.

Implement _fetch_nps_share_one with SHARE_ANALYSIS_URL and fetch_nps_share_events with the existing ThreadPoolExecutor pattern. Compute minimum = ceil(len(holdings) * 0.8). Raise ScreeningDataError only when require_coverage is true and valid_pages is below minimum. Otherwise log the low coverage and partial failures, retain successful rows, and sort by code/date.

- [ ] **Step 5: Green 확인**

Run the Task 2 test command.

Expected: all screening tests OK

- [ ] **Step 6: Lore 커밋**

    git add screening.py tests/test_screening.py
    git commit -m "Distinguish pension purchases from shareholder decreases" \
      -m "Constraint: FnGuide exposes only the latest twenty shareholder changes" \
      -m "Confidence: high" \
      -m "Scope-risk: moderate" \
      -m "Tested: Snapshot and ShareAnalysis parser and coverage tests"

---

### Task 3: 원천 수집과 상태 저장 트랜잭션

**Files:**
- Modify: screening.py:277-308
- Modify: tests/test_screening.py
- Modify: .gitignore

**Interfaces:**
- Produces: build_nps_buy_signals(ticker_map_path: str, state_path: str, *, as_of: date | None = None) -> tuple[list[dict], dict]
- Extends: fetch_all_data with nps_state_path and as_of keyword arguments

- [ ] **Step 1: 상태 커밋 실패 테스트**

With TemporaryDirectory, patch turn/supply/holdings/events and assert a successful require_all call creates nps_state.json. In a second test, make fetch_turnaround raise ScreeningDataError, return a candidate NPS state, and assert the pre-existing state bytes are unchanged.

- [ ] **Step 2: Red 확인**

Run screening tests.

Expected: build_nps_buy_signals or new keyword argument failure

- [ ] **Step 3: 후보 상태와 커밋 구현**

Add:

    from datetime import date
    from nps_tracker import (
        kst_today,
        load_nps_state,
        reconcile_nps_signals,
        save_nps_state,
    )

    DEFAULT_NPS_STATE = os.path.join(os.path.dirname(__file__), "nps_state.json")

Implement build_nps_buy_signals with effective_date = as_of or kst_today(), then load state, fetch holdings, and call:

    events = fetch_nps_share_events(
        holdings,
        require_coverage=previous is None,
    )

Then call reconcile_nps_signals. This makes the first initialization strict while an established state can survive partial ShareAnalysis failures using Snapshot differences and preserved signals.

Refactor fetch_all_data so it gathers turn, supply, and the NPS candidate separately. If require_all and any error exists, raise before saving. Save pending NPS state only when errors is empty; wrap save errors in ScreeningDataError.

- [ ] **Step 4: 기존 mock 경계 갱신**

Change SourceOrchestrationTest patches from fetch_nps_holdings to build_nps_buy_signals and patch save_nps_state where persistence is not under test. Keep the existing default-mode and required-mode behaviors.

- [ ] **Step 5: 상태 파일 제외와 Green 확인**

Append nps_state.json to .gitignore, then run screening and tracker tests.

Expected: both suites OK and git status does not show a generated nps_state.json

- [ ] **Step 6: Lore 커밋**

    git add screening.py tests/test_screening.py .gitignore
    git commit -m "Publish pension state only after complete source validation" \
      -m "Constraint: Partial refreshes must preserve the last trustworthy state" \
      -m "Confidence: high" \
      -m "Scope-risk: moderate" \
      -m "Tested: Source orchestration and atomic state persistence tests"

---

### Task 4: 점수와 웹 대시보드 마이그레이션

**Files:**
- Modify: screening.py:311-372
- Modify: app.py:34-150, 630-870
- Modify: tests/test_screening.py
- Modify: tests/test_app.py

**Interfaces:**
- Preserves result keys 종목명, 종합점수, 출처, 순위
- Preserves stats.nps_count while changing its meaning to active NPS buy signals

- [ ] **Step 1: 점수 실패 테스트**

    def test_nps_signal_is_one_point_with_new_source_name(self):
        results, stats = calculate_scores(
            [{"종목명": "A"}],
            [{"종목명": "A"}],
            [{"종목명": "A", "매수구분": "추가매수",
              "매수일": "2026-06-30", "만료일": "2026-09-30"}],
        )
        self.assertEqual(results[0]["종합점수"], 3)
        self.assertEqual(
            results[0]["출처"],
            "연간실적호전, 순매수전환, 국민연금 신규/추가매수",
        )
        self.assertEqual(results[0]["[연금]매수구분"], "추가매수")
        self.assertEqual(stats["nps_count"], 1)

    def test_expired_signal_removal_reduces_score_by_one(self):
        active, _ = calculate_scores(
            [{"종목명": "A"}], [{"종목명": "A"}], [{"종목명": "A"}]
        )
        expired, _ = calculate_scores(
            [{"종목명": "A"}], [{"종목명": "A"}], []
        )
        self.assertEqual(active[0]["종합점수"] - expired[0]["종합점수"], 1)
        self.assertNotIn("국민연금", expired[0]["출처"])

- [ ] **Step 2: Red 확인**

Run screening tests.

Expected: source label assertion failure

- [ ] **Step 3: 점수 소스 이름 변경**

Add:

    NPS_SOURCE_LABEL = "국민연금 신규/추가매수"

Use it only when stock is in nps_map. Keep the 연금 detail prefix, nps_count, and maximum score unchanged.

- [ ] **Step 4: 구형 캐시와 화면 실패 테스트**

In tests/test_app.py, use a temporary CACHE_FILE and assert load_cache returns False for JSON without version. Add:

    def test_dashboard_describes_time_bounded_nps_signal(self):
        self.assertIn("국민연금 신규/추가매수", app_module.HTML_TEMPLATE)
        self.assertIn("3개월", app_module.HTML_TEMPLATE)

- [ ] **Step 5: 웹 캐시 버전과 표현 구현**

Add CACHE_VERSION = 2. Include version when refresh_data writes cache. load_cache must return False when cache.get("version") != CACHE_VERSION so an old indefinite holding score cannot be displayed.

Replace visible copy:

- 국민연금 보유 -> 국민연금 신규/추가매수
- 국민연금공단 보유 종목 -> 국민연금 신규/추가매수 신호
- 국민연금 tab -> 국민연금 매수

Add this sentence to the dashboard:

    국민연금 주요주주 신규·추가매수 신호는 매수일부터 3개월 동안만 1점으로 반영됩니다.

- [ ] **Step 6: Green 확인**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_screening.py' -v
    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_app.py' -v

Expected: both suites OK

- [ ] **Step 7: Lore 커밋**

    git add screening.py app.py tests/test_screening.py tests/test_app.py
    git commit -m "Show only active pension purchase points in the dashboard" \
      -m "Constraint: Legacy holding caches cannot retain indefinite pension points" \
      -m "Confidence: high" \
      -m "Scope-risk: moderate" \
      -m "Tested: Scoring and Flask cache and template tests"

---

### Task 5: 정적 HTML과 텔레그램 표현

**Files:**
- Modify: stock_screener.py:1-120, 350-440, 493-520
- Create: tests/test_stock_screener.py
- Modify: daily_report.py:173-202, 274-299
- Modify: tests/test_daily_report.py

**Interfaces:**
- Consumes 매수구분, 매수일, 만료일 details from calculate_scores
- Produces identical category wording in static HTML and Telegram

- [ ] **Step 1: 표현 실패 테스트**

Create a one-row DataFrame whose source is 국민연금 신규/추가매수 and whose 연금 details include 추가매수, 2026-06-30, and 2026-09-30. Generate into TemporaryDirectory and assert the HTML contains the category and expiry date.

Add to tests/test_daily_report.py:

    def test_message_uses_nps_buy_signal_label(self):
        message = daily_report.format_telegram_message(
            [{"종목명": "A", "종합점수": 1,
              "출처": "국민연금 신규/추가매수"}],
            {"nps_count": 1, "score_1": 1},
            {"metrics": {}, "stock_performance": []},
            {},
        )
        self.assertIn("국민연금 신규/추가매수: 1종목", message)

- [ ] **Step 2: Red 확인**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_stock_screener.py' -v
    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_daily_report.py' -v

Expected: new wording assertions fail

- [ ] **Step 3: 정적 HTML 구현**

Replace 국민연금 보유현황, 국민연금 보유, and 국민연금공단 보유 종목 with 신규/추가매수 copy. Change the source tag condition from exact equality to:

    elif "국민연금" in src:
        sources_html += '<span class="tag tag-nps">국민연금 매수</span> '

The existing 연금-prefixed detail renderer must expose 매수구분, 매수일, 만료일 without a duplicate rendering path.

- [ ] **Step 4: 텔레그램 구현**

Change summary copy to:

    lines.append(
        f"  국민연금 신규/추가매수: {stats.get('nps_count', 0)}종목"
    )

Change log/source_counts display names while preserving nps_data and nps_count identifiers.

- [ ] **Step 5: Green 확인과 Lore 커밋**

Run both Task 5 tests, expect OK, then:

    git add stock_screener.py daily_report.py tests/test_stock_screener.py tests/test_daily_report.py
    git commit -m "Explain pension purchase timing in every report surface" \
      -m "Constraint: Web, static HTML, and Telegram must describe one shared score" \
      -m "Confidence: high" \
      -m "Scope-risk: narrow" \
      -m "Tested: Static report and Telegram formatting tests"

---

### Task 6: GitHub Actions 상태 지속과 README

**Files:**
- Modify: .github/workflows/daily_report.yml:15-46
- Create: tests/test_workflow.py
- Modify: README.md:5-170

**Interfaces:**
- Consumes and produces repository-root nps_state.json
- Requires no new secrets

- [ ] **Step 1: 워크플로 실패 테스트**

    from pathlib import Path
    import unittest

    class WorkflowStateCacheTest(unittest.TestCase):
        def test_daily_report_restores_and_saves_nps_state(self):
            workflow = Path(
                ".github/workflows/daily_report.yml"
            ).read_text(encoding="utf-8")
            self.assertIn("actions/cache/restore@v4", workflow)
            self.assertIn("actions/cache/save@v4", workflow)
            self.assertGreaterEqual(workflow.count("nps_state.json"), 2)
            self.assertIn("nps-state-", workflow)

- [ ] **Step 2: Red 확인**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v

Expected: cache action assertion failure

- [ ] **Step 3: Actions 캐시 구현**

After checkout:

    - name: 국민연금 신호 상태 복원
      uses: actions/cache/restore@v4
      with:
        path: nps_state.json
        key: nps-state-\${{ runner.os }}-\${{ github.run_id }}
        restore-keys: |
          nps-state-\${{ runner.os }}-

After the daily report and before artifact upload:

    - name: 국민연금 신호 상태 저장
      if: success()
      uses: actions/cache/save@v4
      with:
        path: nps_state.json
        key: nps-state-\${{ runner.os }}-\${{ github.run_id }}

- [ ] **Step 4: README 계약**

Change the third criterion to:

    | 국민연금 신규/추가매수 | 공개 주요주주 신규·보유량 증가 이벤트 발생일부터 3개월 | FnGuide Snapshot + ShareAnalysis |

Document all facts:

- multiple buy events still contribute at most one point;
- an additional buy resets the three-month window;
- the expiry date removes the category and one point;
- sells and holding decreases do not refresh the window;
- undisclosed trades below the public-data threshold are outside detection scope;
- nps_state.json persists active signals locally and through Actions cache.

- [ ] **Step 5: Green과 문서 검사**

Run:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -p 'test_workflow.py' -v
    git diff --check
    rg -n "국민연금 신규/추가매수|3개월|nps_state.json" README.md .github/workflows/daily_report.yml

Expected: test OK, no diff error, all terms present

- [ ] **Step 6: Lore 커밋**

    git add .github/workflows/daily_report.yml tests/test_workflow.py README.md
    git commit -m "Carry active pension signals across scheduled reports" \
      -m "Constraint: GitHub-hosted runners start with an empty filesystem" \
      -m "Confidence: high" \
      -m "Scope-risk: moderate" \
      -m "Directive: Keep nps_state.json cached but untracked" \
      -m "Tested: Workflow contract test and documentation scan"

---

### Task 7: 전체 검증과 완료 감사

**Files:**
- Modify only files from Tasks 1-6 when a verification failure proves a defect

**Interfaces:**
- Verifies every completion condition in the design spec

- [ ] **Step 1: 전체 회귀 테스트**

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v

Expected: zero failures and zero errors

- [ ] **Step 2: 정적 검사와 컴파일**

    uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py

Expected: All checks passed and both commands exit 0

- [ ] **Step 3: 실제 FnGuide 파서 스모크**

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import requests; from screening import parse_nps_share_events, SHARE_ANALYSIS_URL; html=requests.get(SHARE_ANALYSIS_URL,params={'cmp_cd':'069620'},timeout=20).text; rows=parse_nps_share_events(html,expected_code='069620',stock_name='대웅제약'); print([(r['변동일'],r['변동사유'],r['증감']) for r in rows[:5]]); assert any(r['증감']>0 for r in rows); assert any(r['증감']<0 for r in rows)"

Expected: positive and negative 국민연금 rows printed

- [ ] **Step 4: 임시 상태를 사용한 전체 실데이터 스모크**

Use tempfile.TemporaryDirectory inside Python so the command is cross-platform:

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import os,tempfile; from screening import fetch_all_data; d=tempfile.TemporaryDirectory(); p=os.path.join(d.name,'nps_state.json'); turn,supply,nps=fetch_all_data(require_all=True,nps_state_path=p); print({'turn':len(turn),'supply':len(supply),'nps_buy_signals':len(nps)}); assert turn and supply and os.path.exists(p)"

Expected: source counts printed and temporary state created

- [ ] **Step 5: 대시보드 스모크**

    uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import app; c=app.app.test_client(); assert c.get('/').status_code==200; assert c.get('/api/status').status_code==200; assert '국민연금 신규/추가매수' in c.get('/').get_data(as_text=True); print('dashboard_ok')"

Expected: dashboard_ok

- [ ] **Step 6: 요구사항별 증거 감사**

Record direct evidence for:

1. 신규매수 +1: tracker 신규 test and scoring test.
2. 3개월 후 -1/category removal: expiry boundary and score difference tests.
3. 추가매수 날짜 reset: additional-buy reset test.
4. no stacking: one signal per ticker assertion.
5. shared UI/report behavior: app, static HTML, and Telegram tests.
6. restart/Actions persistence: state round-trip and workflow tests.
7. public-data scope disclosure: README and dashboard text scan.

Add a missing test before completion if any requirement lacks direct evidence.

- [ ] **Step 7: 검증 실패는 소유 Task로 되돌려 수정**

Task 7 자체는 파일을 소유하지 않는다. 실패가 나오면 해당 파일을 소유한 Task 1-6의 마지막 Green 단계로 돌아가 테스트를 먼저 추가하고 그 Task의 명시된 Lore 커밋에 포함한다. 모든 검증이 처음부터 통과하면 빈 커밋을 만들지 않는다.
