# 국장검색 (`gukjang_gumsak`)

FnGuide 공개 데이터를 이용해 한국 주식 종목을 3개 기준으로 점수화하고, 웹 대시보드·백테스트·DuckDB 이력·텔레그램 일일 리포트로 제공하는 Python 애플리케이션입니다.

> 이 프로젝트는 투자 참고 및 소프트웨어 실험용입니다. 데이터의 정확성·완전성을 보장하지 않으며 투자 판단과 결과에 대한 책임은 사용자에게 있습니다.

## 현재 제공 기능

| 실행 경로 | 진입점 | 주요 기능 | 자동 실행 |
| --- | --- | --- | --- |
| 로컬 웹 | `app.py` | 스크리닝 대시보드, 수동 갱신, 5개 백테스트 전략, CSV 다운로드, DuckDB 뷰어 | 매일 08:00 KST |
| 정적 리포트 | `stock_screener.py` | 현재 스크리닝 결과를 단일 HTML 파일로 생성 | 없음 |
| GitHub Actions | `daily_report.py` | 스크리닝, 6개월 복합전략 백테스트, 텔레그램 요약·CSV 전송 | 평일 08:00 KST |

전체 데이터 흐름은 다음과 같습니다.

```text
FnGuide JSON + Snapshot/ShareAnalysis
                ↓
       응답·종목코드·표 구조 검증
                ↓
          3개 기준 점수 계산
                ↓
 DuckDB 스냅샷 저장 → 메모리/JSON 캐시 게시
                ↓
    대시보드 · 백테스트 · 텔레그램
```

스크리닝 소스 하나라도 유효하지 않으면 자동·수동 갱신 전체를 실패로 처리하고 이전 대시보드와 캐시를 유지합니다. 새 결과는 DuckDB에 먼저 저장된 뒤 화면과 JSON 캐시에 게시됩니다.

## 스크리닝 기준

각 기준에 1점을 부여합니다. 웹 백테스트의 대상 점수와 항목은 실행 화면에서 선택하며, GitHub Actions 일일 리포트는 2점 이상 종목을 사용합니다.

| 기준 | 판정 내용 | 데이터 소스 |
| --- | --- | --- |
| 연간실적호전 | 연간 영업이익이 개선된 종목 | FnGuide `TURNAROUND_A.json` |
| 외국인/기관 동반 순매수 전환 | 외국인과 기관이 함께 순매수로 전환한 종목 | FnGuide `SUPPLY_TREND_FIRST_BUY.json` |
| 국민연금 신규/추가매수 | 공개 주요주주 신규·보유량 증가 이벤트 발생일부터 3개월 | FnGuide Snapshot + ShareAnalysis |

구형 `WooriRenewal` HTML 화면이나 Selenium DOM은 사용하지 않습니다. JSON 응답과 Snapshot·ShareAnalysis의 실제 종목코드, 필수 주주 표·행 구조를 검증해 HTTP 200 오류 문서, 깨진 HTML, 우선주에서 보통주로 잘못 연결된 페이지를 결과에서 제외합니다.

### 국민연금 신호 규칙

- 신규매수 또는 추가매수 확인일부터 달력 기준 3개월 동안 1점을 부여합니다. 범위는 `매수일 <= 기준일 < 만료일`이며 만료일에는 신호와 점수가 제거됩니다.
- 같은 종목에서 여러 이벤트가 발생해도 국민연금 점수는 최대 1점입니다. 추가매수가 확인되면 가장 최근 매수일부터 3개월로 유효기간을 다시 계산합니다.
- 매도나 보유주식 수 감소는 기존 유효기간을 연장하지 않습니다. 현재 Snapshot에서 국민연금 보유 행이 사라지면 활성 신호도 제거합니다.
- ShareAnalysis가 검증되지 않은 종목은 Snapshot의 최종변동일만으로 새 매수일을 추론하지 않습니다.
- 최초 실행에서 현재 국민연금 보유 종목 전체를 신규매수로 간주하지 않습니다. 공개 ShareAnalysis에서 확인되는 최근 이벤트만 복원합니다.
- 탐지 범위는 FnGuide 공개 주요주주 화면에 나타나는 이벤트이며 국민연금의 전체 주문 내역을 의미하지 않습니다.

