# security

Deterministic security checks: same input, same findings, no LLM call. Three jobs.

**1. Pre-deployment gate** - `scripts/run_security_scan.py`, exits 1 on any unwaived high or critical finding. Runs in CI (`security` job) and locally.

| Layer | What | Source |
|---|---|---|
| Secrets | every tracked file and every added line in all git history; forbidden tracked files (`.env`, `*.jsonl`, rendered MCP configs, keys) | `secrets_scan.py` |
| Decisions | every approval and change record: append-only violations, edits after review, missing or placeholder approver, apply without approval, replayed apply, unattributed apply, secrets in records, high-risk operations in approved proposals | `decision_audit.py` |
| Authentication | MCP templates (pinning, read-only enforcement, literal credentials), web UI (auth, Host/Origin checks, client-supplied identity, approve+apply collapse), containers (root, pins, bind address, credential mounts, `.dockerignore`), CI (token scope, action pinning), the secret redactor's coverage | `auth_audit.py` |
| SAST / dependencies | bandit (medium and above), pip-audit. A tool that cannot run is itself a high finding | `external.py` |

**2. Runtime guards** - `guards.py`, `gate.py`. Wired into `diagnose.py`, `generate_dockerfile.py`, `apply.py`, `decide.py` and `ui/app.py`.

- *Before anything starts* (`require_preflight`): the GitHub read-only flag must be exactly `--read-only=true` or `--read-only=false`; a read stage may never see a write-capable server; classic PATs, long-lived AWS keys, an unexpected `ANTHROPIC_API_KEY`, and template or tracked-file findings block the run.
- *Every input* (`require_clean_input`, UI `start_run`): pasted credentials, prompt-injection shapes, hidden or bidi characters, encoded blobs.
- *Every output* (`screen_proposal`): a secret-shaped value refuses persistence; other findings (pipe-to-shell, hook bypass, off-allowlist URLs, injection echoes) are attached as `verifier_warnings` so the approver sees them.
- `apply.py` re-screens approved content before executing, records `applied_by`, and withholds secret-shaped text from the audit log.

**3. Waivers** - `security-waivers.json` at the repo root. Each entry needs a `rule` and a non-empty `reason`. Waived findings are still printed as `WAIVED`.

## Limits

Pattern-based. Known evasions that are not caught: character substitution, spacing between letters, other languages, paraphrases with no trigger words, base64-wrapped instructions. Indirect injection (instructions hidden in a repo file the model reads) never passes through an input guard; the defenses there are the tool allowlists, the read-only server flag, and the human approval. The eval corpus was written by the same author as the patterns, so its 100% is an upper bound.

## Tests and evals

- Gate: `pytest`, in the default suite, no network. Includes `test_this_repository_passes_its_own_gate`.
- Eval: `pytest services/security/evals -q -s`. Recall on seeded injection paraphrases (threshold 0.85), false positives on benign traffic (threshold 0), every credential format in eight disguises.
