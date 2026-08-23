"""문서(글) 수집기.

피드·게시판·리서치 목록에서 경제 문서를 발견해 `document`에 넣는다. 숫자를 받는 수집기와
달리 소비자가 LLM이라, 저장 단위가 시계열이 아니라 문서 한 건이고 가치 판단은 뒤따르는
평가 DAG이 한다.

지금 여기 있는 것은 네이버 증권 리서치와 DART 공시다. 나머지(`collectors/documents.py`의 피드,
`collectors/document_listings.py`의 KRX·금감원)는 아직 옛 자리에 있고, 옮기는 계획은
`docs/collectors-class-migration.md`에 있다.

`collectors/__init__.py`와 같은 이유로 하위 모듈을 재수출하지 않는다.

    from modules.collectors.document.naver_research import NaverResearchCollector
"""
