"""`sql/` 볼륨에 있는 쿼리 파일을 읽는다.

배포 Airflow는 `sql/`을 `/opt/airflow/sql`로 마운트한다. 저장소의 `airflow/`가 컨테이너의
`/opt/airflow`에 대응하므로, `AIRFLOW_HOME`이 있으면 그 값을, 없으면 이 파일 기준 상위 폴더를
뿌리로 쓴다. 로컬 pytest와 컨테이너가 같은 파일을 읽는다.
"""

from pathlib import Path

from modules.utility import AIRFLOW_HOME

SQL_ROOT = Path(AIRFLOW_HOME) / "sql" if AIRFLOW_HOME else Path(__file__).resolve().parents[1] / "sql"


def read_sql(*parts: str) -> str:
    """`sql/` 아래 경로 조각으로 쿼리 파일 하나를 읽는다. 예: `read_sql("postgres", "x", "insert.sql")`."""
    return SQL_ROOT.joinpath(*parts).read_text(encoding="utf-8")
