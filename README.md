# 국장검색 (gukjang_gumsak)

한국 증시 종합 스크리닝, 백테스트, 일일 텔레그램 리포트를 한 저장소에서 실행하는 Python 애플리케이션입니다.

## 주요 기능

FnGuide의 세 가지 지표에 각각 1점을 부여하고, 2점 이상 종목을 백테스트 대상으로 사용합니다.

| 기준 | 설명 | 현재 데이터 경로 |
| --- | --- | --- |
| 연간실적호전 | 연간 영업이익이 개선된 종목 | FnGuide `TURNAROUND_A.json` |
| 외국인/기관 동반 순매수 전환 | 외국인과 기관이 함께 순매수로 전환한 종목 | FnGuide `SUPPLY_TREND_FIRST_BUY.json` |
| 국민연금 신규/추가매수 | 공개 주요주주 신규·보유량 증가 이벤트 발생일부터 3개월 | FnGuide Snapshot + ShareAnalysis |

구형 `WooriRenewal` HTML 화면이나 Selenium DOM에 의존하지 않습니다. JSON 응답 구조와 Snapshot·ShareAnalysis의 실제 종목코드, 필수 주주 표·행 구조를 검증하므로 HTTP 200 오류 문서, 깨진 HTML, 우선주→보통주 페이지 연결을 결과에서 걸러냅니다.

### 국민연금 매수 신호

- 신규매수 또는 추가매수가 확인된 날부터 달력 기준 3개월 동안 1점을 부여합니다. `매수일 <= 기준일 < 만료일`이며 만료일에는 카테고리와 해당 1점이 제거됩니다.
- 같은 종목에 매수 이벤트가 여러 번 있어도 국민연금 점수는 최대 1점입니다. 추가매수가 확인되면 가장 최근 매수일부터 3개월로 유효기간을 다시 계산합니다.
- 매도나 보유주식 수 감소는 기존 유효기간을 갱신하지 않습니다. 현재 Snapshot에서 국민연금 보유 행이 사라지면 활성 신호도 즉시 제거합니다.
- ShareAnalysis를 검증하지 못한 종목은 Snapshot의 최종변동일만으로 새 매수일을 추론하지 않습니다. 매수 후 매도가 이어진 경우 매도일을 추가매수일로 오인하지 않기 위한 안전장치입니다.
- 최초 실행에서는 현재 국민연금 보유 종목 전체를 신규매수로 간주하지 않습니다. ShareAnalysis에서 확인되는 최근 이벤트만 복원하고 현재 보유량은 다음 실행을 위한 기준선으로 저장합니다.
- 탐지 범위는 FnGuide 공개 주요주주 화면에 나타나는 이벤트입니다. 공개 기준 미만 거래를 포함한 국민연금의 전체 주문 내역을 의미하지 않습니다.
- `nps_state.json`이 보유 기준선과 활성 신호를 로컬에 유지합니다. GitHub Actions에서는 예약·수동 실행을 직렬화하고 실행 시도마다 새 캐시 키로 같은 파일을 복원·저장합니다.
- Actions 캐시는 영구 저장소가 아닙니다. 캐시가 만료되거나 삭제되면 다음 성공 실행이 ShareAnalysis의 공개 최근 이벤트로 기준선을 다시 초기화하므로, 장기 보관이 필요하면 `nps_state.json`을 별도로 백업해야 합니다.

웹 UI는 다음 기능을 제공합니다.

- 스크리닝 대시보드와 수동 재조회
- 5개 백테스트 전략, 거래비용 반영, KOSPI 비교
- 백테스트 CSV 다운로드
- DuckDB 테이블/스키마/종목별 데이터 조회
- 매일 오전 8시 APScheduler 자동 갱신

## 요구사항

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- 인터넷 연결

Python을 별도로 설치하거나 운영체제의 Python을 사용할 필요가 없습니다. 아래 명령은 `uv`가 관리하는 Python 3.11과 격리된 실행 환경을 사용합니다. Chrome, ChromeDriver, Selenium도 필요하지 않습니다.

웹 백테스트는 기본적으로 저장소의 `ticker_map.json`과 yfinance를 사용합니다. KRX 계정이 있고 `KRX_ID`, `KRX_PW` 환경 변수를 설정한 경우 pykrx를 우선 사용하며, 호출 실패 시 yfinance로 자동 대체합니다.

