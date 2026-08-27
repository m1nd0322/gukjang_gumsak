# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code 에이전트를 위한 개발 지침입니다.

## 프로젝트 개요

`국장검색`은 FnGuide와 Daum Finance 공개 데이터를 수집해 세 가지 기준으로 한국 주식 종목을 점수화하고, 결과를 DuckDB에 저장한 뒤 선택적으로 과거 가격 백테스트를 실행하는 시스템입니다.

프로젝트 언어는 한국어입니다. UI, 변수명, 주석과 모든 사용자 노출 문구는 기존 표현을 우선하고 불필요하게 영어로 바꾸지 않습니다.

## 실행

`uv`가 Python 3.11과 의존성을 격리해 관리하므로 시스템 Python을 직접 사용하지 않습니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python app.py
# 대시보드: http://localhost:5050
# 백테스트: http://localhost:5050/backtest
# DuckDB 뷰어: http://localhost:5050/db
```

`app.py`는 기본적으로 waitress WSGI 서버를 사용하고, waitress를 import할 수 없을 때만 Flask 개발 서버로 대체합니다. 기본 포트는 5050이며 `GUKJANG_PORT`로 바꿀 수 있습니다.

필수 조건은 `uv`와 인터넷 연결입니다. Selenium, Chrome, ChromeDriver는 사용하지 않습니다. KRX 인증 정보(`KRX_ID`, `KRX_PW`)가 없으면 종목 가격과 KOSPI 지수에 yfinance 경로를 사용합니다.

관련 실행 명령:

```bash
# 정적 HTML 리포트
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python stock_screener.py

# macOS LaunchAgent 세 가지 등록: 웹, 07:00 갱신, 일요일 06:00 DB 백업
uv run --managed-python --python 3.11 python scripts/install_daily_refresh_launch_agent.py

# 즉시 DB 백업 또는 로그 회전
uv run --managed-python --python 3.11 python scripts/backup_stock_db.py
uv run --managed-python --python 3.11 python scripts/rotate_logs.py
```

## 아키텍처

### 데이터 흐름

```text
FnGuide 턴어라운드 API + Daum 일별 수급 API
    + FnGuide Snapshot/ShareAnalysis 종목별 조회
    + ticker_map.json / nps_state.json
                    |
                    v
          screening.py: 수집·응답 검증
                    |
                    v
          calculate_scores: 기준별 1점 합산
                    |
       +------------+-------------+
       v                          v
 current_data + cache_data.json  screening_results DuckDB snapshot
       |
       v
 백그라운드 가격 동기화: ticker_map -> pykrx/yfinance -> daily_prices
       |
       v
 StockDB 가격·KOSPI 조회 -> BacktestEngine -> 결과 JSON/CSV
