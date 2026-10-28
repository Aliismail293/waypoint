"""Loads parsed courses and programs into BigQuery (dataset `waypoint`).

Tables (all replaced on each load, so the load is idempotent):
- courses:        one row per course, with prerequisite/exclusion codes as arrays
- programs:       one row per program, with requirement text and the course codes it mentions
- prerequisites:  one row per (course, required course) edge, for "what does X unlock?" queries
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from waypoint.config import Settings

COURSE_SCHEMA = [
    ("code", "STRING", "REQUIRED"),
    ("title", "STRING", "REQUIRED"),
    ("description", "STRING", "NULLABLE"),
    ("department", "STRING", "REQUIRED"),
    ("level", "INT64", "REQUIRED"),
    ("credits", "FLOAT64", "REQUIRED"),
    ("prerequisites", "STRING", "NULLABLE"),
    ("prerequisite_codes", "STRING", "REPEATED"),
    ("corequisites", "STRING", "NULLABLE"),
    ("exclusions", "STRING", "NULLABLE"),
    ("exclusion_codes", "STRING", "REPEATED"),
    ("recommended_preparation", "STRING", "NULLABLE"),
    ("distribution", "STRING", "NULLABLE"),
    ("hours", "STRING", "NULLABLE"),
    ("delivery", "STRING", "NULLABLE"),
    ("other", "STRING", "NULLABLE"),
    ("area", "STRING", "NULLABLE"),
    ("source_url", "STRING", "NULLABLE"),
    ("loaded_at", "TIMESTAMP", "REQUIRED"),
]
PROGRAM_SCHEMA = [
    ("code", "STRING", "REQUIRED"),
    ("name", "STRING", "REQUIRED"),
    ("program_type", "STRING", "REQUIRED"),
    ("description", "STRING", "NULLABLE"),
    ("enrolment_requirements", "STRING", "NULLABLE"),
    ("completion_requirements", "STRING", "NULLABLE"),
    ("course_codes", "STRING", "REPEATED"),
    ("area", "STRING", "NULLABLE"),
    ("source_url", "STRING", "NULLABLE"),
    ("loaded_at", "TIMESTAMP", "REQUIRED"),
]
PREREQ_SCHEMA = [
    ("course_code", "STRING", "REQUIRED"),
    ("required_code", "STRING", "REQUIRED"),
    ("loaded_at", "TIMESTAMP", "REQUIRED"),
]


def read_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def build_rows(corpus_dir: Path) -> dict[str, list[dict]]:
    loaded_at = datetime.now(UTC).isoformat()
    courses = read_jsonl(corpus_dir / "courses.jsonl")
    programs = read_jsonl(corpus_dir / "programs.jsonl")
    course_fields = {name for name, _, _ in COURSE_SCHEMA}
    program_fields = {name for name, _, _ in PROGRAM_SCHEMA}
    return {
        "courses": [{k: v for k, v in c.items() if k in course_fields} | {"loaded_at": loaded_at} for c in courses],
        "programs": [{k: v for k, v in p.items() if k in program_fields} | {"loaded_at": loaded_at} for p in programs],
        "prerequisites": [
            {"course_code": c["code"], "required_code": req, "loaded_at": loaded_at}
            for c in courses
            for req in c["prerequisite_codes"]
        ],
    }


def load_into_bigquery(corpus_dir: Path, settings: Settings) -> None:
    from google.cloud import bigquery

    client = bigquery.Client(project=settings.gcp_project)
    schemas = {"courses": COURSE_SCHEMA, "programs": PROGRAM_SCHEMA, "prerequisites": PREREQ_SCHEMA}
    for table, rows in build_rows(corpus_dir).items():
        job_config = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField(n, t, mode=m) for n, t, m in schemas[table]],
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        table_id = f"{settings.gcp_project}.{settings.bq_dataset}.{table}"
        client.load_table_from_json(rows, table_id, job_config=job_config).result()
        print(f"loaded {len(rows)} rows into {table_id}")
