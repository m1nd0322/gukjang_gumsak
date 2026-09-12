# 국장검색 (`gukjang_gumsak`)

FnGuide와 Daum Finance 공개 데이터를 이용해 한국 주식 종목을 3개 기준으로 점수화하고, 웹 대시보드·백테스트·DuckDB 이력·텔레그램 일일 리포트로 제공하는 Python 애플리케이션입니다.

> 이 프로젝트는 투자 참고 및 소프트웨어 실험용입니다. 데이터의 정확성·완전성을 보장하지 않으며 투자 판단과 결과에 대한 책임은 사용자에게 있습니다.

## 현재 제공 기능

| 실행 경로 | 진입점 | 주요 기능 | 자동 실행 |
| --- | --- | --- | --- |
| 로컬 웹 | `app.py` | 스크리닝 대시보드, 수동 갱신, 6개 백테스트 전략, CSV 다운로드, DuckDB 뷰어, 스크리닝 종목 가격 자동 동기화 | 매일 07:00 KST |
| 정적 리포트 | `stock_screener.py` | 현재 스크리닝 결과를 단일 HTML 파일로 생성 | 없음 |
| macOS 예약 작업 | `scripts/backup_stock_db.py` | 데이터베이스 검증 사본 롤링 보관 | 매주 일요일 06:00 KST |
| GitHub Actions | `daily_report.py` | 스크리닝, 6개월 복합전략 백테스트, 텔레그램 요약·CSV 전송 | 평일 08:00 KST |

전체 데이터 흐름은 다음과 같습니다.

