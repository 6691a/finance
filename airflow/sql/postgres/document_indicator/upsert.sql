-- 문서와 지표 시계열을 잇는다. series_id는 제공처 안에서만 고유해 provider가 함께 들어간다.
-- 정의의 원본은 `apps/models/content.py`의 `DocumentIndicator`다.
INSERT INTO document_indicator (document_id, provider, series_id)
VALUES (%s, %s, %s)
ON CONFLICT (document_id, provider, series_id) DO NOTHING
