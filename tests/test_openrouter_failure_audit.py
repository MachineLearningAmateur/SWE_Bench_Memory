from src.openrouter_failure_audit import classify, commands, source_edits


def record(**fields):
    return {"exit_status": "Submitted", "error": None, "model_patch": "", **fields}


def test_source_edits_ignore_scratch_scripts_and_patch_file():
    cmds = ["grep -n x a.py", "sed -i 's/a/b/' lib/a.py", "echo 'x' > /testbed/test_repro.py",
            "git diff -- lib/a.py > patch.txt", "cat <<EOF > lib/b.py\nx\nEOF"]
    assert source_edits(cmds) == ["sed -i 's/a/b/' lib/a.py", "cat <<EOF > lib/b.py\nx\nEOF"]


def test_classify_orders_stops_before_patch_state():
    sed = ["sed -i 's/a/b/' lib/a.py"]
    assert classify(record(model_patch="diff"), sed, True) == "resolved"
    assert classify(record(exit_status=None, error={"type": "BadRequestError"}), sed, False) == "context_overflow"
    assert classify(record(exit_status="RepeatedFormatError"), sed, False) == "repeated_format_error"
    assert classify(record(exit_status="TimeExceeded"), sed, False) == "time_limit"
    assert classify(record(model_patch="diff"), sed, False) == "wrong_patch"
    assert classify(record(), sed, False) == "empty_edit_no_change"
    assert classify(record(), ["cat lib/a.py"], False) == "empty_no_edit"


def test_commands_reads_actions_in_order():
    messages = [{"role": "system", "content": "x"},
                {"role": "assistant", "extra": {"actions": [{"command": "ls"}]}},
                {"role": "user", "extra": {"returncode": 0}},
                {"role": "assistant", "extra": {"actions": [{"command": "pwd"}]}}]
    assert commands(messages) == ["ls", "pwd"]
