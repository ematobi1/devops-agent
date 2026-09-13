"""Deterministic, pre-approval sanity check on ApprovalRequest.proposed_actions.

No LLM call - pure pattern matching, same input always produces the same
findings. Motivated by a real bug: a diagnostic run once produced an action
containing a literal bracketed placeholder ("[add other MCP operations...]")
and a separate "action" that was actually a question for the operator. The
root cause was fixed in orchestrator's SYSTEM_PROMPT, but prompt-following
isn't calibrated to 100% - this is a second, independent, deterministic
layer that catches the same failure shape even if the prompt ever slips,
before a human wastes time reviewing a broken proposal.

Deliberately narrow: distinguishing "a legitimate executable action" from
"secretly a question for a human" in the general case requires understanding
what the action actually means - that's a judgment call (latent space), not
a deterministic one, so it's not attempted here beyond the unambiguous "?"
signal. See docs/ARCHITECTURE.md "services/verifier" for why this boundary
was drawn where it was.

Only ever scans the `instruction` field of each proposed action, never
`payload`. Found live: a generated Dockerfile's `CMD ["nginx", "-g",
"daemon off;"]` (correct, recommended exec-form syntax) was flagged as a
bracketed placeholder, because the whole file's content had been embedded
in a single action string alongside its instruction. Fixed structurally by
splitting ApprovalRequest actions into instruction (prose, checked) and
payload (raw generated content, never checked) - this isn't a narrower
regex working around one case, it makes the whole failure class impossible
for any future bracket-using language (Terraform lists, YAML flow
sequences, JSON, ...), not just Dockerfiles.
"""

import json
import re
from dataclasses import dataclass

# Any bracketed span not immediately followed by "(" - excludes markdown
# links like "[label](url)". Whether it's actually a placeholder is decided
# by _looks_like_prose_placeholder(), not by this pattern alone.
_BRACKET_SPAN = re.compile(r"\[[^\]]+\](?!\()")

_TODO_MARKER = re.compile(r"\b(TODO|FIXME|TBD|XXX|PLACEHOLDER)\b", re.IGNORECASE)

_PROSE_PLACEHOLDER_STARTS = ("add", "insert", "replace", "fill", "todo", "fixme", "xxx", "your")


def _looks_like_prose_placeholder(bracket_span: str) -> bool:
    """bracket_span includes the surrounding [ ]. A structural literal - a
    JSON/exec-form array, a Terraform list, a quoted YAML flow sequence -
    parses as valid JSON and is never a placeholder regardless of its
    wording. Only bracket text that both fails to parse as JSON AND reads
    like prose instructions (starts with a placeholder-ish verb, or
    contains "e.g.") counts."""
    try:
        json.loads(bracket_span)
        return False
    except (json.JSONDecodeError, ValueError):
        pass

    inner = bracket_span[1:-1].strip().lower()
    if "e.g." in inner:
        return True
    return inner.startswith(_PROSE_PLACEHOLDER_STARTS)


@dataclass(frozen=True)
class Finding:
    action: str
    reason: str


def check_proposed_actions(actions: list[dict]) -> list[Finding]:
    findings = []
    for action in actions:
        instruction = action["instruction"]
        for bracket_span in _BRACKET_SPAN.findall(instruction):
            if _looks_like_prose_placeholder(bracket_span):
                findings.append(Finding(action=instruction, reason="contains bracketed placeholder text (not a markdown link or literal array)"))
                break
        if _TODO_MARKER.search(instruction):
            findings.append(Finding(action=instruction, reason="contains a TODO/FIXME/TBD/PLACEHOLDER marker"))
        if "?" in instruction:
            findings.append(Finding(action=instruction, reason="phrased as a question, not an executable instruction"))
    return findings
