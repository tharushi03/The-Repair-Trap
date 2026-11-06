#!/usr/bin/env python3
"""Pull failed GitHub Actions runs, their logs, and candidate fixes.

    python scripts/extract_runs.py owner/repo \
        --workflows "CI,Deploy"

Writes to corpus/raw/<owner>__<repo>/ (gitignored):
    runs.json        every run in the window, trimmed to the fields below
    incidents.json   failures grouped into incidents, each with a candidate fix
    run-<id>.log     output of the failed steps

An incident is an unbroken streak of failed runs of one workflow on one branch,
closed by the next successful run. That success's commit is a *candidate* fix:
comparing it with the last failure gives the diff to read when labelling
`correct_fix`. It still needs a human check, because the commit range can hold
unrelated work and a success can also mean the failure was flaky.

Safe to re-run: logs already on disk are skipped, and runs are merged into the
existing runs.json so incidents older than the window survive. GitHub deletes run
logs after 90 days by default, so re-extract at least monthly. extraction.json
records when each extraction ran.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

RUN_FIELDS = ("id", "name", "path", "event", "head_branch", "head_sha", "conclusion",
              "created_at", "run_attempt", "html_url")
# Outcomes that say nothing about whether the code was fixed.
INCONCLUSIVE = {"cancelled", "skipped", "stale", "neutral", "action_required", None}


def gh(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["gh", *args], capture_output=True)


def fetch_runs(repo: str, since: datetime) -> list[dict]:
    """Page newest-first until past the cutoff.

    The unfiltered listing is used on purpose: GitHub caps filtered listings at
    1,000 results, and successes are needed anyway to close incidents.
    """
    runs: list[dict] = []
    page = 1
    while True:
        result = gh("api", f"repos/{repo}/actions/runs?per_page=100&page={page}")
        if result.returncode != 0:
            sys.exit(f"gh api failed: {result.stderr.decode(errors='replace')}")
        batch = json.loads(result.stdout)["workflow_runs"]
        if not batch:
            return runs
        for run in batch:
            if parse_time(run["created_at"]) < since:
                return runs
            kept = {field: run.get(field) for field in RUN_FIELDS}
            kept["pull_requests"] = [pr["number"] for pr in run.get("pull_requests") or []]
            runs.append(kept)
        print(f"  page {page}: {len(runs)} runs", flush=True)
        page += 1


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merge_runs(path: Path, fresh: list[dict]) -> list[dict]:
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    merged = {run["id"]: run for run in known}
    merged.update({run["id"]: run for run in fresh})
    return sorted(merged.values(), key=lambda r: r["created_at"], reverse=True)


def build_incidents(runs: list[dict], repo: str, workflows: set[str] | None) -> list[dict]:
    # A workflow that dies before its YAML is parsed reports its file path as its
    # name, so map those back or they vanish from a filter on workflow names.
    names = {run["path"]: run["name"] for run in runs if run["name"] != run["path"]}

    streams: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for run in runs:
        name = names.get(run["path"], run["name"])
        if workflows is None or name in workflows:
            streams[name, run["head_branch"]].append(run)

    incidents = []
    for (workflow, branch), stream in streams.items():
        stream.sort(key=lambda r: r["created_at"])
        open_failures: list[dict] = []
        for run in stream:
            if run["conclusion"] == "failure":
                open_failures.append(run)
            elif run["conclusion"] == "success" and open_failures:
                incidents.append(make_incident(repo, workflow, branch, open_failures, run))
                open_failures = []
            elif run["conclusion"] not in INCONCLUSIVE and open_failures:
                open_failures.append(run)  # timed_out and similar count as failing
        if open_failures:
            incidents.append(make_incident(repo, workflow, branch, open_failures, None))

    incidents.sort(key=lambda i: i["first_failed_at"])
    return incidents


def make_incident(repo, workflow, branch, failures, success) -> dict:
    last = failures[-1]
    same_commit = bool(success) and success["head_sha"] == last["head_sha"]
    return {
        "workflow": workflow,
        "branch": branch,
        "attempts": len(failures),
        "first_failed_at": failures[0]["created_at"],
        "last_failed_at": last["created_at"],
        "failed_run_ids": [run["id"] for run in failures],
        "failed_sha": last["head_sha"],
        "pull_requests": sorted({n for run in failures for n in run["pull_requests"]}),
        "resolved": bool(success),
        "fix_sha": success["head_sha"] if success else None,
        # Passing on the identical commit points at flakiness or an environment
        # change, so there is no code diff to learn from.
        "passed_without_code_change": same_commit,
        "compare_url": (f"https://github.com/{repo}/compare/{last['head_sha']}...{success['head_sha']}"
                        if success and not same_commit else None),
    }


def fetch_log(repo: str, run_id: int, out_dir: Path) -> str:
    path = out_dir / f"run-{run_id}.log"
    if path.exists():
        return "ok"
    result = gh("run", "view", str(run_id), "--repo", repo, "--log-failed")
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").lower()
        return "unavailable" if "log not found" in message or "expired" in message else "error"
    if not result.stdout.strip():
        return "empty"  # e.g. the run failed before any step started
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(result.stdout)
    tmp.replace(path)  # an interrupted run never leaves a partial log behind
    return "ok"


def fetch_fix_files(repo: str, incident: dict) -> list[str] | None:
    """Files changed between the last failure and the fix. None if GitHub cannot
    compare them, for example after a force-push removed the failed commit."""
    if not incident["compare_url"]:
        return None
    base_head = f"{incident['failed_sha']}...{incident['fix_sha']}"
    result = gh("api", f"repos/{repo}/compare/{base_head}", "--jq", "[.files[].filename]")
    return json.loads(result.stdout) if result.returncode == 0 else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("--workflows", help="comma-separated names; default is all")
    parser.add_argument("--since-days", type=int, default=90)
    parser.add_argument("--out", type=Path, default=Path("corpus/raw"))
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    workflows = {w.strip() for w in args.workflows.split(",")} if args.workflows else None
    out_dir = args.out / args.repo.replace("/", "__")
    out_dir.mkdir(parents=True, exist_ok=True)

    since = datetime.now(timezone.utc) - timedelta(days=args.since_days)
    print(f"listing runs for {args.repo} since {since:%Y-%m-%d}", flush=True)
    fresh = fetch_runs(args.repo, since)
    runs = merge_runs(out_dir / "runs.json", fresh)
    (out_dir / "runs.json").write_text(json.dumps(runs, indent=1), encoding="utf-8")

    incidents = build_incidents(runs, args.repo, workflows)
    run_ids = [run_id for incident in incidents for run_id in incident["failed_run_ids"]]
    print(f"{len(runs)} runs, {len(incidents)} incidents, {len(run_ids)} logs to fetch", flush=True)

    with ThreadPoolExecutor(args.jobs) as pool:
        statuses = dict(zip(run_ids, pool.map(lambda i: fetch_log(args.repo, i, out_dir), run_ids)))
        fix_files = list(pool.map(lambda i: fetch_fix_files(args.repo, i), incidents))

    for incident, files in zip(incidents, fix_files):
        incident["log_status"] = {str(i): statuses[i] for i in incident["failed_run_ids"]}
        incident["fix_files"] = files
    (out_dir / "incidents.json").write_text(json.dumps(incidents, indent=1), encoding="utf-8")

    tally: dict[str, int] = defaultdict(int)
    for status in statuses.values():
        tally[status] += 1
    print(f"log results: {dict(tally)}")

    history_path = out_dir / "extraction.json"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    history.append({"extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "repo": args.repo, "window_start": since.date().isoformat(),
                    "workflows": sorted(workflows) if workflows else None,
                    "runs_total": len(runs), "incidents": len(incidents), "logs": dict(tally)})
    history_path.write_text(json.dumps(history, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
