"""시세·수급 수집기.

"얼마에 거래됐나"(봉)와 "지금 누가 사고 누가 파나"(수급)를 받는다. 값이 숫자이고 그레인이
시각 또는 거래일이라, 소비자가 LLM인 문서 계열과 저장 규칙이 다르다.

지금 여기 있는 것은 KIS 투자자 매매동향과 포지션 지표(신용·공매도·대차·증시자금)다. 나머지
시세 수집기(`collectors/kis.py`의 분봉, `collectors/yahoo.py`)는 아직 옛 자리에 있다.

`collectors/__init__.py`와 같은 이유로 하위 모듈을 재수출하지 않는다. 한 수집기의 의존성이
없는 환경에서 관계없는 DAG이 import 오류로 죽는다.

    from modules.collectors.market.kis_investor_flow import KisInvestorFlowCollector
"""