`nps_state.json`은 보유 기준선과 만료 전 신호를 저장합니다. 웹, 정적 리포트, 일일 리포트가 동시에 상태를 변경하지 않도록 `nps_state.json.lock/` 디렉터리 잠금을 사용합니다.

## 빠른 시작

### 요구사항

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- 인터넷 연결

Python, Chrome, ChromeDriver, Selenium을 별도로 설치할 필요가 없습니다. `uv`가 Python 3.11과 격리된 의존성 환경을 관리합니다. 아래 대시보드 실행 명령은 macOS, Linux, Windows PowerShell/CMD에서 동일합니다.

```bash
git clone https://github.com/m1nd0322/gukjang_gumsak.git
cd gukjang_gumsak
uv --version
```

### 대시보드 실행

저장소 루트에서 아래 명령을 **한 줄로** 실행합니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python app.py
```

첫 실행은 Python과 패키지를 내려받기 때문에 시간이 걸릴 수 있습니다. 이후에는 `uv` 다운로드 캐시를 재사용합니다.

| 화면 | 주소 |
| --- | --- |
| 스크리닝 대시보드 | <http://localhost:5000> |
| 백테스트 | <http://localhost:5000/backtest> |
| DuckDB 뷰어 | <http://localhost:5000/db> |

서버는 실행한 터미널에서 `Ctrl+C`로 종료합니다.

### 선택적 KRX 인증

기본 실행은 저장소의 `ticker_map.json`과 yfinance를 사용합니다. KRX 계정이 있다면 다음 환경 변수를 설정해 pykrx를 우선 사용할 수 있으며, KRX 호출이 실패하면 yfinance로 대체합니다.

```bash
export KRX_ID='your-id'
export KRX_PW='your-password'
```

## 자동 갱신 동작

로컬 웹과 GitHub Actions는 서로 독립된 스케줄러입니다.

### 로컬 웹 스케줄러

- `app.py`가 실행 중이면 주말을 포함해 매일 08:00 KST에 갱신합니다.
- Mac이 잠든 상태로 08:00을 지나더라도 앱 프로세스가 살아 있으면, 시스템이 깨어났을 때 놓친 갱신을 한 번 실행합니다.
- 여러 실행 시각이 밀려도 `coalesce=True`로 한 번만 실행하고 `max_instances=1`로 중복 갱신을 막습니다.
- 수동 갱신과 예약 갱신도 같은 잠금을 사용하므로 동시에 실행되지 않습니다.
- 앱 프로세스 자체가 종료되어 있던 시간의 작업은 실행할 수 없습니다. 항상 켜진 자동화가 필요하면 GitHub Actions 경로를 사용하세요.

수동 갱신은 대시보드의 `재조회` 버튼 또는 다음 API로 시작할 수 있습니다.

```bash
curl -X POST http://localhost:5000/api/refresh
curl http://localhost:5000/api/status
```

### GitHub Actions 스케줄러

`.github/workflows/daily_report.yml`은 월~금 08:00 KST에 실행됩니다. 한국 공휴일은 별도로 제외하지 않으며 GitHub의 `workflow_dispatch`로 수동 실행할 수 있습니다.

## 웹 화면과 API

| 메서드 | 경로 | 용도 |
| --- | --- | --- |
| `GET` | `/` | 현재 스크리닝 결과와 점수별 통계 |
| `POST` | `/api/refresh` | 비동기 수동 갱신 시작 |
| `GET` | `/api/status` | 갱신 상태, 마지막 갱신 시각, 현재 결과 |
| `GET` | `/backtest` | 백테스트 설정·결과 화면 |
| `POST` | `/api/backtest/run` | 백테스트 시작 |
| `GET` | `/api/backtest/status` | 백테스트 진행 상태·결과 |
| `GET` | `/api/backtest/csv` | 최근 백테스트 상세 CSV 다운로드 |
| `GET` | `/db` | DuckDB 테이블 뷰어 |
| `GET` | `/api/db/tables` | 허용된 테이블 목록과 DB 통계 |
| `GET` | `/api/db/schema/<table_name>` | 테이블 컬럼 조회 |
| `GET` | `/api/db/query/<table_name>` | 정렬·필터·페이지네이션 조회 |
| `GET` | `/api/db/ticker-summary` | 종목별 가격 데이터 요약 |

갱신과 백테스트는 백그라운드 스레드에서 실행되며, 동일 작업이 이미 실행 중이면 새 요청을 중복 시작하지 않습니다.

## 백테스트

웹 백테스트에서는 종합점수 `3점`, `2점`, `1점`을 동시에 선택할 수 있습니다. 최초 기본값은 기존 동작과 같은 `3점 + 2점`이며, 점수를 하나도 선택하지 않으면 전체 점수를 대상으로 합니다. 선택한 점수 중 하나와 일치하면 점수 조건을 만족합니다.

`연간실적호전`, `순매수전환`, `국민연금 매수` 항목도 동시에 선택할 수 있습니다. 여러 항목을 선택하면 선택한 항목을 **모두 만족하는 종목만** 포함하고, 아무 항목도 선택하지 않으면 항목 제한을 적용하지 않습니다. 종목은 점수 조건과 항목 조건을 모두 만족해야 합니다.

`POST /api/backtest/run`에서도 같은 규칙을 사용합니다. `scores`를 생략하면 기존 호환 기본값 `[3, 2]`, 빈 배열로 보내면 전체 점수이며, `items`를 생략하거나 빈 배열로 보내면 항목 제한이 없습니다.

```json
{
  "period": 6,
  "capital": 100000000,
  "strategy": "equal_weight",
  "scores": [3, 2],
  "items": ["turnaround", "nps"]
}
```

항목 키는 `turnaround`(연간실적호전), `supply`(순매수전환), `nps`(국민연금 매수)입니다. 다음 전략을 지원합니다.

| 전략 키 | 화면 표시 | 동작 |
| --- | --- | --- |
| `equal_weight` | 동일 비중 Buy & Hold | 첫 거래일에 동일 금액으로 매수 후 보유 |
| `rebalance` | 월간 리밸런싱 | 20거래일마다 동일 비중으로 재배분 |
| `vol_trailing_stop` | 변동성 가중 + 트레일링 스탑 | 저변동성 비중 확대와 고점 대비 하락 시 매도 |
| `ma_filter` | 이동평균 필터 | 종가가 MA20보다 높을 때만 보유 |
| `composite` | 복합 전략 | MA 필터, 변동성 가중, 트레일링 스탑 결합 |

웹 화면에서 기간, 초기 자본금, 슬리피지, 매수·매도 수수료, 매도 증권거래세를 설정할 수 있습니다. 가격과 KOSPI 벤치마크는 DuckDB 캐시를 먼저 사용하고 부족한 구간만 pykrx 또는 yfinance로 보충합니다.

엔진은 다음을 반영합니다.

- 매수·매도 슬리피지
- 양방향 거래 수수료
- 매도 시 증권거래세
- FIFO 로트 기반 부분·전량 청산
- 매수 비용부터 반영한 총수익률과 MDD
- KOSPI 벤치마크 비교

> 현재 시점의 스크리닝 결과를 과거 전체 기간에 적용하므로 Look-ahead bias가 있습니다. 전략 간 상대 비교와 시스템 검증 용도로 해석하세요.

## 데이터 저장

### DuckDB

`stock_data.duckdb`에는 다음 테이블이 있습니다.

| 테이블 | 내용 |
| --- | --- |
| `daily_prices` | 종목별 일봉 OHLCV |
| `ticker_map` | 종목코드·종목명·시장 매핑 |
| `index_prices` | KOSPI 지수 종가 |
| `screening_results` | KST 날짜별 전체 스크리닝 결과와 상세정보 |

`screening_results`는 같은 날 다시 갱신하면 해당 날짜 전체를 트랜잭션으로 교체하고 이전 날짜 이력은 유지합니다. DuckDB 저장에 실패하면 새 메모리 상태와 JSON 캐시는 게시하지 않습니다.

### 로컬 파일

| 파일 | 용도 | Git 포함 |
| --- | --- | --- |
| `ticker_map.json` | FnGuide 및 yfinance 종목 매핑의 저장소 기본값 | 포함 |
| `cache_data.json` | 마지막 대시보드 결과와 캐시 버전 | 제외 |
| `nps_state.json` | 국민연금 보유 기준선과 활성 신호 | 제외 |
| `stock_data.duckdb` | 가격·지수·티커·스크리닝 이력 | 제외 |
| `backtest_*.csv` | 백테스트 상세 결과 | 제외 |
| `stock_screening_result.html` | 정적 스크리닝 리포트 | 제외 |

로컬 DuckDB와 GitHub Actions가 캐시하는 DuckDB는 서로 독립된 파일입니다. Actions 캐시는 영구 저장소가 아니므로 장기 이력이 필요하면 별도 백업이 필요합니다.

## 정적 HTML 리포트

웹 서버 없이 현재 스크리닝 결과만 생성하려면 다음을 실행합니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python stock_screener.py
```

