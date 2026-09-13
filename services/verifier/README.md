# verifier

Deterministic checks — no LLM call, same input always produces the same
findings. Two planned checks, built sequentially against concrete need
rather than speculatively:

1. **`check_proposed_actions()` — pre-approval sanity check (built, revised
   2026-09-13 after a real false positive).** Runs in `scripts/diagnose.py`
   and `scripts/generate_dockerfile.py`, before an `ApprovalRequest` is
   persisted. Catches bracketed placeholder text (not markdown links or
   valid JSON literals — see below), TODO/FIXME/TBD/PLACEHOLDER markers, and
   question-phrased instructions (a literal `?`). Only ever scans each
   action's `instruction` field, never its optional `payload` — raw
   generated content (file contents, code, config) is never prose-scanned.
   Findings are attached to the `ApprovalRequest` as `verifier_warnings` and
   the request is persisted anyway — annotate, not block; `scripts/decide.py`
   surfaces them again at decision time.

   **The bracket check found a real false positive:** a generated
   Dockerfile's `CMD ["nginx", "-g", "daemon off;"]` — correct, recommended
   exec-form syntax — was flagged as a placeholder, because at the time the
   whole file was embedded in one action string alongside its instruction.
   Fixed two ways: (a) the heuristic now excludes anything that parses as
   valid JSON (exec-form arrays, Terraform lists, quoted YAML flow
   sequences) and only flags bracket content that also reads like prose
   (starts with add/insert/replace/fill/todo/fixme/xxx/your, or contains
   "e.g."); (b) structurally, `ApprovalRequest` actions now carry
   `payload` separately from `instruction`, so generated file content is
   never in the prose-scanned field at all — the actual fix, since (a)
   alone would just recur for the next bracket-using language.
   Blocking-vs-annotating also changed as a direct result: the original
   false positive cost a full re-run to notice; now it would cost a glance
   at `scripts/decide.py`'s printed warning.
2. **`check_diff()`/`fetch_pr_diff_and_state()` — post-apply diff
   verification (built).** Given a `ChangeRecord`, `scripts/verify_apply.py`
   extracts the PR it references, fetches the real diff and PR state via
   `gh` (a known-PR lookup by number — deterministic I/O, not a judgment
   call, same reasoning as `orchestrator` shelling out to `claude`/`docker`),
   and checks two structural invariants: the diff isn't empty (catches
   "apply claimed success but changed nothing"), and it doesn't delete a
   file (apply's tool allowlist should make this impossible — a
   trust-but-verify backstop on that boundary). Does **not** check PR state
   (open/merged/closed) — an earlier version did, flagging anything but
   `OPEN`, and was caught producing a false positive on a live, real PR that
   a human had legitimately merged after review. A PR's terminal state is
   decided by subsequent human action, not by apply, so there's no way to
   tell "apply merged its own PR" (impossible given the tool allowlist) from
   "a human merged it later" just by looking at current state — removed
   rather than kept wrong.

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