## 설치와 실행

먼저 공식 설치 안내에 따라 `uv`를 한 번 설치합니다. 이후 명령은 macOS, Linux, Windows PowerShell/CMD에서 동일하며, 가상환경을 직접 만들거나 활성화할 필요가 없습니다.

저장소를 처음 받는 경우:

```bash
git clone https://github.com/m1nd0322/gukjang_gumsak.git
cd gukjang_gumsak
uv --version
```

### 대시보드 실행

저장소 루트에서 다음 명령을 실행합니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python app.py
```

첫 실행에서는 `uv`가 Python 3.11과 패키지를 내려받으므로 시간이 조금 걸릴 수 있습니다. 이후 실행은 다운로드 캐시를 재사용합니다.

접속 주소:

- 메인 대시보드: <http://localhost:5000>
- 백테스트: <http://localhost:5000/backtest>
- DB 뷰어: <http://localhost:5000/db>

서버는 실행한 터미널에서 `Ctrl+C`를 눌러 종료합니다.

정적 HTML 리포트만 만들려면 다음을 실행합니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python stock_screener.py
```

결과는 `stock_screening_result.html`에 생성되며 Git에는 포함되지 않습니다.

## 테스트

회귀 테스트는 외부 네트워크 없이 실행됩니다.

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v
```

선택적으로 정적 검사와 컴파일 검사를 실행할 수 있습니다.

```bash
uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py
```

실데이터 연결 확인:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import os; from screening import fetch_all_data; turn, supply, nps = fetch_all_data(require_all=True); print({'turnaround': len(turn), 'supply': len(supply), 'nps_buy_signals': len(nps)}); assert turn and supply and os.path.exists('nps_state.json')"
```

활성 국민연금 매수 신호는 시장 상황과 3개월 유효기간에 따라 0개일 수 있으며, 이 경우도 정상입니다.

### macOS Python 오류 해결

`pyexpat`, `libexpat` 또는 `Symbol not found` 오류가 나타나면 시스템/Homebrew Python이 실행된 경우가 많습니다. `python app.py`를 직접 실행하지 말고 위의 `uv run` 명령을 그대로 사용하세요. 다음 명령의 출력이 `Python 3.11.x`이면 운영체제 Python과 분리된 상태입니다.

```bash
uv run --managed-python --python 3.11 python --version
```

## 백테스트

2점 이상 종목을 대상으로 다음 전략을 지원합니다.

| 전략 | 설명 |
| --- | --- |
| 동일 비중 Buy & Hold | 첫 거래일에 동일 금액으로 매수 후 보유 |
| 월간 리밸런싱 | 20거래일마다 동일 비중으로 재배분 |
| 변동성 가중 + 트레일링 스탑 | 저변동성 종목에 높은 비중을 두고 고점 대비 하락 시 매도 |
| 이동평균 필터(MA20) | 종가가 20일 이동평균보다 높을 때만 보유 |
| 복합 전략 | MA 필터, 변동성 가중, 트레일링 스탑 결합 |

엔진은 다음 비용을 별도로 추적합니다.

- 매수/매도 슬리피지
- 매수/매도 수수료
- 매도 시 증권거래세

총수익률과 MDD는 첫 거래 후 평가액이 아니라 초기 자본금을 기준으로 계산합니다. 여러 번 매수한 포지션은 FIFO 로트로 부분·전량 청산하며, 각 로트의 매수 수수료와 매도 비용을 실현손익에 반영합니다.

> 백테스트는 현재 스크리닝 결과를 과거 전체 기간에 적용하므로 Look-ahead bias가 있습니다. 전략 간 상대 비교와 시스템 검증 용도로 해석하세요.

## 데이터 저장

- `stock_data.duckdb`: 일봉 가격, KOSPI 지수, 종목코드 매핑과 날짜별 종합 스크리닝 결과를 저장합니다.
- `screening_results`: KST 날짜별로 `종목명`, `점수`, `해당항목`, `상세정보`를 저장합니다. 같은 날 다시 갱신하면 해당 날짜의 종목 전체를 교체하고 이전 날짜 이력은 보존합니다.
- `cache_data.json`: 마지막 스크리닝 결과를 버전과 함께 저장해 서버 재시작 시 복원합니다.
- `nps_state.json`: 국민연금 보유 기준선과 만료 전 신규/추가매수 신호를 저장합니다. 로컬 전용 파일이며 Git에는 포함하지 않습니다.
- `nps_state.json.lock/`: 웹·정적 CLI·일일 리포트가 동시에 실행될 때 상태의 읽기와 저장을 프로세스 간 직렬화하는 임시 잠금 디렉터리입니다. 정상 종료 시 자동으로 제거됩니다.
- `ticker_map.json`: FnGuide Snapshot 조회와 GitHub Actions의 yfinance 종목 매핑에 사용합니다.