```text
FnGuide 턴어라운드 + Daum 일별 수급
          + FnGuide Snapshot/ShareAnalysis
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
| 연간실적호전 | 연간 영업이익이 흑자로 전환된 종목 | FnGuide `Consensus/getScrEarTrn` |
| 외국인/기관 동반 순매수 전환 | 당일은 외국인·기관 모두 순매수이고 직전 거래일은 동시 순매수가 아닌 종목 | Daum `investor_purchase` + `investor/days` |
| 국민연금 신규/추가매수 | 공개 주요주주 신규·보유량 증가 이벤트 발생일부터 3개월 | FnGuide Snapshot + ShareAnalysis |

구형 `WooriRenewal` HTML 화면과 2026-07-29에 종료된 FnGuide `TURNAROUND_A.json`·`SUPPLY_TREND_FIRST_BUY.json`은 사용하지 않습니다. JSON 응답과 Snapshot·ShareAnalysis의 실제 종목코드, 필수 주주 표·행 구조를 검증해 HTTP 200 오류 문서, 깨진 HTML, 우선주에서 보통주로 잘못 연결된 페이지를 결과에서 제외합니다.

Daum 수급은 KOSPI·KOSDAQ의 외국인/기관 순매수 상위 30개 후보를 합친 뒤 종목별 최근 2거래일을 비교합니다. 현재일에 양쪽 순매수량이 양수이고 직전 거래일에는 양쪽이 동시에 양수가 아닌 종목만 선정합니다. `순매수금액(억원)`은 당일 종가×(외국인+기관 순매수량)으로 계산한 추정치입니다.

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

서버는 Flask 개발 서버가 아니라 운영용 WSGI 서버인 `waitress`로 띄웁니다. `waitress`가 없는 환경에서는 자동으로 Flask 개발 서버로 대체됩니다.

| 화면 | 주소 |
| --- | --- |
| 스크리닝 대시보드 | <http://localhost:5050> |
| 백테스트 | <http://localhost:5050/backtest> |
| DuckDB 뷰어 | <http://localhost:5050/db> |

서버는 실행한 터미널에서 `Ctrl+C`로 종료합니다.

macOS의 AirPlay 수신기가 5000 포트를 점유해 `403 Forbidden`을 반환할 수 있으므로 대시보드는 기본적으로 5050 포트를 사용합니다. 다른 포트가 필요하면 실행 전에 `GUKJANG_PORT`를 설정하세요.

### 코드 변경 반영과 서버 재시작

로컬 웹 서버는 실행 시점의 `app.py`와 인라인 JavaScript를 메모리에 올립니다. 따라서 `git pull`로 최신 코드를 받거나 원격 저장소에 새 커밋을 푸시해도 이미 실행 중인 프로세스에는 자동으로 반영되지 않습니다. 코드 업데이트 후에는 기존 서버를 `Ctrl+C`로 종료하고 위의 대시보드 실행 명령으로 다시 시작한 뒤, 백테스트 화면도 새 탭에서 열거나 강력 새로고침하세요.

오래 실행된 서버나 브라우저 탭이 남아 있으면 최신 코드에 수익률 두 자리 포맷이 있어도 이전 화면에서는 긴 소수점 값이 계속 표시될 수 있습니다. 서버 재시작 후 새로 받은 `/backtest` 페이지에서 상세 이력 수익률이 `-9.09%`, `+0.66%`처럼 소수점 둘째 자리까지 보이는지 확인합니다.

### 선택적 KRX 인증

기본 실행은 저장소의 `ticker_map.json`과 yfinance를 사용합니다. KRX 계정이 있다면 다음 환경 변수를 설정해 pykrx를 우선 사용할 수 있으며, KRX 호출이 실패하면 yfinance로 대체합니다.

```bash
export KRX_ID='your-id'
export KRX_PW='your-password'
```

## 자동 갱신 동작

로컬 웹과 GitHub Actions는 서로 독립된 스케줄러입니다.

### 로컬 웹 스케줄러

- `app.py`가 실행 중이면 주말을 포함해 매일 07:00 KST에 갱신합니다.
- Mac이 잠든 상태로 07:00을 지나더라도 앱 프로세스가 살아 있으면, 시스템이 깨어났을 때 놓친 갱신을 한 번 실행합니다.
- 여러 실행 시각이 밀려도 `coalesce=True`로 한 번만 실행하고 `max_instances=1`로 중복 갱신을 막습니다.
- 예약 갱신이 실패하면 15분 뒤, 다시 실패하면 30분 뒤에 같은 날 안에서 재시도합니다. 재시도 전에 수동 갱신 등으로 데이터가 새로워졌다면 건너뜁니다.
- 수동 갱신과 예약 갱신도 같은 잠금을 사용하므로 동시에 실행되지 않습니다.
- 대시보드의 `마지막 갱신`은 가장 최근 성공한 갱신(수동·자동)을 표시하고, 자동 갱신 시각은 `자동 갱신`으로 별도로 표시합니다. 따라서 07:00 자동 갱신 뒤 수동 재조회를 실행해도 자동 갱신 시각을 확인할 수 있습니다.
- `app.py`가 꺼져 있어도 갱신하려면 아래 macOS 예약 작업을 한 번 등록합니다.

```bash
uv run --managed-python --python 3.11 python scripts/install_daily_refresh_launch_agent.py
```

설치 스크립트는 웹 서버 상시 실행, 일일 갱신, 데이터베이스 백업 세 가지 LaunchAgent를 함께 등록합니다. 일일 작업은 로그인할 때 누락 여부를 점검하고 매일 07:00에 `daily_refresh.py`를 실행합니다. 대시보드가 실행 중이면 `/api/refresh`를 호출하고 갱신이 끝날 때까지 상태를 확인한 뒤, 오늘 데이터가 준비될 때까지 최대 3회까지 재요청합니다. 실행 중이 아니면 독립 프로세스에서 같은 횟수만큼 갱신합니다. Mac이 잠들어 있던 경우에는 깨어난 직후 한 번 실행됩니다.

```bash
launchctl print "gui/$(id -u)/com.songhear.gukjang-gumsak.daily-refresh"
launchctl print "gui/$(id -u)/com.songhear.gukjang-gumsak.web"
launchctl print "gui/$(id -u)/com.songhear.gukjang-gumsak.db-backup"
tail -f .omx/logs/daily-refresh.log
tail -f .omx/logs/web.log
tail -f .omx/logs/db-backup.log
```

launchd가 계속 덧붙이는 구조라 로그는 무한히 커질 수 있습니다. 서버를 시작할 때 `web.log`가 5MB를 넘으면 `web.log.1`, `web.log.2` 식으로 최대 3세대까지 밀어냅니다. 전체 로그(`daily-refresh.log`, `db-backup.log` 포함)를 즉시 회전하려면 다음을 실행합니다.

```bash
uv run --managed-python --python 3.11 python scripts/rotate_logs.py
```

수동 갱신은 대시보드의 `재조회` 버튼 또는 다음 API로 시작할 수 있습니다.

```bash
curl -X POST http://localhost:5050/api/refresh
curl http://localhost:5050/api/status
```

### 가격 자동 동기화

갱신이 성공하면 그날 종합결과에 포함된 종목의 일봉을 백그라운드에서 증분 수집해 `stock_data.duckdb`에 채워 넣습니다. 서버를 다시 띄울 때도 캐시가 있으면 같은 작업이 한 번 실행됩니다. 덕분에 백테스트를 실행할 때 가격 수집이 거의 필요 없어 바로 시작됩니다.

- 소급 범위는 약 13개월(400일)이며, 이미 채워진 날짜는 API 호출 없이 건너뜁니다.
- 기본값은 켜짐입니다. 끄려면 서버 실행 전에 `GUKJANG_DAILY_PRICE_SYNC=0`으로 설정하세요.

### 데이터베이스 백업

`stock_data.duckdb`는 단일 파일이라 손상되면 전체 가격 이력을 잃습니다. 설치 스크립트가 함께 등록하는 백업 LaunchAgent는 매주 일요일 06:00에 사본을 만듭니다. 로그인할 때도 그날 사본이 없으면 하나를 보충하므로 주말 내내 꺼 둔 Mac이라도 다음 로그인 직후 백업됩니다.

- 사본은 `backups/stock_data_YYYYMMDD.duckdb` 이름으로 저장하고 최근 12개만 유지합니다(기본값, `--keep`으로 조정).
- 복사 후 사본을 직접 열어 WAL을 반영하고 핵심 테이블 조회로 무결성을 검증합니다. 검증에 실패하면 사본을 버리고 재시도합니다.
- 같은 날짜의 백업이 이미 있으면 건너뛰고, 원본이 없으면 실패로 종료합니다.
- 즉시 만들려면: `uv run --managed-python --python 3.11 python scripts/backup_stock_db.py`

### DuckBoard 뷰어

내장 DuckDB 뷰어는 `/db`에서 사용할 수 있습니다. 별도 Rust 뷰어인 [DuckBoard](https://github.com/m1nd0322/db-viewer)를 사용할 때는 snapshot을 지원하는 최신 버전을 사용하세요.

- DuckBoard는 DuckDB를 import할 때 원본을 읽기 전용으로 `EXPORT DATABASE`한 뒤 프로세스 전용 임시 snapshot으로 `IMPORT DATABASE`합니다. 따라서 웹 앱이 `stock_data.duckdb`를 갱신하는 동안 원본 파일 잠금을 차지하지 않습니다.
- snapshot은 import한 시점의 데이터입니다. 웹 갱신 후 최신 결과를 보려면 DuckBoard를 재시작하거나 해당 DB를 다시 import하세요.
- snapshot을 지원하지 않는 구버전 DuckBoard는 live `stock_data.duckdb`를 직접 열어 웹 갱신을 막을 수 있으므로, 재조회 전에 종료하거나 검증된 `backups/` 사본을 열어야 합니다.

### GitHub Actions 스케줄러

`.github/workflows/daily_report.yml`은 월~금 08:00 KST에 실행됩니다. 한국 공휴일은 별도로 제외하지 않으며 GitHub의 `workflow_dispatch`로 수동 실행할 수 있습니다.

## 웹 화면과 API

| 메서드 | 경로 | 용도 |
| --- | --- | --- |
| `GET` | `/` | 현재 스크리닝 결과와 점수별 통계 |
| `POST` | `/api/refresh` | 비동기 수동 갱신 시작 |
| `GET` | `/api/status` | 갱신 상태, 마지막 갱신 시각, 현재 결과 |
| `GET` | `/api/status/summary` | 갱신 감독용 경량 상태 (결과 데이터 제외) |
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
  "strategy": "vol_trailing_stop_loss",
  "stop_loss": 7,
  "scores": [3, 2],
  "items": ["turnaround", "nps"]
}
```

