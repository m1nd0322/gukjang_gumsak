# Daily Screening Results DuckDB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매일 자동갱신에서 계산한 종합결과의 종목명, 점수, 해당항목, 상세정보를 DuckDB 날짜별 스냅샷으로 원자 갱신한다.

**Architecture:** `StockDB`가 `screening_results` 스키마와 같은 날짜 전체 교체 트랜잭션을 소유한다. Flask 자동갱신과 GitHub Actions 일일 리포트는 점수 계산 직후 이 인터페이스를 호출하고, GitHub Actions는 러너 사이에서 DuckDB 파일을 캐시로 복원·저장한다.

**Tech Stack:** Python 3.11, DuckDB, Flask, APScheduler, GitHub Actions, `unittest`, Ruff

## Global Constraints

- 스냅샷 날짜는 `Asia/Seoul` 기준이다.
- 같은 날짜 재실행은 해당 날짜 전체를 교체하고 이전 날짜는 보존한다.
- DuckDB 저장 성공 전에는 Flask 메모리 상태와 `cache_data.json`을 새 결과로 게시하지 않는다.
- GitHub Actions에서 DuckDB 저장 실패는 일일 리포트 실패로 처리한다.
- 새 의존성을 추가하지 않는다.
- 사용자 소유의 미추적 `get-pip.py`는 변경하거나 커밋하지 않는다.

---

### Task 1: 날짜별 종합결과 저장소

**Files:**
- Modify: `stock_db.py:17-91`
- Modify: `stock_db.py:631-765`
- Test: `tests/test_stock_db.py`

**Interfaces:**
- Consumes: `calculate_scores()`가 반환하는 `list[dict]`
- Produces: `StockDB.replace_screening_results(results: List[dict], snapshot_date: Optional[date] = None) -> int`
- Produces: DuckDB 테이블 `screening_results(snapshot_date, stock_name, score, matched_items, details, updated_at)`

- [x] **Step 1: 저장 필드와 테이블 노출을 검증하는 실패 테스트 작성**

```python
from datetime import date, datetime, timedelta

def test_screening_results_store_requested_fields_and_are_queryable(self):
    saved = self.db.replace_screening_results(
        [
            {
                "종목명": "삼성전자",
                "종합점수": 2,
                "출처": "연간실적호전, 순매수전환",
                "순위": 1,
                "[턴]PER": "12.3",
                "[수급]수익률(%)": "4.2",
            }
        ],
        snapshot_date=date(2026, 7, 13),
    )

    page = self.db.query_table("screening_results", order_by="stock_name")

    self.assertEqual(saved, 1)
    self.assertEqual(page["total"], 1)
    self.assertEqual(page["rows"][0]["stock_name"], "삼성전자")
    self.assertEqual(page["rows"][0]["score"], 2)
    self.assertEqual(
        page["rows"][0]["matched_items"], "연간실적호전, 순매수전환"
    )
    self.assertEqual(
        json.loads(page["rows"][0]["details"]),
        {"[턴]PER": "12.3", "[수급]수익률(%)": "4.2"},
    )
```

- [x] **Step 2: 동일 날짜 교체와 이전 날짜 보존 실패 테스트 작성**

```python
def test_screening_results_replace_same_day_and_preserve_previous_days(self):
    self.db.replace_screening_results(
        [{"종목명": "전날종목", "종합점수": 1, "출처": "연간실적호전"}],
        snapshot_date=date(2026, 7, 12),
    )
    self.db.replace_screening_results(
        [
            {"종목명": "유지종목", "종합점수": 1, "출처": "연간실적호전"},
            {"종목명": "탈락종목", "종합점수": 2, "출처": "연간실적호전, 순매수전환"},
        ],
        snapshot_date=date(2026, 7, 13),
    )
    self.db.replace_screening_results(
        [{"종목명": "유지종목", "종합점수": 3, "출처": "연간실적호전, 순매수전환, 국민연금 신규/추가매수"}],
        snapshot_date=date(2026, 7, 13),
    )

    connection = self.db._connect()
    try:
        rows = connection.execute(
            "SELECT CAST(snapshot_date AS VARCHAR), stock_name, score "
            "FROM screening_results ORDER BY snapshot_date, stock_name"
        ).fetchall()
    finally:
        connection.close()

    self.assertEqual(
        rows,
        [("2026-07-12", "전날종목", 1), ("2026-07-13", "유지종목", 3)],
    )
```

- [x] **Step 3: 빈 결과 교체 실패 테스트 작성**

