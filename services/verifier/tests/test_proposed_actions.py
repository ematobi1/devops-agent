from verifier import check_proposed_actions


def test_clean_action_produces_no_findings():
    assert check_proposed_actions(["revert commit abc123 on main"]) == []


def test_bracket_placeholder_is_flagged():
    findings = check_proposed_actions(["- Read access via GitHub MCP server: [add other MCP operations being exercised]"])
    assert len(findings) == 1
    assert "placeholder" in findings[0].reason


def test_markdown_link_is_not_flagged_as_placeholder():
    """Regression: a bracketed span immediately followed by '(' is a real
    markdown link, not a placeholder - must not be flagged."""
    findings = check_proposed_actions(["Reference the design doc [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)"])
    assert findings == []


def test_todo_marker_is_flagged_case_insensitively():
    for marker in ("TODO", "todo", "Fixme", "TBD", "XXX", "PLACEHOLDER"):
        findings = check_proposed_actions([f"add error handling here ({marker})"])
        assert len(findings) == 1, f"expected a finding for marker {marker!r}"
        assert "marker" in findings[0].reason


def test_todo_substring_inside_another_word_is_not_flagged():
    """Word-boundary check: 'TODOS' should not trigger on the 'TODO' substring."""
    assert check_proposed_actions(["update the TODOS.md file"]) == []


def test_question_mark_is_flagged():
    findings = check_proposed_actions(["Confirm the actual list of MCP operations this repo validates?"])
    assert len(findings) == 1
    assert "question" in findings[0].reason


def test_real_world_motivating_case_is_caught():
    """The exact action shape that motivated building this check at all."""
    action = (
        "Add a bullet: Read access to repo file contents via the GitHub MCP server\n"
        "- [add other MCP operations being exercised, e.g. issue/PR listing, commit reads]"
    )
    findings = check_proposed_actions([action])
    assert len(findings) == 1
    assert "placeholder" in findings[0].reason


def test_single_action_can_trigger_multiple_findings():
    findings = check_proposed_actions(["Confirm whether to use [approach A or B]?"])
    reasons = {f.reason for f in findings}
    assert len(findings) == 2
    assert any("placeholder" in r for r in reasons)
    assert any("question" in r for r in reasons)


def test_checks_each_action_independently():
    findings = check_proposed_actions(["revert commit abc123", "add [real content here] text"])
    assert len(findings) == 1
    assert findings[0].action == "add [real content here] text"


def test_empty_list_produces_no_findings():
    assert check_proposed_actions([]) == []
