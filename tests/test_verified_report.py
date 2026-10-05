import pytest

from src.verified_report import trajectory_metrics, collect


def test_usage_includes_billed_format_error_responses():
    response = {"usage": {"prompt_tokens": 12, "completion_tokens": 3},
                "agent_tool_repairs": [{"kind": "observed_typo"}]}
    trajectory = {"info": {"model_stats": {"api_calls": 2}}, "messages": [
        {"role": "assistant", "extra": {"response": response}},
        {"role": "user", "content": "Tool call error:\ninvalid tool", "extra": {"response": response}},
        {"role": "user", "content": "issue prompt mentioning Tool call error:"},
    ]}
    metrics = trajectory_metrics(trajectory)
    assert metrics["input_tokens"] == 24
    assert metrics["output_tokens"] == 6
    assert metrics["responses_with_usage"] == 2
    assert metrics["format_errors"] == 1


def test_final_report_refuses_partial_batch(tmp_path):
    (tmp_path / "status.json").write_text('{"state":"running"}')
    with pytest.raises(ValueError, match="complete"):
        collect(tmp_path)