결과는 `stock_screening_result.html`에 생성됩니다.

## GitHub Actions 일일 리포트

워크플로는 다음 순서로 동작합니다.

1. 동일 그룹의 예약·수동 실행을 직렬화
2. 이전 `nps_state.json`과 `stock_data.duckdb` 캐시 복원
3. Python 3.11과 의존성 설치
4. 전체 회귀 테스트 실행
5. FnGuide 3개 기준 수집 및 스코어링
6. 전체 결과를 DuckDB에 저장
7. 2점 이상 종목의 6개월 복합전략 백테스트
8. 텔레그램 요약과 CSV 전송
9. 성공한 상태 파일과 DuckDB를 새 캐시 키로 저장
10. 생성된 CSV를 Actions Artifact로 30일 보관

워크플로 제한 시간은 30분이며 Chrome 설치 단계는 없습니다.

### 텔레그램 설정

GitHub 저장소의 `Settings → Secrets and variables → Actions`에 다음 Repository secret을 등록합니다.

| Secret | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 메시지를 받을 개인 또는 그룹 Chat ID |

봇은 텔레그램 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 만들 수 있습니다. 수동 실행은 GitHub의 `Actions → 일일 국장검색 리포트 → Run workflow`에서 시작합니다.

## 테스트와 검사

