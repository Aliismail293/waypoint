from waypoint.tools.catalog import InMemoryCatalog
from waypoint.tools.plan import parse_plan, validate_plan_text

CATALOG = InMemoryCatalog()


def test_parse_plan():
    assert parse_plan("2026 Fall: MAT224, sta256h5; 2027 Winter: MAT301H5") == [
        ("2026 Fall", ["MAT224", "STA256H5"]),
        ("2027 Winter", ["MAT301H5"]),
    ]


def test_valid_plan_orders_prerequisites():
    result = validate_plan_text("2026 Fall: MAT224H5; 2027 Winter: MAT301H5", ["MAT102H5", "MAT223H5"], CATALOG)
    assert result["valid"], result["errors"]
    assert "cal:mathematical-sciences#mat301h5" in result["citations"]


def test_prerequisite_in_same_term_is_an_error():
    result = validate_plan_text("2026 Fall: MAT224H5, MAT301H5", ["MAT102H5", "MAT223H5"], CATALOG)
    assert not result["valid"]
    assert any("MAT301H5" in e and "not met" in e for e in result["errors"])


def test_unknown_course_duplicate_and_exclusion():
    plan = "2026 Fall: MAT999H5, MAT224H5; 2027 Winter: MAT240H5"
    result = validate_plan_text(plan, ["MAT102H5", "MAT223H5", "MAT224H5"], CATALOG)
    errors = " ".join(result["errors"])
    assert "MAT999H5 is not in" in errors
    assert "already completed" in errors
    assert "exclusion" in errors


def test_overload():
    plan = "2026 Fall: MAT102H5, MAT135H5, MAT136H5, MAT223H5, MAT202H5, MAT232H5"
    result = validate_plan_text(plan, [], CATALOG)
    assert any("full load" in e for e in result["errors"])


def test_empty_plan():
    assert validate_plan_text("", [], CATALOG)["valid"] is False
