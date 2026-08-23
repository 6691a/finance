"""지표 시계열 수집기.

금리·물가·고용처럼 `(provider, series_id, observation_date)`를 키로 `indicator_observation`에
쌓이는 값을 받는다. 응답 하나가 한 계열의 한 구간이고 저장은 upsert라 재수집이 행을 늘리지
않는다.

지금 여기 있는 것은 FRED 하나다. 나머지(`collectors/ecos.py`·`ecb.py`·`ecb_irs.py`·`bbk.py`·
`boe.py`·`mof.py`)는 아직 옛 자리에 있다.

`collectors/__init__.py`와 같은 이유로 하위 모듈을 재수출하지 않는다. 한 수집기의 의존성이
없는 환경에서 관계없는 DAG이 import 오류로 죽는다.

    from modules.collectors.indicator.fred import FredCollector
"""
