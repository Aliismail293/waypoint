"""The "legacy" source: Parks Canada's yearly attendance CSVs, as published, or the same rows in Postgres.

The files are what a real legacy export looks like: a BOM, one file per fiscal year with the year only in a
column header, files in different encodings (UTF-8 with a BOM, or Windows-1252), numbers formatted as text ("41,145"), status codes instead of numbers (UR = unable to
report, TC = temporarily closed), and footnote rows at the bottom. The source returns raw rows untouched;
cleaning them up is the adapter's job.
"""

import csv
import re
from pathlib import Path
from typing import Protocol, TypedDict

DATA = Path(__file__).parent.parent / "data"
YEAR_HEADER = re.compile(r"(\d{4}-\d{2}) FY Attendance")


class RawRow(TypedDict):
    place: str
    fiscal_year: str
    value: str


class Source(Protocol):
    def rows(self) -> list[RawRow]: ...


def read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")  # the older exports are Windows-1252 (French place names)


class CsvSource:
    def __init__(self, directory: Path = DATA) -> None:
        self.directory = directory

    def rows(self) -> list[RawRow]:
        out: list[RawRow] = []
        for path in sorted(self.directory.glob("parks-canada-attendance-*.csv")):
            reader = csv.reader(read_text(path).splitlines())
            header = next(reader)
            match = YEAR_HEADER.search(header[1])
            if not match:
                raise ValueError(f"{path.name}: unexpected header {header!r}")
            for row in reader:
                if len(row) >= 2 and row[0].strip():
                    out.append({"place": row[0], "fiscal_year": match.group(1), "value": row[1]})
        return out


class PostgresSource:
    """The same raw rows, loaded into a separate schema on the shared Cloud SQL instance (see load.py)."""

    def __init__(self, dsn: str, schema: str) -> None:
        self.dsn = dsn
        self.schema = schema

    def rows(self) -> list[RawRow]:
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            records = conn.execute(f"SELECT place, fiscal_year, value FROM {self.schema}.attendance_raw").fetchall()
        return [{"place": p, "fiscal_year": y, "value": v} for p, y, v in records]
