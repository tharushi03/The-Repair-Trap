# Guide review

Gaps found in `corpus/labeling-guide.md` while labelling real records.

## Findings from case-001 and case-002

### 1. `attempts` is undefined for PR-history records

The guide defines `attempts` as failed runs before a pass. PR-history records may have no
pass, and most failed workflow runs are not attempts at the repair. Current workaround: count
observed failed repair attempts only.

Recommendation: redefine `attempts` for PR-history records as "observed failed repair
attempts", or add a separate field.

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
