"""리포지토리들이 공유하는 것. **리소스 하나에만 쓰이는 것은 여기 두지 않는다.**"""

from pydantic import BaseModel, ConfigDict

# 한 번에 돌려줄 목록 크기. 상한을 두는 이유는 날짜 구간이 넓을 때의 폭주를 막는 것이고,
# 실질 페이지네이션은 구간을 좁히는 것이다 — 자연키가 (run_date, run_slot, subject)라
# 날짜를 좁히면 결과가 곧 한 페이지가 된다.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# 목록 기본 창(일). `to`가 오늘이고 `from`이 이만큼 전이다.
DEFAULT_WINDOW_DAYS = 13


class RowBundle(BaseModel):
    """한 응답이 필요로 하는 행을 한 번에 담는다.

    맨 tuple로 넘기면 부르는 쪽이 자리로 푼다 — 칸이 늘 때 조용히 어긋난다.
    ORM 객체를 담으므로 `arbitrary_types_allowed`가 필요하다.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