회귀 테스트는 외부 네트워크 없이 실행됩니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v
```

컴파일 검사:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py
```

선택적 Ruff 검사:

```bash
uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
```

실데이터 소스 연결 확인:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import os; from screening import fetch_all_data; turn, supply, nps = fetch_all_data(require_all=True); print({'turnaround': len(turn), 'supply': len(supply), 'nps_buy_signals': len(nps)}); assert turn and supply and os.path.exists('nps_state.json')"
```

활성 국민연금 신호는 시장 상황과 3개월 유효기간에 따라 0개일 수 있으며 이 경우도 정상입니다.

## 문제 해결

### `unexpected argument '--with-'` 또는 `command not found: requirements`

`--with-requirements`가 줄바꿈으로 `--with-`와 `requirements`로 나뉜 경우입니다. 빠른 시작의 `uv run ...` 명령을 한 줄 전체로 실행하세요.

### macOS `pyexpat`, `libexpat`, `Symbol not found` 오류

시스템 또는 Homebrew Python이 사용된 경우가 많습니다. `python app.py`를 직접 실행하지 말고 `uv run --isolated --managed-python --python 3.11 ...` 명령을 사용하세요.

```bash
uv run --managed-python --python 3.11 python --version
```

출력이 `Python 3.11.x`인지 확인합니다.

### 08:00 자동 갱신 확인

앱 시작 로그의 `다음 자동 갱신` 시각을 확인하세요. 앱 프로세스가 살아 있는 상태의 절전 누락은 깨어난 뒤 한 번 보정되지만, 터미널 종료·재부팅 등으로 앱 프로세스가 종료되어 있었다면 갱신되지 않습니다.

## 프로젝트 구조

```text
app.py                         Flask 웹 UI/API와 로컬 08:00 스케줄러
screening.py                   FnGuide 수집·검증과 공통 점수 계산
nps_tracker.py                 국민연금 신호 상태 전이·만료·원자 저장
backtester.py                  거래비용/FIFO 기반 백테스트 엔진
stock_db.py                    DuckDB 스키마, 캐시, 스크리닝 이력
stock_screener.py              정적 HTML 리포트 CLI
daily_report.py                GitHub Actions 텔레그램 리포트
ticker_map.json                저장소 기본 종목명→종목코드 매핑
tests/                         네트워크 독립 회귀 테스트
requirements.txt               Python 런타임 의존성
.github/workflows/
  daily_report.yml             평일 08:00 KST 일일 리포트
```
