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
"""

import re
from dataclasses import dataclass

# A bracketed span not immediately followed by "(" - excludes markdown links
# like "[label](url)", which are legitimate, while still catching a bare
# placeholder like "[add other MCP operations being exercised]".
_BRACKET_PLACEHOLDER = re.compile(r"\[[^\]]+\](?!\()")

_TODO_MARKER = re.compile(r"\b(TODO|FIXME|TBD|XXX|PLACEHOLDER)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    action: str
    reason: str


def check_proposed_actions(actions: list[str]) -> list[Finding]:
    findings = []
    for action in actions:
        if _BRACKET_PLACEHOLDER.search(action):
            findings.append(Finding(action=action, reason="contains bracketed placeholder text (not a markdown link)"))
        if _TODO_MARKER.search(action):
            findings.append(Finding(action=action, reason="contains a TODO/FIXME/TBD/PLACEHOLDER marker"))
        if "?" in action:
            findings.append(Finding(action=action, reason="phrased as a question, not an executable instruction"))
    return findings
