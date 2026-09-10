-- 같은 대화의 `running` 행을 `failed`로 닫는다. 새 행을 열기 직전, 같은 트랜잭션이다.
--
-- 프로세스가 밖에서 죽으면(타임아웃 kill, 워커 재시작) `finally`가 못 돌아 행이 영영
-- `running`으로 남는다(2026-08-24 백필 1차 시도). "돌다 죽었다"가 "아직 돌고 있다"로
-- 읽히면 원장이 거짓말을 한다. 다시 돌 실행이 없는 행은 이 문장이 안 닿는다 —
-- 그건 사람이 닫는다.
--
-- `slot`은 관찰이 NULL이라 `=`가 아니라 `IS NOT DISTINCT FROM`이다.
UPDATE kospi_llm_run
SET status = 'failed',
    finished_at = %(finished_at)s,
    error = %(error)s,
    updated_at = now()
WHERE kind = %(kind)s
  AND run_date = %(run_date)s
  AND slot IS NOT DISTINCT FROM %(slot)s
  AND status = 'running'
