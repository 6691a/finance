"""애널리스트 판단을 받는 수집기.

시세가 "얼마에 거래됐나"이고 수급이 "누가 들고 있나"라면 이쪽은 **"전문가가 이 종목을 어떻게
보나"**다. 지금은 KIS 종목투자의견 하나뿐이고, 리포트 글은 문서 계열(`collectors/document`)이
가져간다 — 숫자는 조인해서 읽고 글은 점수로 고르므로 쓰임이 다르다.

`collectors/__init__.py`와 같은 이유로 하위 모듈을 재수출하지 않는다. DAG는 필요한 모듈을
직접 가리킨다.

    from modules.collectors.analyst.kis_opinion import KisAnalystOpinionCollector
"""
