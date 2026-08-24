-- 확정 일봉에서 검출한 매매 신호 한 건. 멱등 키는 (provider, symbol, signal_date, kind)다.
--
-- **`thesis`와 달리 덮어쓴다.** 저 쪽은 LLM이 재호출마다 답이 달라 첫 성공본을 지키지만
-- 이것은 결정적 계산이라, 원천 봉이 수정되면 값이 따라가는 편이 맞다. 덮어써도 "최초
-- 판단"이 사라지는 게 아니다.
--
-- 정의의 원본은 `apps/models/analysis.py`의 `TechnicalSignal`이고
-- `tests/modules/test_technical_signals.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO technical_signal (
    provider,
    symbol,
    signal_date,
    kind,
    direction,
    close,
    sma20,
    sma60,
    rsi14,
    macd,
    macd_signal,
    volume_ratio20,
    rule_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, signal_date, kind) DO UPDATE SET
    direction = EXCLUDED.direction,
    close = EXCLUDED.close,
    sma20 = EXCLUDED.sma20,
    sma60 = EXCLUDED.sma60,
    rsi14 = EXCLUDED.rsi14,
    macd = EXCLUDED.macd,
    macd_signal = EXCLUDED.macd_signal,
    volume_ratio20 = EXCLUDED.volume_ratio20,
    rule_version = EXCLUDED.rule_version,
    updated_at = now()
