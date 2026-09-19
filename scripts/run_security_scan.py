"""Pre-deployment security gate. Exits non-zero on any unwaived high or
critical finding. Scans: tracked files and full git history for secrets;
every approval and change record for decision-integrity problems; every
authentication path (MCP templates, web UI, containers, CI, redactor);
Python SAST (bandit) and known-vulnerable dependencies (pip-audit).

Accepted risks live in security-waivers.json and must carry a reason.

Usage:
    .venv/Scripts/python scripts/run_security_scan.py [--decisions-dir data] [--skip-external]
"""

import argparse
import sys
from pathlib import Path

from security_scan import load_waivers, run_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions-dir", type=Path, default=REPO_ROOT / "data")
    parser.add_argument("--skip-external", action="store_true", help="skip bandit and pip-audit (offline use only)")
    args = parser.parse_args()

    from ui.pipeline import redact_secrets

    report = run_scan(
        REPO_ROOT,
        approvals_path=args.decisions_dir / "approvals.jsonl",
        changes_path=args.decisions_dir / "changes.jsonl",
        waivers=load_waivers(REPO_ROOT / "security-waivers.json"),
        redactor=redact_secrets,
        include_external=not args.skip_external,
    )
    print(report.render())
    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
