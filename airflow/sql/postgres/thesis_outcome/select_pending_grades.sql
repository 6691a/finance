-- 아직 채점하지 않은 (추론, 지평) 조합 전부.
--
-- `pre_open`만이다. `post_close` 리뷰는 이미 일어난 일의 해석이라 예측이 아니고 채점할
-- 대상이 없다(해설은 붙는다 — `select_pending_narratives.sql`).
--
-- **날짜 상한이 없다.** 장후가 실패했던 날의 것도 다음 실행이 회수해야 한다. 종가가 영영
-- 나오지 않는 행(상장폐지 등)은 계속 이 결과에 남는다 — 자연키 UNIQUE가 인덱스를 주므로
-- 스캔은 그 인덱스를 탄다. 누적이 문제가 되면 그때 상한을 둔다.
--
-- 지평 목록은 파라미터다. 상수를 SQL과 파이썬 두 곳에 두면 한쪽만 고쳐지는 날이 온다.
--
-- 목표 영업일이 지났는지는 여기서 보지 않는다. 파이썬이
-- `market_session/select_nth_open_day.sql`로 날짜를 구하고, 그 날짜의 종가가 없으면
-- 그 조합을 그냥 건너뛴다. 달력을 SQL 두 곳에서 세지 않는다.
SELECT thesis.id,
       thesis.run_date,
       thesis.as_of_at,
       thesis.subject_kind,
       thesis.subject_code,
       thesis.prob_up,
       thesis.prob_down,
       thesis.prob_flat,
       horizon.horizon_days
FROM thesis
CROSS JOIN unnest(%s::integer[]) AS horizon(horizon_days)
WHERE thesis.run_slot = 'pre_open'
  AND NOT EXISTS (
      SELECT 1
      FROM thesis_outcome
      WHERE thesis_outcome.thesis_id = thesis.id
        AND thesis_outcome.horizon_days = horizon.horizon_days
        AND thesis_outcome.evaluated_at IS NOT NULL
  )
ORDER BY thesis.run_date, thesis.id, horizon.horizon_days
