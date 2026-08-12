-- 공시 접수 하나를 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, rcept_no)다.
--
-- **detected_at은 갱신하지 않는다.** 최초로 그 접수번호를 본 시각이라 재수집이 덮으면
-- 의미가 사라진다. 2분 폴링이라 이 값이 공시 시각의 상한 노릇을 한다.
--
-- 정의의 원본은 `apps/models/market.py`의 `DisclosureEvent`이고
-- `tests/collectors/test_dart.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO disclosure_event (
    provider,
    corp_code,
    stock_code,
    company_name,
    rcept_no,
    report_name,
    filer_name,
    corp_class,
    receipt_date,
    detected_at,
    remarks,
    source_record_id
) VALUES ('dart', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, rcept_no) DO UPDATE SET
    company_name = EXCLUDED.company_name,
    report_name = EXCLUDED.report_name,
    filer_name = EXCLUDED.filer_name,
    corp_class = EXCLUDED.corp_class,
    receipt_date = EXCLUDED.receipt_date,
    remarks = EXCLUDED.remarks,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
