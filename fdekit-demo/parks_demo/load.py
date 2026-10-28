"""Load the raw CSV rows into their own schema on the shared Cloud SQL instance (no new paid resources).

PARKS_DB_PASSWORD=... uv run python -m parks_demo.load      # via cloud-sql-proxy on 127.0.0.1:5432
"""

import psycopg

from parks_demo.config import get_settings
from parks_demo.source import CsvSource


def main() -> None:
    s = get_settings()
    rows = CsvSource().rows()
    dsn = f"host={s.db_host} port={s.db_port} dbname={s.db_name} user={s.db_user} password={s.db_password}"
    with psycopg.connect(dsn) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {s.db_schema}")
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {s.db_schema}.attendance_raw "
            "(place text NOT NULL, fiscal_year text NOT NULL, value text NOT NULL, PRIMARY KEY (place, fiscal_year))"
        )
        conn.execute(f"TRUNCATE {s.db_schema}.attendance_raw")
        with conn.cursor().copy(f"COPY {s.db_schema}.attendance_raw (place, fiscal_year, value) FROM STDIN") as copy:
            for r in rows:
                copy.write_row((r["place"], r["fiscal_year"], r["value"]))
    print(f"loaded {len(rows)} raw rows into {s.db_name}.{s.db_schema}.attendance_raw")


if __name__ == "__main__":
    main()
