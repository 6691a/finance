from datetime import UTC, datetime

from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import dag, task


@dag(
    dag_id="test_backend_db_connection",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),  # UTC 2026-01-01 00:00 = KST 2026-01-01 09:00
    catchup=False,
    tags=["test"],
    is_paused_upon_creation=False,
)
def test_backend_db_connection():
    @task
    def check_backend_db():
        connection = PostgresHook(postgres_conn_id="news").get_conn()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user, 1")
                database, user, result = cursor.fetchone()
        finally:
            connection.close()

        if result != 1:
            raise RuntimeError("Backend DB health check returned an unexpected result")

        print(f"Backend DB OK: database={database}, user={user}")

    check_backend_db()


test_backend_db_connection = test_backend_db_connection()