```python
def test_empty_screening_results_clear_only_requested_day(self):
    self.db.replace_screening_results(
        [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}],
        snapshot_date=date(2026, 7, 12),
    )
    self.db.replace_screening_results(
        [{"종목명": "B", "종합점수": 1, "출처": "순매수전환"}],
        snapshot_date=date(2026, 7, 13),
    )

    saved = self.db.replace_screening_results([], snapshot_date=date(2026, 7, 13))

    connection = self.db._connect()
    try:
        rows = connection.execute(
            "SELECT CAST(snapshot_date AS VARCHAR), stock_name FROM screening_results"
        ).fetchall()
    finally:
        connection.close()
    self.assertEqual(saved, 0)
    self.assertEqual(rows, [("2026-07-12", "A")])
```

- [x] **Step 4: 대상 테스트가 기능 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v`

Expected: `StockDB`에 `replace_screening_results`가 없어 FAIL.

- [x] **Step 5: 테이블과 원자 교체 메서드 최소 구현**

```python
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

con.execute("""
    CREATE TABLE IF NOT EXISTS screening_results (
        snapshot_date DATE NOT NULL,
        stock_name VARCHAR NOT NULL,
        score INTEGER NOT NULL,
        matched_items VARCHAR NOT NULL,
        details JSON NOT NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (snapshot_date, stock_name)
    )
""")

