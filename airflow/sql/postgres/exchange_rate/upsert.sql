-- [배포] 저장 위치 3/5 — 테이블 이름과 스키마.
-- 스키마를 수식하지 않으므로 연결의 search_path(PostgreSQL 기본 public)를 따른다.
-- 운영에서 다른 스키마에 두려면 아래 두 곳을 `<스키마>.exchange_rate`로 바꾼다.
-- INSERT 대상과 ON CONFLICT 제약 이름은 함께 움직여야 한다.
-- 어느 DB에 넣을지는 여기가 아니라 DAG의 연결 ID가 정한다.
--
-- 고시 환율 1행을 저장한다. 값은 문장에 끼워 넣지 않고 전부 파라미터로 넘긴다.
-- 멱등 키는 unique_currency_date_time_round와 같은 (currency, date, time, round)다.
-- 같은 회차를 다시 수집하면 행을 늘리지 않고 최신 고시 값으로 갱신한다.
--
-- 이 폴더에 create.sql은 없다. 테이블은 DAG가 아니라 백엔드 마이그레이션이 만든다.
-- 정의의 원본은 `apps/models/finance.py`의 `ExchangeRate`이고 DDL은
-- `migrations/versions/b3d0a15c7e42_move_exchange_rate_to_default.py`가 낸다.
INSERT INTO exchange_rate (
    currency,
    round,
    date,
    time,
    buy,
    sell,
    send,
    receive,
    exchange_standard_rate
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (currency, date, time, round) DO UPDATE SET
    buy = EXCLUDED.buy,
    sell = EXCLUDED.sell,
    send = EXCLUDED.send,
    receive = EXCLUDED.receive,
    exchange_standard_rate = EXCLUDED.exchange_standard_rate,
    updated_at = CURRENT_TIMESTAMP
