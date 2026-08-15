-- 프롬프트에 넣을 지표 후보.
--
-- 두 마스터를 합친다. `indicator_series`는 금리 같은 지표 시계열이고 `quote_symbol`은 환율·
-- 지수·원자재다. 기사가 "환율이 올랐다"고 할 때 걸릴 대상은 후자에 있다.
SELECT provider, series_id, label
FROM indicator_series
UNION ALL
SELECT provider, symbol AS series_id, label
FROM quote_symbol
ORDER BY provider, series_id
