-- 문서와 종목을 잇는다. 같은 태그가 다시 와도 행이 늘지 않는다.
-- 정의의 원본은 `apps/models/content.py`의 `DocumentInstrument`다.
INSERT INTO document_instrument (document_id, ticker)
VALUES (%s, %s)
ON CONFLICT (document_id, ticker) DO NOTHING