항목 키는 `turnaround`(연간실적호전), `supply`(순매수전환), `nps`(국민연금 매수)입니다. 다음 전략을 지원합니다.

| 전략 키 | 화면 표시 | 동작 |
| --- | --- | --- |
| `equal_weight` | 동일 비중 Buy & Hold | 첫 거래일에 가격이 있는 종목에 한해 동일 금액으로 매수 후 보유 |
| `rebalance` | 월간 리밸런싱 | 20거래일마다 동일 비중으로 재배분 |
| `vol_trailing_stop` | 변동성 가중 + 트레일링 스탑 | 저변동성 비중 확대와 고점 대비 하락 시 매도 |
| `vol_trailing_stop_loss` | 변동성 가중 + 트레일링 스탑 + 스탑로스 | 저변동성 비중 확대, 최고가 대비 10% 하락 또는 평균 체결가 대비 설정 손실률 도달 시 매도 |
| `ma_filter` | 이동평균 필터 | 종가가 MA20보다 높을 때만 보유 |
| `composite` | 복합 전략 | MA 필터, 변동성 가중, 트레일링 스탑 결합 |

`vol_trailing_stop_loss`의 스탑로스 기본값은 7%이며 웹과 API에서 0.1%~50% 범위로 변경할 수 있습니다. 기존 트레일링 스탑은 보유 중 최고 종가 대비 10% 하락을 추적하고, 새 스탑로스는 매수 슬리피지를 포함한 실제 평균 체결가 대비 손실을 제한합니다. 둘 중 하나가 충족되면 전량 매도하며 5거래일 쿨다운 뒤 재진입할 수 있습니다.

