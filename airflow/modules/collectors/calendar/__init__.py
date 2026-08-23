"""거래일·결제일 수집기.

"그날 장이 열리나, 결제는 언제인가"를 `market_session`에 채운다. 시세도 문서도 아니고,
다른 수집기와 브리핑·추론이 "오늘이 영업일인가"를 물을 때 보는 기준 정보다.

지금 여기 있는 것은 KIS 국내휴장일·해외결제일 하나다. `collectors/nyse_calendar.py`는 아직
옛 자리에 있다.

`collectors/__init__.py`와 같은 이유로 하위 모듈을 재수출하지 않는다. 한 수집기의 의존성이
없는 환경에서 관계없는 DAG이 import 오류로 죽는다.

    from modules.collectors.calendar.kis_market_calendar import KisMarketCalendarCollector
"""
