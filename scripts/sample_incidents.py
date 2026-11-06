#!/usr/bin/env python3
"""Draw a reproducible stratified sample of incidents for hand labelling.

    python scripts/sample_incidents.py corpus/sanitized/owner__repo --size 60

Samples incidents rather than raw runs, because repeated pushes to one branch
would otherwise count the same failure many times. Writes sample.json with the
seed, the method and every selected incident, so the draw can be reproduced.

Method:
  1. Exclude incidents with no log, since they cannot be labelled.
  2. Take a census of the rare, research-relevant incidents: every fix that touches
     schema files, and every failure class with fewer than CENSUS_BELOW incidents.
  3. Fill the remaining places by proportional random allocation across strata of
     (class_hint, attempts >= 3), using the largest-remainder method.

`attempts >= 3` stands in for multi-step repair, and `class_hint` and
`fix_touches_schema` for failure type and data risk. The real values are only known
after labelling, and stratifying on them beforehand would be circular.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

CENSUS_BELOW = 4
HARD_REPAIR_ATTEMPTS = 3


def stratum(record: dict) -> str:
    hard = "hard" if record["attempts"] >= HARD_REPAIR_ATTEMPTS else "easy"
    return f"{record['class_hint']}/{hard}"


def allocate(strata: dict[str, list[dict]], places: int) -> dict[str, int]:
    """Largest-remainder allocation, capped at each stratum's size."""
    population = sum(len(group) for group in strata.values())
    quotas = {name: places * len(group) / population for name, group in strata.items()}
    counts = {name: int(quota) for name, quota in quotas.items()}
    by_remainder = sorted(strata, key=lambda n: (quotas[n] - counts[n], n), reverse=True)
    for name in by_remainder[:places - sum(counts.values())]:
        counts[name] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--size", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    records = [json.loads(line) for line in
               (args.root / "triage.jsonl").read_text(encoding="utf-8").splitlines()]
    eligible = [r for r in records if r["runs_with_logs"] > 0]
    size = min(args.size, len(eligible))

    class_sizes = Counter(r["class_hint"] for r in eligible)
    census = [r for r in eligible
              if r["fix_touches_schema"] or class_sizes[r["class_hint"]] < CENSUS_BELOW]
    census_ids = {r["incident"] for r in census}

    strata: dict[str, list[dict]] = defaultdict(list)
    for record in eligible:
        if record["incident"] not in census_ids:
            strata[stratum(record)].append(record)

    rng = random.Random(args.seed)
    drawn: list[dict] = []
    for name, count in sorted(allocate(strata, size - len(census)).items()):
        drawn += rng.sample(sorted(strata[name], key=lambda r: r["incident"]), count)

    selected = sorted(census + drawn, key=lambda r: r["incident"])
    (args.root / "sample.json").write_text(json.dumps({
        "seed": args.seed,
        "method": "census of schema-touching and rare classes, then proportional random "
                  "allocation over (class_hint, attempts>=3)",
        "population": len(records),
        "eligible": len(eligible),
        "excluded_no_log": len(records) - len(eligible),
        "census": len(census),
        "sampled": len(drawn),
        "incidents": [{key: r[key] for key in ("incident", "class_hint", "attempts", "workflow",
                                               "fix_touches_schema", "compare_url")}
                      | {"selection": "census" if r["incident"] in census_ids else "random"}
                      for r in selected],
    }, indent=1), encoding="utf-8")

    print(f"selected {len(selected)} of {len(eligible)} eligible "
          f"({len(census)} census, {len(drawn)} random), seed {args.seed}")
    for name, count in Counter(r["class_hint"] for r in selected).most_common():
        print(f"  {name}: {count} of {class_sizes[name]}")


if __name__ == "__main__":
    main()
