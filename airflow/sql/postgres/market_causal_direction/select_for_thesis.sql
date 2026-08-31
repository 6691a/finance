-- 추론이 관측 상태에 실을 방향성. 대상마다 **한 주**만 준다.
--
-- **`created_at`으로 자른다**(설계 §4.2). `week_start`로는 안 된다 — 경로는 그 주가 끝나고
-- 한 주 뒤(`W+2` 월요일)에 생기고, 재실행이면 아무 때나 생긴다. 운영의 두 주가 전부
-- 2026-08-30 재실행 시각에 생겼다.
--
-- **나이 상한이 함께 걸린다**(설계 §4.2.1). 주간 태스크가 skip되거나 실패해도 지난 주 행은
-- 남아 있고, 그것을 최신인 척 읽으면 skip이 추론까지 전파되지 않는다. 정상은 `W-2`이고
-- 한 주 놓치면 `W-3`이라 거기까지 허용한다. 상한 밖이면 그 대상의 키가 아예 없고,
-- 프롬프트는 "없음"이라 적는다 — 낡은 값을 참고로 주지 않는다.
--
-- **대상 코드로만 맞춘다.** 추론은 `stock`, 그래프는 `instrument`라 `kind` 이름이 다르고
-- 둘 다 코드가 유일하다.
--
-- 여러 주를 겹쳐 주지 않는다. 추세를 보여 주려면 그건 별도 결정이고, 지금 데이터가 두 주다.
SELECT target_code,
       week_start,
       bias,
       reasoning,
       up_count,
       down_count,
       flat_count,
       path_ids,
       channels
  FROM (
        SELECT target_code,
               week_start,
               bias,
               reasoning,
               up_count,
               down_count,
               flat_count,
               path_ids,
               channels,
               row_number() OVER (PARTITION BY target_code ORDER BY week_start DESC) AS recency
          FROM market_causal_direction
         WHERE target_code = ANY(%(codes)s)
           AND created_at <= %(as_of_at)s
           AND week_start >= %(oldest_week)s
       ) ranked
 WHERE recency = 1
 ORDER BY target_code
