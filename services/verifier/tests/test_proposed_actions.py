from verifier import check_proposed_actions


def _action(instruction, payload=None):
    return {"instruction": instruction, "payload": payload} if payload is not None else {"instruction": instruction}


def test_clean_action_produces_no_findings():
    assert check_proposed_actions([_action("revert commit abc123 on main")]) == []


def test_bracket_placeholder_is_flagged():
    findings = check_proposed_actions([_action("- Read access via GitHub MCP server: [add other MCP operations being exercised]")])
    assert len(findings) == 1
    assert "placeholder" in findings[0].reason


def test_markdown_link_is_not_flagged_as_placeholder():
    """Regression: a bracketed span immediately followed by '(' is a real
    markdown link, not a placeholder - must not be flagged."""
    findings = check_proposed_actions([_action("Reference the design doc [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)")])
    assert findings == []


def test_todo_marker_is_flagged_case_insensitively():
    for marker in ("TODO", "todo", "Fixme", "TBD", "XXX", "PLACEHOLDER"):
        findings = check_proposed_actions([_action(f"add error handling here ({marker})")])
        assert len(findings) == 1, f"expected a finding for marker {marker!r}"
        assert "marker" in findings[0].reason


def test_todo_substring_inside_another_word_is_not_flagged():
    """Word-boundary check: 'TODOS' should not trigger on the 'TODO' substring."""
    assert check_proposed_actions([_action("update the TODOS.md file")]) == []


def test_question_mark_is_flagged():
    findings = check_proposed_actions([_action("Confirm the actual list of MCP operations this repo validates?")])
    assert len(findings) == 1
    assert "question" in findings[0].reason


def test_real_world_motivating_case_is_caught():
    """The exact action shape that motivated building this check at all."""
    action = (
        "Add a bullet: Read access to repo file contents via the GitHub MCP server\n"
        "- [add other MCP operations being exercised, e.g. issue/PR listing, commit reads]"
    )
    findings = check_proposed_actions([_action(action)])
    assert len(findings) == 1
    assert "placeholder" in findings[0].reason


def test_single_action_can_trigger_multiple_findings():
    findings = check_proposed_actions([_action("Confirm whether to use [add the specific approach here]?")])
    reasons = {f.reason for f in findings}
    assert len(findings) == 2
    assert any("placeholder" in r for r in reasons)
    assert any("question" in r for r in reasons)


def test_checks_each_action_independently():
    findings = check_proposed_actions([_action("revert commit abc123"), _action("[add real content here] text")])
    assert len(findings) == 1
    assert findings[0].action == "[add real content here] text"


def test_empty_list_produces_no_findings():
    assert check_proposed_actions([]) == []


# Regression tests for the false positive found live: a generated Dockerfile's
# exec-form CMD was flagged as a bracketed placeholder.


def test_dockerfile_exec_form_cmd_is_not_flagged():
    findings = check_proposed_actions([_action('Set the entrypoint to CMD ["nginx", "-g", "daemon off;"]')])
    assert findings == []


def test_terraform_list_is_not_flagged():
    findings = check_proposed_actions([_action('Set the subnet list to availability_zones = ["us-east-1a", "us-east-1b"]')])
    assert findings == []


def test_yaml_flow_sequence_is_not_flagged():
    findings = check_proposed_actions([_action("Set environments: [dev, staging, prod]")])
    assert findings == []


def test_quoted_yaml_flow_sequence_is_not_flagged():
    findings = check_proposed_actions([_action('Set environments: ["dev", "staging", "prod"]')])
    assert findings == []


def test_prose_placeholder_with_leading_verb_still_flagged_alongside_json_arrays():
    """The narrowed heuristic must not become so permissive it stops
    catching genuine placeholders just because some bracket in the same
    instruction happens to be valid JSON."""
    findings = check_proposed_actions(
        [_action('Use ports [8080, 9090] and [add the remaining ports here]')]
    )
    assert len(findings) == 1
    assert "placeholder" in findings[0].reason


def test_payload_is_never_scanned_even_with_obvious_placeholder_text():
    """Structural fix: payload holds raw generated content and must never
    be prose-scanned, regardless of what it contains."""
    findings = check_proposed_actions(
        [_action("Create a Dockerfile with exactly this content, via a PR.", payload="[add TODO here]?")]
    )
    assert findings == []
