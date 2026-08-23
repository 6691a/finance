"""수집기 패키지.

여기서 하위 모듈을 재수출하지 않는다. 재수출하면 DAG 하나가 이 패키지를 건드릴 때 모든
수집기가 함께 import되고, 한 수집기의 의존성(예: `scrapling`, `curl_cffi`)이 Airflow 환경에
없으면 관계없는 DAG까지 import 오류로 죽는다. DAG는 필요한 모듈을 직접 가리킨다.

    from modules.collectors.indicator.fred import FredCollector
"""
