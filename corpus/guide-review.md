# Guide review

Gaps found in `corpus/labeling-guide.md` while labelling real records.

## Findings from case-001 and case-002

### 1. `attempts` was undefined for PR-history records

Resolved. `attempts` is now the number of failed runs whose logs show the record's fault, which
equals the length of `run_ids`. It is verifiable from logs, works for records that never passed,
and gives each fault in a shared streak its own count.

### 2. `record_type: failure` requires an observed repair

The guide's definition is "something actually failed and the change repaired it". PR-history
records often have an observed failure but an unconfirmed repair. Current workaround: record
the gap in `confidence` and `notes`.

Recommendation: add `verified: true|false` or `repair_status: observed|unobserved`. Without
it, unverified failures cannot be labelled honestly.

### 3. No field for cross-record dependencies

case-001's repair cannot proceed until case-002 is fixed, because the migration repair cannot
connect to Staging until the URL is corrected. The guide has no field for this.

Recommendation: add `depends_on: [failure_id]` for linked records.

## Findings from case-003 and case-004

- How should a fix commit with two changes that can't be separated be labelled? Current handling: both go in, `confidence: low`.
- Should "enables unattended destructive capability" (a Dockerfile `CMD`) count in `data_risk`, or just be noted? Current handling: rated on the migrations the fix actually applies, and noted.
- Is "no service log available" a valid reason for `confidence: low`, or does the guide need a separate evidence-quality field?

## Findings from case-005

- Fixes with a prior failed repair attempt: does the failed attempt go in `repair_steps`, `attempts` or `notes`? Current handling: count the working fix in `repair_steps` and note the failed attempt separately.