```

스크리닝 소스는 `screening.fetch_all_data(require_all=True)`에서 독립적으로 수집하고 응답 구조를 검증합니다. 필수 소스 하나라도 요청 또는 검증에 실패하면 새 결과를 게시하지 않고 기존 메모리 상태와 캐시를 유지합니다.

### 모듈 책임

- **`app.py`** — Flask 앱, 모든 웹/API route, 인라인 HTML 템플릿, 갱신·백테스트 orchestration, APScheduler를 담당합니다. 로컬 스케줄러는 매일 07:00 KST에 실행하고 실패하면 15분·30분 뒤 같은 날 재시도합니다. 갱신 성공 후 스크리닝 종목 가격을 약 400일 범위로 백그라운드 증분 동기화합니다.
- **`screening.py`** — FnGuide 턴어라운드, Daum 수급, FnGuide Snapshot/ShareAnalysis와 국민연금 상태를 수집·검증하고 `fetch_all_data`와 `calculate_scores`를 제공합니다. 웹, 정적 리포트, GitHub Actions가 이 데이터 계층을 공유합니다.
- **`daily_refresh.py`** — macOS LaunchAgent가 호출하는 독립 진입점입니다. `/api/status/summary`로 최소 상태만 조회하고 `/api/refresh`를 호출한 뒤 완료를 polling합니다. 웹 서버가 없으면 `app.refresh_data()`를 직접 실행합니다.
- **`backtester.py`** — 외부 백테스트 패키지 없이 거래 비용과 FIFO 로트를 반영하는 엔진입니다. `CostConfig`, `Portfolio`, `BacktestEngine`과 여섯 실행 메서드를 제공합니다.
- **`stock_db.py`** — DuckDB를 감싸는 `StockDB`입니다. 가격·티커·지수의 증분 수집, 가격 조회, KST 날짜별 스크리닝 snapshot 교체를 담당합니다. 네트워크 수집은 병렬화하고 DB 연결은 호출마다 새로 만들며 mutation lock으로 파일 쓰기를 직렬화합니다.
- **`nps_tracker.py`** — 국민연금 기준선, 매수 신호 유효기간, 상태 전이와 원자적 상태 저장을 담당합니다.
- **`stock_screener.py`** — 웹 서버와 독립적으로 현재 스크리닝 결과를 `stock_screening_result.html`로 만드는 CLI입니다.
- **`daily_report.py`** — GitHub Actions에서 평일 08:00 KST 스크리닝, 6개월 복합전략 백테스트, Telegram 요약·CSV 전송을 실행합니다.
- **`scripts/install_daily_refresh_launch_agent.py`** — 웹 상시 실행, 07:00 갱신, 일요일 06:00 DB 백업 LaunchAgent를 생성·등록합니다.
- **`scripts/backup_stock_db.py`** — DuckDB와 WAL을 복사하고 핵심 테이블을 열어 검증한 뒤 최근 12개 백업만 유지합니다.
- **`scripts/rotate_logs.py`** — 5MB를 넘은 로그를 최대 3세대(`.1`, `.2`, `.3`)로 회전합니다. `app.py` 시작 시 `web.log`를 회전하고 CLI는 세 로그를 모두 처리합니다.

### 세 가지 스크리닝 기준

| 기준 | 원천 | 코드 라벨 |
|---|---|---|
| 연간 실적 호전/영업이익 흑자전환 | FnGuide `getScrEarTrn` API | `turn` |
| 외국인·기관 순매수 전환 | Daum `investor_purchase` 및 일별 수급 API | `supply` |
| 국민연금 신규·추가 매수 신호 | FnGuide `Snapshot`·`ShareAnalysis` | `nps` |

각 기준은 1점이며 종목별 최대 점수는 3점입니다. 결과는 종합점수 내림차순, 종목명 오름차순으로 정렬하고 모든 기준의 합집합을 포함합니다.

### 주요 API

| 경로 | 메서드 | 용도 |
|---|---|---|
| `/` | GET | 스크리닝 대시보드 |
| `/backtest` | GET | 백테스트 화면 |
| `/api/refresh` | POST | 비동기 데이터 갱신 시작 |
| `/api/status` | GET | 전체 스크리닝 결과와 갱신 상태 |
| `/api/status/summary` | GET | `daily_refresh` 감독용 경량 상태 |
| `/api/backtest/run` | POST | 백테스트 시작 |
| `/api/backtest/status` | GET | 백테스트 진행 상태와 결과 |
| `/api/backtest/csv` | GET | 백테스트 CSV 다운로드 |
| `/db` | GET | DuckDB 뷰어 |
| `/api/db/tables` | GET | 허용 테이블 및 DB 통계 |
| `/api/db/schema/<table_name>` | GET | 테이블 컬럼 조회 |
| `/api/db/query/<table_name>` | GET | 정렬·필터·페이지네이션 조회 |
| `/api/db/ticker-summary` | GET | 종목별 가격 데이터 요약 |

### 동시성

- Flask 갱신과 백테스트는 daemon thread에서 실행합니다.
- `data_lock`은 `current_data`, `bt_lock`은 `backtest_state` 접근을 보호합니다.
- `refresh_lock`은 수동·예약 갱신 중복 실행을 막고 `price_sync_lock`은 가격 동기화를 직렬화합니다.
- `StockDB`는 호출별 DuckDB 연결과 mutation lock을 사용합니다. `StockDB(':memory:')`는 지원하지 않습니다.

## 저장소

`stock_data.duckdb`의 핵심 테이블은 다음 네 개입니다.

| 테이블 | 내용 |
|---|---|
| `daily_prices` | 종목코드·종목명별 일봉 OHLCV |
| `ticker_map` | 종목코드·종목명·시장 매핑 |
| `index_prices` | KOSPI 지수 종가 |
| `screening_results` | KST 날짜별 전체 스크리닝 결과와 상세정보 |

- `cache_data.json`은 서버 재시작 후 대시보드 복구에 사용하는 마지막 스크리닝 캐시입니다.
- `nps_state.json`은 국민연금 신호 상태와 기준선을 저장하며 `nps_state.json.lock/` 디렉터리 잠금을 사용합니다.
- 가격 데이터는 이미 저장된 날짜를 건너뛰는 증분 방식입니다.
- 같은 날짜의 `screening_results`는 트랜잭션으로 전체 교체하고 이전 날짜 이력은 유지합니다.
- `backups/stock_data_YYYYMMDD.duckdb`에는 검증된 일일 DB 사본을 저장하며 기본 최근 12개를 유지합니다.
- DB, 캐시, 백업, 로그와 생성된 CSV/HTML은 `.gitignore` 대상입니다.

## 프론트엔드

HTML과 JavaScript는 `app.py`의 Python raw string 템플릿에 포함되어 있습니다. 백테스트 차트는 CDN의 Chart.js를 사용하며 별도 build step이나 frontend 프로젝트는 없습니다.

## 변경 및 검증 규칙

- 사용자 노출 문구는 한국어 기존 용어를 유지합니다.
- 네트워크에 의존하는 수집 코드는 응답 구조와 필수 필드를 검증하고 실패 시 부분 결과를 게시하지 않습니다.
- DuckDB 변경은 기존 thread-safety 및 트랜잭션 경계를 유지합니다.
- 수집·저장·점수 계산 로직을 수정하면 네트워크 독립 회귀 테스트를 먼저 실행합니다.

```bash
# 전체 회귀 테스트
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v

# 컴파일 검사
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py

# 선택적 Ruff 검사
uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
```

실데이터 연결 검사는 외부 서비스 상태에 좌우되므로 회귀 테스트와 분리합니다. 비밀값(`KRX_ID`, `KRX_PW`, Telegram secret)을 코드나 커밋에 넣지 않습니다.
