# 일일 종합결과 DuckDB 저장 설계

## 목적

성공한 스크리닝 자동갱신마다 종합결과를 DuckDB에 날짜별 스냅샷으로 저장한다. 저장 대상은 종목명, 점수, 해당항목, 상세정보이며, 같은 날짜에 다시 실행하면 그 날짜의 결과 전체를 교체한다.

## 적용 범위

- Flask 앱의 APScheduler `daily_refresh` 작업과 수동 `/api/refresh`가 공유하는 `refresh_data()` 경로
- GitHub Actions의 평일 일일 리포트가 실행하는 `daily_report.py` 경로
- `stock_data.duckdb`의 새 `screening_results` 테이블
- DuckDB 뷰어에서 새 테이블의 목록, 스키마, 데이터를 조회하는 기존 API
- GitHub Actions 실행 사이에 `stock_data.duckdb`를 복원하고 저장하는 캐시 단계

로컬 Flask 앱과 GitHub Actions는 서로 다른 파일 시스템에서 실행하므로 물리적으로 같은 DuckDB 파일을 공유하지 않는다. 두 환경은 동일한 스키마와 날짜별 교체 규칙을 사용해 각자의 이력을 유지한다.

## 데이터 모델

`screening_results` 테이블은 다음 컬럼을 가진다.

| 컬럼 | 타입 | 의미 |
| --- | --- | --- |
| `snapshot_date` | `DATE` | `Asia/Seoul` 기준 결과 날짜 |
| `stock_name` | `VARCHAR` | 종합결과의 `종목명` |
| `score` | `INTEGER` | 종합결과의 `종합점수` |
| `matched_items` | `VARCHAR` | 종합결과의 `출처`; UI의 해당항목 |
| `details` | `JSON` | 핵심 컬럼을 제외한 `[턴]`, `[수급]`, `[연금]` 상세 필드 |
| `updated_at` | `TIMESTAMP` | 해당 날짜 스냅샷을 마지막으로 교체한 시각 |

기본키는 `(snapshot_date, stock_name)`이다. 같은 종목이 서로 다른 날짜에 존재할 수 있어 점수 변화 이력을 보존한다.

`details`에서는 `종목명`, `종합점수`, `출처`, `순위`를 제외한다. 상세 필드 이름은 원본 종합결과의 키를 그대로 보존하고 UTF-8 JSON으로 직렬화한다.

## 저장 인터페이스

`StockDB.replace_screening_results(results, snapshot_date=None) -> int`를 추가한다.

- `results`는 `calculate_scores()`가 반환한 종합결과 목록이다.
- `snapshot_date`를 생략하면 `Asia/Seoul`의 현재 날짜를 사용한다.
- 모든 행의 필수 필드와 JSON 직렬화 가능 여부를 DB 트랜잭션 전에 검증한다.
- 트랜잭션 안에서 같은 날짜의 기존 행 전체를 삭제한 뒤 새 행을 삽입한다.
- 빈 결과도 유효하다. 같은 날짜의 기존 행을 모두 삭제하고 0을 반환한다.
- 삽입 실패 시 롤백하여 기존 날짜 스냅샷을 그대로 유지한다.

이 방식은 같은 날 자동·수동 갱신이 반복돼도 중복 없이 최신 전체 결과만 남기며, 새 결과에서 탈락한 종목이 잔존하지 않게 한다.

## 실행 흐름

### Flask 자동갱신

1. `fetch_all_data(require_all=True)`로 세 소스를 모두 수집한다.
2. `calculate_scores()`로 종합결과를 계산한다.
3. `stock_db.replace_screening_results()`로 DuckDB 스냅샷을 저장한다.
4. 저장 성공 후에만 `current_data`와 `cache_data.json`을 새 결과로 게시한다.
5. DuckDB 저장 실패는 기존 갱신 실패 경로로 전달하고 이전 메모리·JSON 캐시를 유지한다.

APScheduler와 수동 API가 같은 `refresh_data()` 경로를 사용하므로 별도 스케줄러 코드는 추가하지 않는다.

### GitHub Actions 일일 리포트

1. 워크플로 시작 시 가장 최근 `stock_data.duckdb` 캐시를 복원한다.
2. `daily_report.py`가 수집과 스코어링을 마친 직후 DuckDB 스냅샷을 저장한다.
3. 2점 이상 종목이 없어 리포트가 조기 종료되더라도 전체 스크리닝 결과는 이미 저장돼 있어야 한다.
4. 성공한 실행에서 DuckDB 파일을 실행별 고유 키로 캐시에 저장한다.
5. 저장 실패는 일일 리포트 실패로 처리해 결과 전송만 성공하고 DB가 누락되는 상태를 허용하지 않는다.

기존 `concurrency` 그룹이 예약·수동 실행을 직렬화하므로 캐시의 동시 덮어쓰기 경쟁은 추가로 만들지 않는다.

## 오류 및 일관성

- 원천 데이터가 하나라도 실패하면 점수 계산과 DuckDB 저장을 실행하지 않는다.
- DuckDB 갱신은 같은 날짜 단위로 원자적이다.
- Flask에서는 DB 저장 전 새 결과를 메모리나 JSON 캐시에 게시하지 않는다.
- GitHub Actions에서는 DB 저장 실패 시 비정상 종료하여 성공 캐시를 만들지 않는다.
- 기존 가격, 종목 매핑, 지수 테이블은 변경하지 않는다.
- 새 의존성은 추가하지 않는다. 이미 사용하는 DuckDB와 Python 3.11 표준 `zoneinfo`를 사용한다.

## 검증

- 새 DB 초기화 시 `screening_results` 스키마가 생성된다.
- 종합결과 네 필드가 올바른 컬럼과 JSON 상세정보로 저장된다.
- 같은 날짜 재실행 시 사라진 종목이 제거되고 남은 종목이 갱신된다.
- 다른 날짜의 스냅샷은 보존된다.
- 빈 결과는 해당 날짜만 비운다.
- Flask 갱신은 DuckDB 저장 후 캐시를 게시하며 저장 실패 시 이전 상태를 유지한다.
- `daily_report.py`는 2점 이상 종목이 없는 경로에서도 저장을 먼저 실행한다.
- GitHub Actions 워크플로가 DuckDB를 복원하고 성공 시 저장한다.
- 전체 단위 테스트, Ruff, Python 컴파일 검사를 통과한다.

## 제외 범위

- 로컬 DuckDB와 GitHub Actions DuckDB의 네트워크 동기화
- 기존 `cache_data.json` 제거
- 과거 `cache_data.json`을 DuckDB 이력으로 소급 이관
- 별도 UI 차트나 점수 변화 분석 화면
