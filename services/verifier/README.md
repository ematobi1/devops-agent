# verifier

Deterministic checks — no LLM call, same input always produces the same
findings. Two planned checks, built sequentially against concrete need
rather than speculatively:

1. **`check_proposed_actions()` — pre-approval sanity check (built).** Runs
   inside `scripts/diagnose.py`, after `run_task()` returns but before the
   `ApprovalRequest` is persisted. Catches bracketed placeholder text (not
   markdown links), TODO/FIXME/TBD/PLACEHOLDER markers, and question-phrased
   actions (a literal `?`). If it finds anything, `diagnose.py` refuses to
   persist the proposal — fail closed, so a known-bad proposal never reaches
   a human reviewer or `scripts/apply.py`.
2. **Post-apply diff verification (not yet built).** Will fetch the actual
   result of an applied change (e.g. a PR's real diff) and confirm it's
   consistent with what was approved — formalizing the manual `gh pr diff`
   check done by hand throughout this project's own validation. Build once
   #1 has landed and been exercised for real.

## Why this is narrow

Distinguishing "a legitimate executable action" from "secretly a question
for a human" in the general case requires understanding what the action
actually *means* — that's a judgment call, not a deterministic
same-input-same-output mapping, so it isn't attempted here beyond the
unambiguous `?` signal. The real fix for that class of problem is prompt
quality (see `orchestrator.orchestrate.SYSTEM_PROMPT`); this check is a
second, independent backstop, not the primary mechanism.

## Wiring

`orchestrator` and `approval` never import `verifier`, and `verifier`
imports neither — it's wired in at the glue-script layer
(`scripts/diagnose.py`), same as `apply.py` wires `orchestrator`+`approval`
together without either service depending on the other directly.

## Tests

`pytest services/verifier/tests` — pure function, no fixtures beyond plain
strings, includes a regression test for the exact real-world case
(a bracketed placeholder inside a proposed markdown edit) that motivated
building this.
