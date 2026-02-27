# 국장검색 (gukjang_gumsak)

한국 증시 종합 스크리닝 시스템 — FnGuide 데이터 기반 종목 점수화 및 백테스트

## 개요

FnGuide에서 3가지 지표를 크롤링하여 종목별로 점수를 매기고, 고점수 종목에 대해 과거 데이터 기반 백테스트를 수행하는 웹 애플리케이션입니다.

### 스크리닝 기준 (각 1점, 최대 3점)

| 기준 | 설명 |
|------|------|
| 연간실적호전 (턴어라운드) | 연간 실적이 호전된 종목 |
| 외국인/기관 동반 순매수 전환 | 외국인과 기관이 동시에 순매수로 전환한 종목 |
| 국민연금 보유 | 국민연금공단이 보유 중인 종목 |

## 실행 방법

### 요구사항

- Python 3.10+
- Chrome 브라우저 (Selenium headless 크롤링)

### 설치 및 실행

```bash
pip install -r requirements.txt
python app.py
```

- 메인 대시보드: http://localhost:5000
- 백테스트 페이지: http://localhost:5000/backtest
- DB 뷰어: http://localhost:5000/db

### 자동 갱신

APScheduler가 매일 오전 8시에 FnGuide 데이터를 자동 갱신합니다. 웹 대시보드의 "재조회" 버튼으로 수동 갱신도 가능합니다.

## 백테스트

2점 이상 종목을 대상으로 5가지 전략을 지원합니다:

| 전략 | 설명 |
|------|------|
| 동일 비중 Buy & Hold | 첫 거래일에 동일 금액 매수 후 보유 |
| 월간 리밸런싱 | 20 거래일마다 동일 비중으로 재배분 |
| 변동성 가중 + 트레일링 스탑 | 저변동성 종목에 높은 비중, 고점 대비 하락 시 매도 |
| 이동평균 필터 (MA20) | 종가 > 20일 이동평균일 때만 보유 |
| 복합 전략 | MA 필터 + 변동성 가중 + 트레일링 스탑 결합 |

슬리피지, 거래 수수료, 증권거래세를 반영하며, KOSPI 벤치마크 대비 성과를 비교합니다. 결과는 CSV로 다운로드할 수 있습니다.

매매 상세 이력에서 종목, 상태(청산/보유중), 손익(수익/손실), 매수일 범위로 필터링할 수 있습니다.

## DB 뷰어

DuckDB에 저장된 데이터를 웹 UI로 조회할 수 있습니다 (`/db`):

- DB 통계 (크기, 레코드 수, 종목 수, 날짜 범위)
- 테이블별 데이터 브라우저 (페이지네이션, 정렬, 필터)
- 종목별 데이터 요약 (종목명, 보유 기간, 최신 종가)

읽기 성능을 위해 `daily_prices`, `index_prices`, `ticker_map` 테이블에 인덱스가 적용되어 있습니다.

## GitHub Actions 자동 리포트

매일 오전 8시(KST)에 자동으로 스크리닝 → 백테스트 → 텔레그램 알림을 수행하는 GitHub Actions 워크플로우가 포함되어 있습니다.

### 동작 흐름

1. FnGuide 3개 지표 크롤링 (Selenium headless)
2. 종목 스코어링 (최대 3점)
3. 2점 이상 종목 대상 백테스트 (복합 전략: MA + 변동성 가중 + 트레일링 스탑)
4. 슬리피지(0.3%), 수수료(0.015%), 증권거래세(0.20%) 반영
5. KOSPI 벤치마크 대비 성과 비교
6. 상위 10종목 + 백테스트 결과를 텔레그램으로 전송
7. CSV 파일을 GitHub Actions Artifact로 업로드 (30일 보관)

### 설정 방법

#### 1. 텔레그램 봇 생성

1. 텔레그램에서 [@BotFather](https://t.me/BotFather)에게 `/newbot` 명령어 전송
2. 봇 이름과 사용자명 설정 후 **봇 토큰** 복사
3. 생성된 봇에게 아무 메시지 전송 (채팅방 활성화)
4. [@userinfobot](https://t.me/userinfobot)에게 메시지를 보내 **Chat ID** 확인 (그룹 채팅방의 경우 봇을 그룹에 추가 후 `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id` 확인)

#### 2. GitHub Secrets 등록

GitHub 저장소 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| Secret 이름 | 값 |
|-------------|-----|
| `TELEGRAM_BOT_TOKEN` | BotFather에서 받은 봇 토큰 (예: `123456:ABC-DEF...`) |
| `TELEGRAM_CHAT_ID` | 메시지를 받을 채팅 ID (예: `123456789`) |

#### 3. 종목 매핑 파일 갱신 (선택)

`ticker_map.json`에 종목명→종목코드 매핑이 저장되어 있습니다. 신규 상장/상장폐지가 있을 경우 로컬에서 갱신할 수 있습니다:

```bash
python -c "
from stock_db import StockDB
db = StockDB()
import json
n2c, _ = db.get_ticker_map_from_db()
with open('ticker_map.json', 'w', encoding='utf-8') as f:
    json.dump(n2c, f, ensure_ascii=False, indent=2)
print(f'{len(n2c)}개 종목 저장 완료')
"
```

> **참고**: GitHub Actions는 해외 서버에서 실행되어 KRX API(pykrx)에 접근할 수 없으므로, 가격 데이터는 yfinance를 사용합니다. 종목 매핑은 로컬에서 생성한 `ticker_map.json`을 사용합니다.

#### 4. 수동 실행

GitHub 저장소 → `Actions` 탭 → `일일 국장검색 리포트` → `Run workflow` 버튼으로 수동 실행할 수 있습니다.

### 텔레그램 알림 내용

- 스크리닝 요약 (3개 지표별 종목 수, 점수 분포)
- 상위 10종목 (종목명, 점수, 출처)
- 백테스트 결과 (수익률, 연환산수익률, MDD, 샤프비율, 승률)
- 거래비용 반영 내역 (슬리피지, 수수료, 거래세)
- KOSPI 벤치마크 비교 (초과수익률 α)
- 개별 종목 수익률
- CSV 파일 첨부

## 데이터 저장

- **DuckDB** (`stock_data.duckdb`): 일봉 가격, 종목코드 매핑, KOSPI 지수 데이터를 증분 저장합니다. 이미 수집된 날짜는 pykrx API를 다시 호출하지 않습니다.
- **JSON 캐시** (`cache_data.json`): 마지막 스크리닝 결과를 캐시하여 서버 재시작 시 즉시 로드합니다.

## 프로젝트 구조

```
app.py              # Flask 웹 서버, 크롤링, 스코어링, 백테스트 오케스트레이션
backtester.py       # 커스텀 백테스트 엔진 (외부 의존성 없음)
stock_db.py         # DuckDB 기반 주가 데이터 스토리지
daily_report.py     # GitHub Actions 일일 자동 리포트 스크립트
ticker_map.json     # 종목명→종목코드 매핑 (yfinance용)
stock_screener.py   # 독립 실행 CLI 버전 (requests 기반, 웹 서버 미사용)
requirements.txt    # Python 의존성
.github/workflows/  # GitHub Actions 워크플로우
  daily_report.yml  #   매일 오전 8시 자동 리포트
```

## 면책 조항

본 시스템은 투자 참고용이며, 투자의 최종 책임은 투자자 본인에게 있습니다. 백테스트 결과는 현재 스크리닝 결과 기준의 시뮬레이션으로, Look-ahead bias가 존재할 수 있습니다.
