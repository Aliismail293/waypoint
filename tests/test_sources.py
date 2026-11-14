from waypoint.sources import locate_passage

CHUNK = (
    "MAT224H5 Linear Algebra II\n"
    "Vector spaces, linear maps and eigenvalues.\n"
    "Prerequisites: MAT102H5 and MAT223H5\n"
    "Exclusions: MAT240H5"
)


def test_highlights_the_line_that_backs_the_claim():
    passage = locate_passage(CHUNK, "MAT224H5 has the prerequisites MAT102H5 and MAT223H5")
    assert passage is not None
    assert CHUNK[passage.start : passage.end] == "Prerequisites: MAT102H5 and MAT223H5"
    assert "prerequisites" in passage.matched


def test_no_overlap_means_no_highlight():
    assert locate_passage(CHUNK, "The campus bookstore opens at nine") is None
    assert locate_passage(CHUNK, "") is None


def test_one_shared_common_word_is_not_enough():
    assert locate_passage(CHUNK, "Vector games are popular") is None


def test_span_offsets_are_exact_for_multiline_text():
    passage = locate_passage(CHUNK, "Exclusions include MAT240H5")
    assert passage is not None and CHUNK[passage.start : passage.end] == "Exclusions: MAT240H5"