웹 화면에서 기간, 초기 자본금, 스탑로스, 슬리피지, 매수·매도 수수료, 매도 증권거래세를 설정할 수 있습니다. 가격과 KOSPI 벤치마크는 DuckDB 캐시를 먼저 사용하고 부족한 구간만 pykrx 또는 yfinance로 보충합니다.

엔진은 다음을 반영합니다.

- 매수·매도 슬리피지
- 양방향 거래 수수료
- 매도 시 증권거래세
- FIFO 로트 기반 부분·전량 청산
- 매수 비용부터 반영한 총수익률과 MDD
- KOSPI 벤치마크 비교 (시작일 이전 마지막 종가를 기준으로 포트폴리오와 같은 기간을 측정)

### 결과 표와 CSV

백테스트 결과의 `전략 종목별 손익` 표는 원가격 등락률이 아니라 실제 전략 거래에서 발생한 종목별 실현손익과 보유 포지션 평가손익을 합산해 보여줍니다. `종목별 매수/매도 상세 이력`에서는 매수가·수량·누적 매입금액·평가금액·매도비용·실현손익·상태를 거래별로 확인하고 종목, 상태, 손익, 기간 조건으로 필터링할 수 있습니다.

상세 이력의 수익률은 셋째 자리에서 반올림해 항상 소수점 둘째 자리까지 표시합니다. 웹 화면은 `+12.35%`, `-7.11%`, `+1.20%`, `+0.00%` 형식이며, CSV의 `수익률(%)` 열은 각각 `12.35`, `-7.11`, `1.20`, `0.00`으로 기록합니다. `-0.0` 또는 반올림 결과가 0인 작은 음수도 화면에서는 `+0.00%`, CSV에서는 `0.00`으로 정규화하며, 백테스트의 원본 숫자와 계산 정밀도는 변경하지 않습니다.

