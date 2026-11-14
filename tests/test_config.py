

def test_model_ids_are_checked_at_startup_in_live_mode_only() -> None:
    import pytest

    from waypoint.config import Settings, check_model_ids, model_ids

    live = Settings(llm="vertex")
    assert set(model_ids(live)) == {"agent", "critic", "router", "embedding"}
    check_model_ids(live)  # defaults are valid
    check_model_ids(Settings(llm="mock", agent_model=""))  # offline runs never check
    with pytest.raises(ValueError, match="Invalid model id"):
        check_model_ids(Settings(llm="vertex", agent_model=""))
    with pytest.raises(ValueError, match="router"):
        check_model_ids(Settings(llm="vertex", router_model="gpt 4"))