DuckDB 캐시 범위가 요청 기간을 포함하면 외부 가격 API를 다시 호출하지 않습니다. 티커 맵 신선도는 가장 최근의 성공적인 갱신 시각을 기준으로 판단하고, 새 DB이거나 KRX 갱신이 실패하면 `ticker_map.json`으로 초기화합니다. KRX 가격·지수 호출이 불가능하면 yfinance의 `.KS`/`.KQ` 종목과 `^KS11` 지수로 자동 대체합니다.

Flask의 매일 08:00 자동갱신과 수동 재조회는 새 결과를 메모리와 JSON 캐시에 게시하기 전에 DuckDB 스냅샷부터 저장합니다. GitHub Actions도 같은 저장 규칙을 사용하며, `stock_data.duckdb`를 실행 전에 캐시에서 복원하고 성공 후 다시 저장합니다. 로컬 DuckDB와 GitHub Actions DuckDB는 서로 독립된 파일입니다.

## GitHub Actions 일일 리포트

`.github/workflows/daily_report.yml`은 월~금 오전 8시(KST)에 다음 순서로 실행됩니다. 한국 공휴일은 별도로 판정하지 않습니다.

1. 예약·수동 실행을 하나씩 직렬 처리
2. 이전 실행의 `nps_state.json`과 `stock_data.duckdb` 캐시 복원
3. Python 3.11 및 의존성 설치
4. 회귀 테스트 실행
5. FnGuide 세 지표 수집, 종목 스코어링, 전체 종합결과의 DuckDB 저장
6. 2점 이상 종목의 6개월 복합 전략 백테스트
7. yfinance 가격과 KOSPI 벤치마크 수집
8. 텔레그램 요약/CSV 전송
9. 성공한 국민연금 상태와 DuckDB를 실행 시도별 새 키로 캐시에 저장
10. CSV를 GitHub Actions Artifact로 30일 보관

워크플로에는 Chrome 설치 단계가 없습니다.

### 텔레그램 설정

저장소의 `Settings → Secrets and variables → Actions`에 다음 Repository secret을 등록합니다.

| Secret | 값 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 메시지를 받을 개인 또는 그룹 Chat ID |

봇 생성은 텔레그램의 [@BotFather](https://t.me/BotFather)에서 `/newbot`으로 시작할 수 있습니다. 수동 검증은 GitHub의 `Actions → 일일 국장검색 리포트 → Run workflow`에서 실행합니다.

GitHub 호스티드 러너에서는 KRX 접근 제약을 피하기 위해 가격 데이터에 yfinance를 사용합니다. 스크리닝과 종목 매핑에는 저장소의 `ticker_map.json`이 필요합니다.

## 프로젝트 구조

```text
app.py                         Flask 웹 서버와 작업 오케스트레이션
screening.py                   FnGuide 데이터 수집, 검증, 공통 점수 계산
nps_tracker.py                 국민연금 매수 신호 날짜·상태 전이와 원자 저장
backtester.py                  거래비용/FIFO 로트 기반 커스텀 백테스트 엔진
stock_db.py                    DuckDB 가격·지수·티커 캐시와 일일 종합결과 이력
stock_screener.py              정적 HTML 리포트 CLI
daily_report.py                GitHub Actions 일일 텔레그램 리포트
ticker_map.json                종목명→종목코드 매핑
tests/                         네트워크 독립 회귀 테스트
requirements.txt               런타임 의존성
.github/workflows/
  daily_report.yml             평일 오전 8시 자동 리포트
```

## 면책 조항

이 프로젝트는 투자 참고 및 소프트웨어 실험용입니다. 데이터의 정확성·완전성을 보장하지 않으며, 투자 판단과 결과에 대한 책임은 사용자에게 있습니다.