def replace_screening_results(
    self,
    results: List[dict],
    snapshot_date: Optional[date] = None,
) -> int:
    snapshot_date = snapshot_date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows = []
    seen_names = set()
    for result in results:
        stock_name = str(result.get("종목명") or "").strip()
        if not stock_name:
            raise ValueError("종합결과에 종목명이 없습니다")
        if stock_name in seen_names:
            raise ValueError(f"종합결과에 중복 종목이 있습니다: {stock_name}")
        seen_names.add(stock_name)
        try:
            score = int(result["종합점수"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"종합결과 점수가 올바르지 않습니다: {stock_name}") from exc
        matched_items = str(result.get("출처") or "")
        details = {
            key: value
            for key, value in result.items()
            if key not in {"종목명", "종합점수", "출처", "순위"}
        }
        details_json = json.dumps(details, ensure_ascii=False, allow_nan=False)
        rows.append((snapshot_date, stock_name, score, matched_items, details_json))

    connection = self._connect()
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(
            "DELETE FROM screening_results WHERE snapshot_date = ?", [snapshot_date]
        )
        if rows:
            connection.executemany(
                """
                INSERT INTO screening_results
                    (snapshot_date, stock_name, score, matched_items, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return len(rows)
```

`screening_results`를 `_ALLOWED_TABLES`에 추가하고 `query_table()` 문서의 허용 테이블 설명도 갱신한다.

- [x] **Step 6: 저장소 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v`

Expected: 모든 `test_stock_db` 테스트 PASS.

- [x] **Step 7: 저장소 변경 커밋**

```bash
git add stock_db.py tests/test_stock_db.py
git commit -m "Keep each daily screening snapshot queryable"
```

---

### Task 2: Flask 자동갱신 저장 순서

**Files:**
- Modify: `app.py:100-145`
- Test: `tests/test_app.py:137-165`

**Interfaces:**
- Consumes: Task 1의 `StockDB.replace_screening_results()`
- Produces: 성공한 `refresh_data()`가 DuckDB 저장 후 메모리와 JSON 캐시를 게시하는 순서

- [x] **Step 1: DuckDB 저장이 게시보다 먼저 실행되는 실패 테스트 작성**

```python
def test_refresh_persists_results_before_publishing_cache(self):
    result = [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}]
    stats = {"score_3": 0, "score_2": 0, "score_1": 1}
    with tempfile.TemporaryDirectory() as directory:
        cache_path = Path(directory) / "cache_data.json"

        def persist(rows):
            self.assertEqual(rows, result)
            self.assertEqual(app_module.current_data["result"], [])
            self.assertFalse(cache_path.exists())
            return 1

        with (
            patch.object(app_module, "CACHE_FILE", str(cache_path)),
            patch.object(app_module, "fetch_all_data", return_value=([], [], [])),
            patch.object(app_module, "calculate_scores", return_value=(result, stats)),
            patch.object(
                app_module.stock_db,
                "replace_screening_results",
                side_effect=persist,
            ) as save_results,
        ):
            refreshed = app_module.refresh_data()

        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    self.assertTrue(refreshed)
    save_results.assert_called_once_with(result)
    self.assertEqual(app_module.current_data["result"], result)
    self.assertEqual(cache["result"], result)
```

- [x] **Step 2: 저장 실패 시 이전 상태 보존 실패 테스트 작성**

```python
def test_refresh_keeps_previous_state_when_duckdb_write_fails(self):
    previous = [{"종목명": "기존", "종합점수": 1}]
    with app_module.data_lock:
        app_module.current_data.update(
            result=previous,
            last_updated="2026-07-12 08:00:00",
            status="done",
        )
    with tempfile.TemporaryDirectory() as directory:
        cache_path = Path(directory) / "cache_data.json"
        cache_path.write_text('{"result": ["기존"]}', encoding="utf-8")
        original_cache = cache_path.read_bytes()
        with (
            patch.object(app_module, "CACHE_FILE", str(cache_path)),
            patch.object(app_module, "fetch_all_data", return_value=([], [], [])),
            patch.object(
                app_module,
                "calculate_scores",
                return_value=([{"종목명": "신규", "종합점수": 1}], {"score_3": 0, "score_2": 0, "score_1": 1}),
            ),
            patch.object(
                app_module.stock_db,
                "replace_screening_results",
                side_effect=RuntimeError("duckdb write failed"),
            ),
        ):
            refreshed = app_module.refresh_data()

        self.assertEqual(cache_path.read_bytes(), original_cache)

    self.assertFalse(refreshed)
    self.assertEqual(app_module.current_data["result"], previous)
    self.assertEqual(app_module.current_data["status"], "done")
```

- [x] **Step 3: Flask 대상 테스트가 저장 호출 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_refresh_persists_results_before_publishing_cache tests.test_app.FlaskApiTest.test_refresh_keeps_previous_state_when_duckdb_write_fails -v`

Expected: `replace_screening_results`가 호출되지 않아 FAIL.

- [x] **Step 4: 점수 계산 직후 DuckDB 저장 호출 추가**

```python
result, stats = calculate_scores(turn, supply, nps)
stock_db.replace_screening_results(result)
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
```

기존 `current_data`와 `cache_data.json` 갱신 코드는 이 호출 뒤에 둔다. 기존 캐시 버전 테스트에서는 `replace_screening_results`를 패치하여 실제 기본 DB를 변경하지 않게 한다.

- [x] **Step 5: Flask 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v`

Expected: 모든 `test_app` 테스트 PASS.

- [x] **Step 6: Flask 연동 커밋**

```bash
git add app.py tests/test_app.py
git commit -m "Publish refreshed screening data only after DuckDB persistence"
```

---

### Task 3: GitHub Actions 일일 리포트 영속화

**Files:**
- Modify: `daily_report.py:27-29`
- Modify: `daily_report.py:310-325`
- Modify: `.github/workflows/daily_report.yml`
- Test: `tests/test_daily_report.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: Task 1의 `StockDB.replace_screening_results()`
- Produces: 일일 리포트의 조기 종료 전 DuckDB 저장
- Produces: GitHub Actions 실행 간 `stock_data.duckdb` 캐시 복원·저장

- [x] **Step 1: 2점 종목이 없어도 저장이 먼저 실행되는 실패 테스트 작성**

```python
@patch("daily_report.StockDB")
@patch("daily_report.send_telegram")
@patch(
    "daily_report.calculate_scores",
    return_value=([{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}], {"score_3": 0, "score_2": 0, "score_1": 1}),
)
@patch("daily_report.fetch_all_data", return_value=([], [], []))
def test_persists_screening_results_before_no_high_score_exit(
    self, _fetch, _calculate, _send, stock_db_class
):
    with self.assertRaises(SystemExit) as raised:
        daily_report.main()

    self.assertEqual(raised.exception.code, 0)
    stock_db_class.return_value.replace_screening_results.assert_called_once_with(
        [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}]
    )
```

- [x] **Step 2: DuckDB 저장 실패가 일일 리포트를 실패시키는 테스트 작성**

```python
@patch("daily_report.StockDB")
@patch("daily_report.send_telegram")
@patch(
    "daily_report.calculate_scores",
    return_value=([{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}], {"score_3": 0, "score_2": 0, "score_1": 1}),
)
@patch("daily_report.fetch_all_data", return_value=([], [], []))
def test_aborts_when_screening_results_cannot_be_persisted(
    self, _fetch, _calculate, send_telegram, stock_db_class
):
    stock_db_class.return_value.replace_screening_results.side_effect = RuntimeError(
        "duckdb write failed"
    )

    with self.assertRaises(SystemExit) as raised:
        daily_report.main()

    self.assertEqual(raised.exception.code, 1)
    self.assertIn("DuckDB", send_telegram.call_args.args[0])
```

- [x] **Step 3: 워크플로 DuckDB 캐시 실패 테스트 작성**

```python
def test_daily_report_restores_and_saves_screening_duckdb(self):
    workflow = Path(".github/workflows/daily_report.yml").read_text(encoding="utf-8")

    restore = workflow.index("DuckDB 종합결과 복원")
    report = workflow.index("run: python daily_report.py")
    save = workflow.index("DuckDB 종합결과 저장")
    self.assertGreaterEqual(workflow.count("stock_data.duckdb"), 2)
    self.assertGreaterEqual(workflow.count("screening-db-"), 3)
    self.assertLess(restore, report)
    self.assertLess(report, save)
```

- [x] **Step 4: 일일 리포트와 워크플로 대상 테스트 실패 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_daily_report tests.test_workflow.WorkflowStateCacheTest -v`

Expected: `daily_report.StockDB`와 DuckDB 캐시 단계가 없어 FAIL.

- [x] **Step 5: 일일 리포트 저장 호출과 오류 처리 구현**

```python
from stock_db import StockDB

scored_results, stats = calculate_scores(turn_data, supply_data, nps_data)
logger.info(f"  3점: {stats['score_3']} | 2점: {stats['score_2']} | 1점: {stats['score_1']}")
try:
    saved_count = StockDB().replace_screening_results(scored_results)
except Exception as exc:
    msg = f"종합결과 DuckDB 저장 실패: {exc}"
    logger.error(msg)
    send_telegram(f"❌ <b>국장검색 리포트 실패</b>\n{msg}")
    sys.exit(1)
logger.info("  DuckDB 종합결과: %d개 저장", saved_count)
```

이 블록은 `high_score` 계산과 조기 종료보다 앞에 둔다.

- [x] **Step 6: GitHub Actions DuckDB 캐시 복원·저장 추가**

```yaml
- name: DuckDB 종합결과 복원
  uses: actions/cache/restore@v4
  with:
    path: stock_data.duckdb
    key: screening-db-${{ runner.os }}-${{ github.run_id }}-${{ github.run_attempt }}
    restore-keys: |
      screening-db-${{ runner.os }}-

- name: DuckDB 종합결과 저장
  if: success()
  uses: actions/cache/save@v4
  with:
    path: stock_data.duckdb
    key: screening-db-${{ runner.os }}-${{ github.run_id }}-${{ github.run_attempt }}
```

복원은 Python 실행 전, 저장은 `daily_report.py` 성공 후에 둔다.

- [x] **Step 7: 일일 리포트와 워크플로 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_daily_report tests.test_workflow -v`

Expected: 모든 `test_daily_report`, `test_workflow` 테스트 PASS.

- [x] **Step 8: 일일 자동화 연동 커밋**

```bash
git add daily_report.py .github/workflows/daily_report.yml tests/test_daily_report.py tests/test_workflow.py
git commit -m "Retain daily screening snapshots between report runs"
```

---

### Task 4: 운영 문서와 전체 검증

**Files:**
- Modify: `README.md:136-160`
- Modify: `README.md:176-190`
- Modify: `CLAUDE.md:24-44`
- Modify: `stock_db.py:1-15`

**Interfaces:**
- Consumes: Tasks 1-3의 최종 스키마와 실행 흐름
- Produces: 운영자가 DuckDB 결과 저장과 GitHub Actions 보존 방식을 이해할 수 있는 문서

- [x] **Step 1: 데이터 저장 문서 갱신**

`README.md`에 다음 내용을 명시한다.

```markdown
- `screening_results`: KST 날짜별 종합결과를 저장합니다. 같은 날 다시 갱신하면 해당 날짜의 종목 전체를 교체하며 이전 날짜는 보존합니다.
- GitHub Actions는 `stock_data.duckdb`를 실행 전 캐시에서 복원하고 성공 후 다시 저장합니다. 로컬 DB와 Actions DB는 서로 독립적입니다.
```

`CLAUDE.md`와 `stock_db.py` 모듈 설명의 테이블 수와 책임도 네 테이블 기준으로 갱신한다.

- [x] **Step 2: Ruff 검사 실행**

Run: `uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests`

Expected: `All checks passed!`

- [x] **Step 3: Python 컴파일 검사 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py`

Expected: exit code 0, 출력 없음.

- [x] **Step 4: 전체 회귀 테스트 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v`

Expected: 기존 80개와 새 테스트가 모두 PASS.

- [x] **Step 5: 임시 DuckDB 스모크 검증**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c 'import json, tempfile; from datetime import date; from stock_db import StockDB; p=tempfile.mktemp(suffix=".duckdb"); db=StockDB(p); db.replace_screening_results([{"종목명":"검증종목","종합점수":2,"출처":"연간실적호전, 순매수전환","[턴]PER":"10"}], date(2026,7,13)); row=db.query_table("screening_results")["rows"][0]; assert row["stock_name"]=="검증종목" and row["score"]==2 and json.loads(row["details"])["[턴]PER"]=="10"; print("screening_results smoke: OK")'
```

Expected: `screening_results smoke: OK`

- [x] **Step 6: 최종 변경 검토와 커밋**

```bash
git diff --check
git status --short
git add README.md CLAUDE.md stock_db.py docs/superpowers/plans/2026-07-13-daily-screening-results-duckdb.md
git commit -m "Document durable daily screening snapshots"
```
