#!/usr/bin/env python3
"""Give each incident a provisional failure class, used only to stratify sampling.

    python scripts/triage.py corpus/sanitized/owner__repo

Reads sanitized logs and writes triage.jsonl beside them. Classes come from the
GitHub Actions `##[error]` annotations and failing job names, not from a keyword
search of the whole log: `--log-failed` returns the entire failed step, so words
like "test" or "migration" appear in nearly every log regardless of the cause.

These are heuristics for sampling. They are not labels.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

# First match wins, so the more specific and research-relevant classes come first.
CLASSES: list[tuple[str, re.Pattern[str]]] = [
    ("migration", re.compile(r"\bP3\d{3}\b|migrate (?:deploy|dev|resolve).*(?:fail|error)", re.I)),
    ("db_connect", re.compile(r"\bP100[0-3]\b")),
    ("workflow_input", re.compile(r"No app selected|requires \w+ to be selected|Tick \w+ to acknowledge")),
    ("lockfile", re.compile(r"ERR_PNPM_\w*LOCKFILE")),
    ("env_config", re.compile(r"Infisical export|is unset|Add the secret|missing env", re.I)),
    ("stack_startup", re.compile(r"never became reachable", re.I)),
    ("compile", re.compile(r"error TS\d+|#build: command|\bbuild: command")),
    ("test", re.compile(r"#test: command|\d+ failed|AssertionError", re.I)),
    ("lint", re.compile(r"#lint: command|simple-import-sort|Run autofix|\berror\s+.*(?:no-|@typescript-eslint)", re.I)),
]
JOB_FALLBACK = (
    ("Fallow", "static_analysis"),
    ("integration", "integration_test"),
    ("Deploy", "deploy"),
    ("Install, Build", "build_other"),
)
# P-codes are also matched against all log lines, since Prisma prints them outside
# of any ##[error] annotation.
PRISMA_FAILURE = re.compile(r"\b(?:Error: )?P(?:30\d{2}|1001)\b")
# A fix that edits these changes persisted schema or history. This is the
# strongest signal available before labelling for "the fix touches data".
SCHEMA_FILE = re.compile(r"(?:^|/)migrations?/|schema\.prisma$|\.sql$|repair.*\.sh$", re.I)


def read_log(path: Path) -> tuple[list[str], list[str], bool]:
    errors, jobs, prisma = [], [], False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t", 2)
            if len(fields) == 3 and fields[0] not in jobs:
                jobs.append(fields[0])
            message = fields[-1]
            if "##[error]" in message:
                errors.append(message.split("##[error]", 1)[1].strip())
            if PRISMA_FAILURE.search(message):
                prisma = True
                errors.append(re.sub(r"^\S+Z\s+", "", message).strip()[:200])
    return errors, jobs, prisma


def classify(errors: list[str], jobs: list[str]) -> str:
    useful = [e for e in errors if not e.startswith("Process completed with exit code")]
    for name, pattern in CLASSES:
        if any(pattern.search(line) for line in useful):
            return name
    for needle, name in JOB_FALLBACK:
        if any(needle in job for job in jobs):
            return name
    return "unknown"


def main() -> None:
    root = Path(sys.argv[1])
    incidents = json.loads((root / "incidents.json").read_text(encoding="utf-8"))
    records = []
    for index, incident in enumerate(incidents):
        errors: list[str] = []
        jobs: list[str] = []
        prisma = False
        logged = 0
        per_run: Counter = Counter()
        for run_id in incident["failed_run_ids"]:
            path = root / f"run-{run_id}.log"
            if not path.exists():
                continue
            logged += 1
            run_errors, run_jobs, run_prisma = read_log(path)
            per_run[classify(run_errors, run_jobs)] += 1
            errors += run_errors
            jobs += [job for job in run_jobs if job not in jobs]
            prisma = prisma or run_prisma
        records.append({
            "incident": index,
            **{key: incident[key] for key in ("workflow", "branch", "attempts", "first_failed_at",
                                              "resolved", "passed_without_code_change", "compare_url")},
            "runs_with_logs": logged,
            "class_hint": per_run.most_common(1)[0][0] if per_run else "no_log",
            "classes_seen": dict(per_run),
            "prisma_error": prisma,
            "fix_files_known": incident["fix_files"] is not None,
            "fix_touches_schema": any(SCHEMA_FILE.search(f) for f in incident["fix_files"] or []),
            "fix_file_count": len(incident["fix_files"] or []),
            "jobs": jobs,
            "error_sample": list(dict.fromkeys(
                e for e in errors if not e.startswith("Process completed")))[:6],
        })

    out = root / "triage.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"{len(records)} incidents -> {out}")
    for name, count in Counter(r["class_hint"] for r in records).most_common():
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
