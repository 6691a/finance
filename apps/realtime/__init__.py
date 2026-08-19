"""KIS 실시간 WebSocket 1분봉 수집 서비스.

Airflow가 실행하지 않는 상주 서비스라 `airflow/`가 아니라 백엔드 트리에 있다.
모델·설정·DB 연결을 (앞으로 올) FastAPI와 공유하고 배포만 별도 컨테이너로 한다.
실행은 `python -m apps.realtime`이다.
"""
