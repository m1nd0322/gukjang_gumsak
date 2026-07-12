# 국장검색 (gukjang_gumsak)

한국 증시 종합 스크리닝, 백테스트, 일일 텔레그램 리포트를 한 저장소에서 실행하는 Python 애플리케이션입니다.

## 주요 기능

FnGuide의 세 가지 지표에 각각 1점을 부여하고, 2점 이상 종목을 백테스트 대상으로 사용합니다.

| 기준 | 설명 | 현재 데이터 경로 |
| --- | --- | --- |
| 연간실적호전 | 연간 영업이익이 개선된 종목 | FnGuide `TURNAROUND_A.json` |
| 외국인/기관 동반 순매수 전환 | 외국인과 기관이 함께 순매수로 전환한 종목 | FnGuide `SUPPLY_TREND_FIRST_BUY.json` |
| 국민연금 보유 | 종목별 주주현황에 국민연금공단이 공시된 종목 | FnGuide CompanyInfo Snapshot |

구형 `WooriRenewal` HTML 화면이나 Selenium DOM에 의존하지 않습니다. JSON 응답 구조와 Snapshot의 실제 종목코드를 검증하므로, HTTP 200 오류 문서와 우선주→보통주 페이지 연결도 결과에서 걸러냅니다.

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
uvx ruff check app.py backtester.py daily_report.py screening.py stock_db.py stock_screener.py tests
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py screening.py stock_db.py stock_screener.py
```

실데이터 연결 확인:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "from screening import fetch_all_data; turn, supply, nps = fetch_all_data(); print({'turnaround': len(turn), 'supply': len(supply), 'nps': len(nps)}); assert turn and supply and nps"
```

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

- `stock_data.duckdb`: 일봉 가격, KOSPI 지수, 종목코드 매핑을 증분 저장합니다.
- `cache_data.json`: 마지막 스크리닝 결과를 저장해 서버 재시작 시 복원합니다.
- `ticker_map.json`: FnGuide Snapshot 조회와 GitHub Actions의 yfinance 종목 매핑에 사용합니다.

DuckDB 캐시 범위가 요청 기간을 포함하면 외부 가격 API를 다시 호출하지 않습니다. 티커 맵 신선도는 가장 최근의 성공적인 갱신 시각을 기준으로 판단하고, 새 DB이거나 KRX 갱신이 실패하면 `ticker_map.json`으로 초기화합니다. KRX 가격·지수 호출이 불가능하면 yfinance의 `.KS`/`.KQ` 종목과 `^KS11` 지수로 자동 대체합니다.

## GitHub Actions 일일 리포트

`.github/workflows/daily_report.yml`은 월~금 오전 8시(KST)에 다음 순서로 실행됩니다. 한국 공휴일은 별도로 판정하지 않습니다.

1. Python 3.11 및 의존성 설치
2. 회귀 테스트 실행
3. FnGuide 세 지표 수집과 종목 스코어링
4. 2점 이상 종목의 6개월 복합 전략 백테스트
5. yfinance 가격과 KOSPI 벤치마크 수집
6. 텔레그램 요약/CSV 전송
7. CSV를 GitHub Actions Artifact로 30일 보관

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
backtester.py                  거래비용/FIFO 로트 기반 커스텀 백테스트 엔진
stock_db.py                    DuckDB 증분 가격·지수·티커 캐시
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
