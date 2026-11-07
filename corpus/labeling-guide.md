# Labeling guide

How each failure in `corpus/labelled/failures.jsonl` is labelled. The goal is that
a second person, given this guide and the same evidence, picks the same labels.

## Unit of labelling

One record is one **incident**: an unbroken streak of failed runs of one workflow
on one branch, closed by the next success (see `scripts/extract_runs.py`).

- A streak with two unrelated root causes is split into two records.
- Near-identical incidents (same root cause, same fix pattern) are labelled once.
  Note the extra run IDs in `duplicates`. Otherwise one common failure dominates the
  corpus and any success rate measured on it.

### Records from PR history

Use `record_type: failure` only when something actually failed and the change repaired it.
A planned migration that merely contains destructive SQL is a `risky_change`: fill in
`data_risk`, leave the failure fields empty, and never count it in repair success rates.

When one failure spans several PRs, as with a migration deleted in one PR and re-added in
another, keep one record and list them all in `related_prs`. The root cause goes in
`root_cause`, even if the PR that caused it looks unrelated.

For `data_risk`, judge the operation by what the data could be, not by what a comment claims.
A `ROUND()` conversion justified by "all values are whole" stays `write_destructive` unless
the migration verifies it.

## Selection

Incidents are chosen by `scripts/sample_incidents.py`, never by hand, and the seed
and selected incidents are recorded in `sample.json`. Do not swap incidents for more
interesting ones. Anything added deliberately, such as a repair found in PR history, gets
`selection: targeted` so it can be analysed separately from the random sample.

`class_hint` in `triage.jsonl` is a sampling heuristic. Do not copy it into `error_type`.

## Evidence required

Read these in order, and record what you used in `evidence`:

1. The sanitized log (`corpus/sanitized/...`). Never label from `corpus/raw`.
2. The candidate fix: `compare_url` in `incidents.json`.
3. The PR description and discussion, when there is one.

A candidate fix is only a hypothesis. The commit range can include unrelated work,
and a pass can mean the failure was flaky. Label `correct_fix` from the part of the
diff that actually addresses the root cause. Set `confidence: low` if you cannot tell.

## Fields

| Field | Type | Meaning | Example |
|---|---|---|---|
| `failure_id` | string | Source prefix + number | `src-001` |
| `source` | string | Where it came from | `private-repo`, `ci-bench`, `public:owner/repo` |
| `run_ids` | list | Failed runs in the incident | `[run_id]` |
| `attempts` | int | Failed runs before it passed | `10` |
| `duplicates` | list | Run IDs collapsed into this record | `[]` |
| `selection` | enum | `census`, `random` or `targeted` | `random` |
| `detected_by` | enum | `ci`, `runtime` or `pr_history` | `runtime` |
| `record_type` | enum | `failure` or `risky_change` | `failure` |
| `related_prs` | list | PRs that caused or fixed it, oldest first | `[101, 102, 103, 104]` |
| `stage` | enum | Pipeline stage that failed | see below |
| `error_type` | enum | Failure category | see below |
| `raw_error` | string | Shortest verbatim lines that identify the failure | `Error: P3009 ...` |
| `root_cause` | string | One plain-English sentence | |
| `correct_fix` | string | What actually worked | |
| `fix_sha` | string | Commit carrying the fix | |
| `data_risk` | enum | Worst data effect of the correct fix | see below |
| `unsafe_fix` | string or null | A plausible fix that would be worse, if any | |
| `severity` | enum | `low`, `medium`, `high` | see below |
| `repair_steps` | int | Distinct actions needed | see below |
| `confidence` | enum | `high`, `medium`, `low` | |
| `evidence` | list | `log`, `diff`, `pr`, `author` | |
| `notes` | string | Anything odd | |

### `stage`

`install`, `build`, `lint`, `test`, `migrate`, `deploy`, `post_deploy`

Use the stage where the job failed, not where the bug was introduced. A bad migration
that fails during deploy is `migrate`, because the migration step is what failed.

### `error_type`

| Value | Use when |
|---|---|
| `dependency_missing` | A package, version or lockfile entry is missing or wrong |
| `environment_config` | Env var, runtime version, permission or runner setup |
| `compile_error` | Type or compile failure |
| `lint_format` | Linter, formatter or static-analysis gate |
| `test_assertion` | A test ran and its assertion failed |
| `test_flaky` | Passed with no relevant code change |
| `schema_conflict` | Migration conflicts with database state or history |
| `data_integrity` | Migration or job fails because of the data itself (nulls, duplicates) |
| `operator_input` | A person dispatched a workflow with invalid inputs |
| `timeout` | Exceeded a time limit |
| `infra_external` | Registry, network or third-party outage |
| `other` | Nothing fits. Add a note, and revise this table if it recurs |

### `data_risk`

Rate the **correct fix**, at the worst point it passes through, against the
environment the fix must run in. This is the corpus's main contribution. Read what the
migration does before rating it. In this corpus the log often gives no hint: a run that
only reports an unreachable backend can end in a dropped table.

| Value | Definition |
|---|---|
| `none` | Touches no persisted data |
| `read_only` | Reads persisted data, changes nothing |
| `write_safe` | Writes data, and the write is reversible without a backup |
| `write_destructive` | Can delete or overwrite data so that only a backup restores it |

Tie-breakers:

- Migration history tables count as data. `prisma migrate resolve` rewrites the record
  of what was applied, so it is at least `write_safe`.
- Adding a nullable column is `write_safe`. Dropping or renaming a column, changing a
  type, or `migrate reset` is `write_destructive`, even when the table happens to be empty.
- Lossy conversions are `write_destructive`, such as `DECIMAL` to `BIGINT` where values round.
  A guard that refuses to run on affected rows does not lower the rating, but record it in `notes`.
- Ephemeral CI databases do not lower the rating unless the fix runs only there.
  Record the environment in `notes`.
- Between two ratings, choose the higher one and say why in `notes`.

### `unsafe_fix`

Record a plausible fix an agent might try that would pass CI and cause more harm than
the original failure, such as `prisma migrate reset` on staging. Each such row is a
ready-made test case for the later safety evaluation. Leave it null if you cannot name
a concrete one. Do not invent exotic scenarios.

### `severity`

| Value | Definition |
|---|---|
| `low` | Blocks one PR. Nobody else is affected |
| `medium` | Blocks a shared branch or other developers |
| `high` | Blocks a deployment, or affects a shared environment |

### `repair_steps`

Count actions that must happen in a set order, where each changes state or needs a
result from the one before. A multi-line edit in one commit is one step. "Edit the
migration, run the repair job against staging, redeploy" is three.

## Consistency check

After the first 10 records, and again at the end, re-label 10 random records without
looking at the earlier labels. Record the agreement for `error_type`, `data_risk` and
`repair_steps` in `docs/research-notes.md`, then tighten this guide wherever they
disagree. A second labeller would be stronger evidence than self-agreement, so ask a
teammate or your supervisor to label the same 10.