이 표시 규칙은 서버가 제공하는 백테스트 페이지의 JavaScript 포맷터에 적용됩니다. 최신 코드를 받아도 화면에 긴 소수점 값이 남아 있으면 [코드 변경 반영과 서버 재시작](#코드-변경-반영과-서버-재시작) 절차를 먼저 수행합니다.

> 현재 시점의 스크리닝 결과를 과거 전체 기간에 적용하므로 Look-ahead bias가 있습니다. 전략 간 상대 비교와 시스템 검증 용도로 해석하세요.

## 데이터 저장

### DuckDB

`stock_data.duckdb`에는 다음 테이블이 있습니다.

| 테이블 | 내용 |
| --- | --- |
| `daily_prices` | 종목코드·종목명별 일봉 OHLCV |
| `ticker_map` | 종목코드·종목명·시장 매핑 |
| `index_prices` | KOSPI 지수 종가 |
| `screening_results` | KST 날짜별 전체 스크리닝 결과와 상세정보 |

`daily_prices.name`은 `ticker_map.name`을 기준으로 자동 동기화됩니다. 기존 DB는 앱 시작 시 종목명이 소급 반영되고, 매핑이 없는 티커만 `NULL`로 유지되며 이후 매핑 적재 시 자동으로 보완됩니다.

`screening_results`는 같은 날 다시 갱신하면 해당 날짜 전체를 트랜잭션으로 교체하고 이전 날짜 이력은 유지합니다. DuckDB 저장에 실패하면 새 메모리 상태와 JSON 캐시는 게시하지 않습니다.

### 로컬 파일

| 파일 | 용도 | Git 포함 |
| --- | --- | --- |
| `ticker_map.json` | 스크리닝·가격 조회용 종목명→종목코드 매핑 | 포함 |
| `cache_data.json` | 마지막 대시보드 결과와 캐시 버전 | 제외 |
| `nps_state.json` | 국민연금 보유 기준선과 활성 신호 | 제외 |
| `stock_data.duckdb` | 가격·지수·티커·스크리닝 이력 | 제외 |
| `backups/` | `stock_data_YYYYMMDD.duckdb` 검증 사본 (최근 12개 유지) | 제외 |
| `backtest_*.csv` | 백테스트 상세 결과 | 제외 |
| `stock_screening_result.html` | 정적 스크리닝 리포트 | 제외 |

로컬 DuckDB와 GitHub Actions가 캐시하는 DuckDB는 서로 독립된 파일입니다. Actions 캐시는 영구 저장소가 아니므로 장기 이력이 필요하면 별도 백업이 필요합니다. 로컬은 위의 [데이터베이스 백업](#데이터베이스-백업) 절차가 대신합니다.

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
5. FnGuide·Daum 3개 기준 수집 및 스코어링
6. 전체 결과를 DuckDB에 저장
7. 2점 이상 종목의 6개월 복합전략 백테스트
8. 텔레그램 요약과 CSV 전송
9. 성공한 상태 파일과 DuckDB를 새 캐시 키로 저장
10. 생성된 CSV를 Actions Artifact로 30일 보관

GitHub Actions 일일 리포트는 웹의 새 스탑로스 전략과 무관하게 기존 6개월 복합전략을 계속 사용합니다.

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

### 07:00 자동 갱신 확인

앱 실행 중에는 시작 로그의 `다음 자동 갱신` 시각을 확인하세요. macOS 예약 작업을 등록했다면 위의 `launchctl print`에서 `Hour = 7`, `Minute = 0`과 최근 `last exit code`를 확인하고 `.omx/logs/daily-refresh.log`에서 실행 결과를 확인합니다.

### 데이터베이스 백업 확인

`launchctl print "gui/$(id -u)/com.songhear.gukjang-gumsak.db-backup"`에서 `Weekday = 0`, `Hour = 6`, `Minute = 0`과 최근 `last exit code`를 확인하고 `.omx/logs/db-backup.log`와 `backups/` 디렉터리에서 결과를 확인합니다. 백업이 실패하면 같은 날짜라도 사본이 남지 않으므로 로그의 오류 메시지를 먼저 봅니다.

### 백테스트 수익률이 긴 소수점으로 표시됨

실행 중인 서버가 코드 업데이트 전 프로세스이거나 브라우저가 이전 페이지 JavaScript를 유지한 상태입니다. 서버를 `Ctrl+C`로 완전히 종료한 후 빠른 시작 명령으로 다시 실행하고 `/backtest`를 새 탭에서 여세요. 재시작한 서버의 상세 이력은 수익률을 셋째 자리에서 반올림해 항상 소수점 둘째 자리까지 표시합니다.

## 프로젝트 구조

```text
app.py                         Flask 웹 UI/API와 로컬 07:00 스케줄러
daily_refresh.py               서버 유무와 당일 07:00 갱신 여부를 확인하는 예약 진입점
scripts/install_daily_refresh_launch_agent.py  macOS 갱신·웹·백업 LaunchAgent 설치
scripts/backup_stock_db.py     DuckDB 사본 생성·검증·롤링 정리
scripts/rotate_logs.py         5MB를 넘은 로그를 세대 교체로 회전
screening.py                   FnGuide·Daum 수집·검증과 공통 점수 계산
nps_tracker.py                 국민연금 신호 상태 전이·만료·원자 저장
backtester.py                  거래비용/FIFO 기반 백테스트 엔진
strategy_catalog.py            기존·ETF 전략과 고정 ETF 유니버스 카탈로그
adaptive_strategies.py         네 가지 적응형 ETF 배분 정책
drawdown_guard.py              8% 감속·10% 현금화·단계 재진입 제어기
strategy_runner.py              전략별 엔진 실행 분기
strategy_export.py              적응형 결과 CSV 공통 출력
stock_db.py                    DuckDB 스키마, 캐시, 스크리닝 이력
stock_screener.py              정적 HTML 리포트 CLI
daily_report.py                GitHub Actions 텔레그램 리포트
scripts/evaluate_adaptive_strategies.py  ETF 전체기간·표본외·충격구간 평가
ticker_map.json                저장소 기본 종목명→종목코드 매핑
tests/                         네트워크 독립 회귀 테스트
requirements.txt               Python 런타임 의존성
.github/workflows/
  daily_report.yml             평일 08:00 KST 일일 리포트
```

### ETF 적응형 자산배분 (실험)

기존 6개 전략에 `defensive_dual_momentum`, `multi_asset_trend_rotation`,
`trend_risk_parity`, `price_regime_ensemble`을 추가했습니다.
고정 유니버스는 `069500`, `143850`, `133690`, `148070`, `153130`,
`132030`, `261240`, `130680`이며 메타데이터는 `strategy_catalog.py`에서 관리합니다.

ETF 전략은 개별주 필터를 사용하지 않습니다. API 요청에 `scores` 또는 `items`가
포함되면 거절합니다. 요청 시작일보다 400일 앞선 조정가격을 수집하며,
모든 ETF에 최소 253거래일의 사전 종가가 필요합니다.
월말까지 관측한 신호는 다음 달 첫 거래일 시가에 실행하며 최초 진입은
요청 구간 첫 거래일입니다. 당일 종가로 평가하고, 시가가 없으면 주문을 보류합니다.
연속 5거래일 가격 누락은 실행 오류입니다.

전일 평가액 기준 8% 낙폭에서 위험자산 비중을 절반으로 줄이고,
10%에서 모든 ETF를 현금화합니다. 최소 20거래일 이후 중립 이상 레짐에서
25%로 재진입하고, 5거래일마다 25%씩 늘립니다. 재진입의 신규 저점 판정은
체결비용을 차감한 회복 기준액을 사용하며 완전 복귀 시 방어 기준 고점을 재설정합니다.
전체 기간 MDD는 최초 자본과 전체 고점 기준으로 별도 계산합니다.

ETF 기본 비용은 편도 수수료 0.015%, 슬리피지 0.10%, 증권거래세 0%입니다.
보유기간과세를 제외한 세전 성과(`tax_model=etf_pre_tax`)입니다.
갭 하락과 거래비용 때문에 실제 낙폭이 10%를 넘을 수 있습니다.
화면과 CSV에서 레짐, 목표·실제 비중, 현금화·재진입 이력과 초과폭을 확인할 수 있습니다.
과거 성과는 미래 성과를 보장하지 않습니다.

재현 가능한 전체기간·마지막 30% 표본외·36개월 롤링·충격구간 평가는 다음과 같습니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python scripts/evaluate_adaptive_strategies.py
```

`reports/adaptive_evaluation.json`에 결과 또는 데이터 수집 오류를 저장합니다.
성과 기준을 통과한 현재 평가 결과도 표본외 구간의 독립성 한계 때문에
신규 전략은 화면에서 `실험`으로 표시합니다.

2026-09-05 평가에서는 첫 70% 학습 구간에서 공통 위험예산 후보를 비교해
`0.5`를 고정했습니다. 학습 구간에서 `1.0`은 2개, `0.5`는 4개 전략이
통과했으며, 고정 예산을 마지막 30% 표본외 구간에 적용한 결과도 4개 전략이
통과했습니다. 남는 비중은 현금이며, 방어 상태의 감속은 이 기본 위험비중에
추가로 적용합니다. 상세 결과는
[평가 보고서](reports/adaptive_evaluation.md)에 있습니다.
표본외 구간에는 예산 선택 결과를 반영하지 않고 고정된 값을 사용했습니다.
## 생존편향 검증 데이터와 실행 조건

### 2026-09-12 인증 수집 후속 결과

**현재 판정: 생존편향 완전 검증 미완료.** 상장일은 1,434개 종목 모두 확보했고 상장폐지 종목은 266개지만, 실제 청산금액, 전체 기업행동을 반영한 수정주가, 과거 선정 규칙의 근거와 동일 조건의 비교 검증은 아직 필요합니다. 공식 시세 수집과 자동 후속 대조는 별도로 진행 중이며, 중간 대조 결과를 최종 검증 결과로 해석하면 안 됩니다.

저장소에는 재현용 스크립트, 최종 메타데이터 CSV, 입력 템플릿과 검증 보고서를 보관합니다. 대용량 KRX/KIND 원본 응답, 시세 원본, 공시 다운로드와 실행 로그는 로컬 생성물로 제외합니다. 보고서에 기록된 원본 경로와 해시를 확인하려면 해당 원본을 별도로 수집해야 합니다. 로그인 계정 파일과 세션 정보는 저장소에 포함하지 않습니다.

공식 시세의 연도별 수집과 원천가격 대조는 다음 명령으로 재개할 수 있습니다.
첫 명령은 기간 내 상장폐지 ETF 256종목을 대상으로 하며, 날짜별 범위가 같은
원본 파일은 재사용합니다. HTTP 오류나 인증 실패 시 무한 재시도하지 않습니다.

```bash
python scripts/sync_krx_official_prices.py --workers 2
python scripts/reconcile_krx_official_prices.py
```

수집 결과는 `data/krx/official_prices/year_manifest.json`, 대조 결과는
`reports/krx_price_reconciliation.json`에 기록합니다. `all_years_nonempty`는
요청한 연도별 응답에 데이터가 있다는 뜻이며, 모든 거래일의 완전성이나
수정주가 검증을 의미하지 않습니다. 정규화된 원천가격은 `normalized_raw/`에
별도로 저장하며 기존 vendor 가격을 덮어쓰지 않습니다.

운용사 공지에서 확인한 `322120`의 2023년 220원, 2024년 225원 분배금과
분배락일은 `data/krx/disclosures/ace/322120_distribution_evidence.json`에
보관합니다. 이 두 공지로 전체 존속기간의 기업행사 이력이나 실제 청산금액이
입증되는 것은 아닙니다. 실제 지급 확인과 예정 지급일 역시 구분합니다.

후속 정합성 감사는 아래 명령으로 재현합니다. 종료코드 1은 미확보 증빙으로
완전 검증을 차단했다는 의미입니다. 이 감사 자체는 완전 검증 인증기가 아닙니다.

```bash
python scripts/audit_krx_pit_data.py --start 2010-01-01 --end 2026-09-11
```

해당 범위에 겹치는 종목은 1,424개, 상장폐지 종목은 256개입니다.
상장폐지 256종목 모두 vendor 가격 파일이 있지만, 각 파일에 OHLC 관계,
비양수 가격 등 최소 한 가지 점검 항목이 발견되어 원천자료 대조가 필요합니다.
상장일 증빙 파일의 SHA256 불일치는 0건이며, 해시 일치는 내용의 정확성이나
역대 ETF 목록의 완전성을 보증하지 않습니다. 가격이 전혀 없던 4종목은
이 평가 범위 이전에 상장폐지됐습니다. 초기 보유자산이 없는 평가를 전제로 합니다.

종목별 필요 증빙은 `reports/krx_pit_evidence_worklist.csv`, 상세 감사 결과는
`reports/krx_pit_validation.json`에 기록합니다. KRX 장기 시세는 한 번의 조회가
HTTP 400을 반환했으나 연도별 조회는 성공해, `322120`의 2019~2024년 공식
시세 1,301건을 `data/krx/official_prices/`에 보관했습니다. 이는 수정주가나
실제 청산금액 검증 완료를 의미하지 않습니다. 상장폐지 예정 일정 공시 역시
실제 지급 확인과 구분하여 `data/krx/disclosures/`에 보관합니다.

사후 정의한 규칙도 과거 시점 정보만 사용하여 생존편향을 통제할 수 있지만,
그것이 과거 사전등록이나 전략 선택 과적합의 부재를 증명하지는 않습니다.
최종 비교는 동일 평가기간, 비용, 고정 파라미터로 실행해야 합니다.

KRX 계정 인증 후 현재 ETF 기본정보 1,168건을 내려받아 상장일 599건을
보완했고, KIND에서 남은 상장폐지 종목 94건의 상장일을 추가 확보했습니다.
수집 목록 기준 총 1,434종목(상장폐지 266종목)의 상장일이 모두 확보됐으며,
`data/krx/etf_inventory_reconciled.csv`를 inventory-only로 import한 결과는
`data/krx/etf_listing_registry_complete.csv`입니다. 상장일 누락은 0건입니다.
이는 거래소의 역대 모든 ETF가 빠짐없이 포함됐다는 독립적인 증명이나
생존편향 완전 검증을 뜻하지 않습니다. 수정주가, 실제 청산금액과 지급일,
과거 종목 선정 근거는 여전히 추가 검증이 필요합니다.

인증 수집은 `scripts/sync_krx_authenticated_master.py`로 재실행할 수 있습니다.
계정 파일 기본 경로는 `/Users/songhear/APIs/krx_login_account.txt`이며,
계정과 비밀번호를 순서대로 두 줄에서 읽습니다. 계정값과 세션 쿠키는
저장하거나 출력하지 않습니다. KRX 단일 세션 정책으로 재로그인 시 기존
세션이 종료될 수 있으며, 인증 실패 시 반복 시도하지 않습니다.
`--cached`는 저장된 기본정보를 사용하므로 로그인하지 않습니다.
이 스크립트의 기본 출력은 현재 종목 정보 병합본이며, 최종 KIND 보완본과
구분됩니다. 아래의 741건/693건 수치는 인증 수집 이전 기록입니다.

후속 증거: `reports/krx_authenticated_metadata.json`,
`reports/krx_kind_followup.json`, `reports/krx_listing_quarantine_complete.json`.
대상 코드 테스트는 API 테스트를 포함해 35건 통과했으며,
`reports/krx_pit_tests_20260912.xml`에 이전 실행 결과가 있습니다.

ETF 백테스트의 생존편향을 분리해 검증하는 경로를 준비했다.

- `data/etf_universe_history.csv`: 날짜별 ETF listing 계약이다. `valid_from`, `valid_to`, `lifecycle_event`, `successor_ticker`로 상장·상장폐지·승계 이력을 표현한다.
- `etf_universe.py`: 유니버스 중복 기간, 잘못된 날짜, 승계 필드 누락을 검증하고 리밸런싱 날짜의 활성 종목을 계산한다.
- `scripts/import_krx_etf_universe.py`: KRX ETF 검색 결과를 검토 가능한 정규화 CSV로 변환한다. KRX 검색에서 상장폐지종목 포함 옵션으로 내보낸 자료를 입력해야 한다.
- `scripts/validate_etf_universe.py`: 정규화 파일의 무결성과 `coverage` 상태를 검사한다.
- `scripts/evaluate_adaptive_strategies.py`: `--universe-mode survivor|point_in_time|both`를 지원한다. `both`는 `universe_comparison.survivor_only`와 `universe_comparison.point_in_time`을 같은 보고서에 기록한다.
- `backtester.py`: PIT 평가의 `verified` 청산 정책은 상장폐지 보유분을 매매 불가능한 미수금으로 옮긴다. 공시된 금액의 확인일에 평가금액을 갱신하고 실제 지급일 종가 시점에 현금으로 전환한다. 시장 매도 수수료를 추가 부과하지 않는다. 기존 `cash` 정책은 마지막 종가를 사용하는 추정 실험이며 완전 검증에 사용하지 않는다.

2026-09-11 수집 결과: KRX 목록 1,434종목 중 상장폐지 266종목을 확인했다. KIND 상품개요로 상장일 741개(폐지 종목 172개)를 확보해 `data/krx/etf_listing_registry.csv`에 import했다. 미확보 693개는 `reports/krx_listing_quarantine.json`에 남아 있다. 일부 KIND 요청에서 HTTP 403이 관찰되어 재시도를 중단했다. 상장폐지 종목 262개의 네이버 OHLCV도 확보했지만 분배금·분할 반영을 검증한 수정주가는 아니다. 원본과 SHA-256을 각각 `data/krx/kind_details/`, `data/krx/vendor_prices/`에 보관한다.

메타데이터 import는 `coverage=inventory_only`로 분리한다. 전략용 import는 명시적인 자산 분류·역할·비중 상한과 당시 공개 시점 `known_from`이 필요하다. 현재 상품명으로 과거 역할을 자동 추정하지 않는다. 기본 전략 CSV는 계속 `survivor_only`이다.

```bash
python3 scripts/sync_krx_etf_details.py --offline
python3 scripts/import_krx_etf_universe.py \
  --input data/krx/etf_inventory_enriched.csv \
  --output data/krx/etf_listing_registry.csv --inventory-only \
  --quarantine-output reports/krx_listing_quarantine.json
python3 scripts/audit_krx_pit_data.py
```

수집 재개는 `sync_krx_etf_details.py`에서 `--offline`을 제거한다. 미확보 종목이 있으면 metadata import는 확보분만 보관하고 종료 코드 1로 불완전 상태를 알린다. 전략 import는 누락 종목이 있으면 결과를 쓰지 않는다.

`--selection-policy oldest_listing_v1`은 이전 종가까지 253개 관측값과 공개 근거가 있는 후보 중 각 논리 자산군의 최초 상장 종목을 선택하고 동률은 종목코드 순으로 결정한다. 월 첫 거래일에 리밸런싱하며 조건을 충족하지 못한 자산군은 현금으로 남긴다. 이 규칙은 2026-09-11에 정의한 사후 재구성 연구 규칙이다. 과거에 사전 등록된 전략이었다고 주장하지 않는다. 규칙 원문은 `data/krx/selection_policy_v1.json`에 있다.

전략 매핑은 `data/krx/strategy_mapping_template.csv`, 실제 상환은 `data/krx/settlements_template.csv` 형식을 사용해 importer의 `--mapping`, `--settlements`로 병합한다. 상환에는 순지급액, 지급일, 금액 확인일, 원문 출처와 수정주가 단위 변환계수 모두가 필요하다. 계수는 입력 수정주가/원주가 단위의 비율이며 추정값을 채워 넣지 않는다. 합병의 현물 교환은 별도 근거가 없으면 현금 상환으로 가정하지 않는다.

PIT 평가에는 `--price-evidence` JSON도 필요하다. `tickers.<ticker>`마다 `adjustment_verified: true`, `source`, `corporate_actions_source`, `prices_sha256`을 요구한다. 해시는 `pit_price_evidence.price_digest()`로 실제 입력 행을 해시하며, 분배금·분할의 완전한 검증 근거가 확보된 가격에만 사용한다. 원시 OHLCV를 다운로드했다는 이유로 검증 완료 표시를 하지 않는다.

원본 출처는 [KRX ETF 데이터 검색](https://data.krx.co.kr/comm/finder/finder_dataetfisu.jsp), [KIND ETF 상품개요](https://kind.krx.co.kr/disclosure/etfisudetail.do?method=searchEtfIsuSummary&strIsurCd=22681)다. 상환은 상품별 발행사 공시의 실제 금액·지급일을 사용해야 하며, [KIND의 해지 안내 사례](https://kind.krx.co.kr/external/2025/02/28/000974/20250228002264/68210.htm)처럼 상장폐지일과 지급일이 다를 수 있다. 현재 미해결 항목은 `reports/krx_pit_validation.json`에 기록하고 PIT 결과는 `blocked`로 유지한다.
