# The Repair Trap

Research on the reliability and safety of automated CI/CD repair agents. Phase 1 builds
a labelled corpus of real pipeline failures, recording for each one whether the correct
fix touches data.

## Layout

```
corpus/raw/              unprocessed logs, gitignored, treat as sensitive
corpus/sanitized/        secrets stripped; gitignored, kept local
corpus/labelled/         the dataset
corpus/labeling-guide.md the labelling method
scripts/extract_runs.py  pull failed runs, logs and candidate fixes
scripts/sanitize.py      strip secrets and verify the result
scripts/triage.py        provisional failure classes, for stratifying only
scripts/sample_incidents.py  reproducible stratified sample for labelling
docs/research-notes.md   running research log, gitignored, kept local
```

## Workflow

Requires Python 3.10+ and an authenticated `gh` CLI.

```bash
python scripts/extract_runs.py owner/repo --workflows "CI,Deploy"
python scripts/sanitize.py corpus/raw corpus/sanitized
python scripts/triage.py corpus/sanitized/owner__repo
python scripts/sample_incidents.py corpus/sanitized/owner__repo --size 60
python -m unittest discover tests
```

`sanitize.py` finishes by re-scanning its output and exits non-zero if anything remains.
Add private terms that no pattern can catch to `.sanitize-denylist`, one per line.
Automated sanitizing is not proof of safety: review a sample by hand before sharing.
It removes secrets and contact details, but repo and branch names stay unless denylisted.

Re-run extraction at least every 30 days, because GitHub deletes run logs after 90 days.
