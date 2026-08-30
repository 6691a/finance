-- 경로가 저장된 주 전부. 초기 적재와 재동기화(`sync_only`)가 이것으로 돈다.
--
-- 오름차순인 것이 중요하다. **앞 주부터 넣어야** 어휘 노드가 먼저 서고 뒤 주가 그것을
-- 이어 붙인다. 순서가 뒤집혀도 MERGE라 결과는 같지만, 중간에 실패했을 때 남는 그래프가
-- 시간 앞쪽부터 온전한 편이 낫다.
SELECT DISTINCT week_start
  FROM market_causal_path
 ORDER BY week_start
