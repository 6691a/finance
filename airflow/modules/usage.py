"""대화 하나가 청구된 토큰.

**여기 따로 있는 이유는 무게다.** 이 모델은 원장에 쓰는 값이라 저장 층(`kospi/store.py`)이
봐야 하는데, 그것을 만드는 `modules/llm.py`는 모듈 맨 위에서 LangChain을 올린다. 파이썬은
`from X import Y`에서 `X`를 통째로 실행하므로, 이름 하나를 가져오는 것만으로 그 무게가
따라온다 — 실측 202개 모듈이었다(2026-09-03).

Airflow 스케줄러는 모든 DAG 파일을 주기적으로 다시 파싱한다. 전망을 만들지도 않는 파싱이
`ChatXAI`와 `ChatOpenAI`를 올리는 것이 그 비용이다. `tests/modules/test_import_weight.py`가
그 경계를 잰다.

저장소 규칙의 "모델을 두는 곳은 그 값을 만드는 모듈이다. 단 그 모듈이 LangChain·Airflow를
import하는데 다른 모듈도 같은 모델을 봐야 하면 **무거운 의존성이 없는 모듈로 따로 뺀다**"가
이 파일이다.

**콜백을 읽어 이 모양으로 바꾸는 것은 `llm.py`의 `token_usage`가 한다.** 그쪽은 LangChain의
핸들러 타입을 알아야 해서 옮길 수 없고, 옮길 이유도 없다 — 무거운 것은 그 함수를 부르는
흐름 코드뿐이다.
"""

from pydantic import BaseModel, ConfigDict


class TokenUsage(BaseModel):
    """대화 하나가 청구된 토큰. `kospi_llm_run`의 네 칸이 이 값을 그대로 받는다.

    넷을 나눠 두는 이유는 **서로 다른 손잡이에 붙기 때문이다.** `prompt`는 왕복마다 대화
    전체가 재전송된 결과라 프롬프트 블록 크기와 왕복 상한이 움직이고, `reasoning`은 대화에
    남지 않아 재전송되지도 캐시되지도 않는다. 한 칸으로 묶으면 어느 쪽이 늘었는지 못 가른다.

    **`completion`은 `reasoning`을 포함하고, `cached`는 `prompt`에 포함된다.** 제공처가 사고
    토큰도 출력 단가로 청구하고, 캐시에서 읽은 입력은 입력 토큰으로 세되 훨씬 싸게 청구한다.

    **`cached`가 없으면 최적화 효과를 못 잰다.** 왕복 하나를 줄여 `prompt`가 20% 줄어도 그
    20%가 전부 캐시 히트였으면 청구는 거의 그대로다. 반대도 같다. 제공처가 안 알려 주면 0이다.
    """

    model_config = ConfigDict(frozen=True)

    prompt: int = 0
    cached: int = 0
    completion: int = 0
    reasoning: int = 0
