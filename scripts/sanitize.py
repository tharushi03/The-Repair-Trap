#!/usr/bin/env python3
"""Strip secrets and private data from CI logs before they enter the repo.

    python scripts/sanitize.py corpus/raw corpus/sanitized   # sanitize a tree
    python scripts/sanitize.py --check corpus/sanitized      # verify; exit 1 on any hit

Sanitizing is idempotent, so --check simply asserts that a second pass changes
nothing. It reports rule names and counts only, never the matched values.

Private strings that no pattern can know about (customer names, internal hosts)
go in `.sanitize-denylist`, one literal per line, case-insensitive.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Each rule is (name, compiled pattern, replacement). Order matters: specific
# credential formats run first so they get the most informative label.
RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("private_key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        re.S), "[REDACTED:private_key]"),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
     "[REDACTED:github_token]"),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED:aws_key]"),
    ("slack_token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:slack_token]"),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[REDACTED:google_key]"),
    ("stripe_key", re.compile(r"\b(?:[sr]k_(?:live|test)_|whsec_)[0-9A-Za-z]{10,}"),
     "[REDACTED:stripe_key]"),
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED:sk_key]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
     "[REDACTED:jwt]"),
    ("auth_header", re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
     r"\1 [REDACTED:auth_header]"),
    ("url_credentials", re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/:@'\"]*:[^\s/@'\"]*@"),
     r"\1[REDACTED:url_credentials]@"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
     "[REDACTED:email]"),
]

# NAME=value / NAME: value, where NAME looks sensitive.
ASSIGNMENT = re.compile(
    r"(?i)(?P<name>[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|credential|database_url|dsn)[\w.-]*)"
    r"(?P<sep>['\"]?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|\S+)"
)
# Values that are plainly not secrets. Keeping them preserves error text such as
# TypeScript's "password: string".
HARMLESS_VALUE = re.compile(
    r"(?i)^(?:\*+|true|false|null|none|undefined|string|number|boolean|any|void|"
    r"\[REDACTED:[a-z_]+.*|\$\{\{.*|\$\{?\w+\}?|process\.env\..*|<[^>]*>)$"
)

IPV4 = re.compile(r"(?<![\w.@/-])(?:\d{1,3}\.){3}\d{1,3}(?![\w.-])")
LOCAL_IPS = {"127.0.0.1", "0.0.0.0", "255.255.255.255"}

BLOB = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{41,}={0,2}")
HEX_BLOB = re.compile(r"^[0-9a-fA-F]+$")
DIGEST_PREFIX = re.compile(r"sha(?:1|256|384|512)[:-]$")


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_like_blob(token: str) -> bool:
    """Tell an encoded blob from a long path or slug.

    A plain "base64 over 40 chars" rule also matches long file paths and branch
    names, which would destroy the error context the corpus exists to keep.
    """
    body = token.rstrip("=")
    if HEX_BLOB.match(body):
        return True
    # Paths and slugs split into short words; random data has long unbroken runs.
    if max(len(part) for part in re.split(r"[/_-]", body)) < 24:
        return False
    classes = sum(bool(re.search(p, body)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
    return classes >= 2 and shannon_entropy(body) >= 3.5


def load_denylist(path: Path) -> re.Pattern[str] | None:
    if not path.is_file():
        return None
    terms = [t.strip() for t in path.read_text(encoding="utf-8").splitlines()]
    terms = sorted((t for t in terms if t and not t.startswith("#")), key=len, reverse=True)
    if not terms:
        return None
    return re.compile("|".join(re.escape(t) for t in terms), re.I)


def sanitize(text: str, denylist: re.Pattern[str] | None = None,
             stats: Counter | None = None) -> str:
    stats = stats if stats is not None else Counter()
    text = ANSI.sub("", text)

    for name, pattern, replacement in RULES:
        text, hits = pattern.subn(replacement, text)
        stats[name] += hits

    def scrub_assignment(match: re.Match[str]) -> str:
        value = match["value"].strip("\"'").rstrip(",;)}]")
        if not value or HARMLESS_VALUE.match(value):
            return match[0]
        stats["assignment"] += 1
        return f"{match['name']}{match['sep']}[REDACTED:assignment]"
    text = ASSIGNMENT.sub(scrub_assignment, text)

    def scrub_ip(match: re.Match[str]) -> str:
        if match[0] in LOCAL_IPS or any(int(o) > 255 for o in match[0].split(".")):
            return match[0]
        stats["ip"] += 1
        return "[REDACTED:ip]"
    text = IPV4.sub(scrub_ip, text)

    def scrub_blob(match: re.Match[str]) -> str:
        before = match.string[max(0, match.start() - 8):match.start()]
        if DIGEST_PREFIX.search(before) or not looks_like_blob(match[0]):
            return match[0]
        stats["blob"] += 1
        return "[REDACTED:blob]"
    text = BLOB.sub(scrub_blob, text)

    if denylist is not None:
        text, hits = denylist.subn("[REDACTED:denylist]", text)
        stats["denylist"] += hits
    return text


def iter_files(root: Path):
    return (p for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in {".log", ".json", ".jsonl", ".txt"})


def check(root: Path, denylist) -> int:
    dirty = 0
    for path in iter_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        stats: Counter = Counter()
        if sanitize(text, denylist, stats) != text:
            dirty += 1
            hits = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
            print(f"RESIDUAL {path}: {hits or 'ansi'}")
    print(f"{'FAIL' if dirty else 'OK'}: {dirty} file(s) with residual matches")
    return 1 if dirty else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("dest", type=Path, nargs="?")
    parser.add_argument("--check", action="store_true", help="verify SOURCE is already clean")
    parser.add_argument("--denylist", type=Path, default=Path(".sanitize-denylist"))
    args = parser.parse_args()
    denylist = load_denylist(args.denylist)

    if args.check:
        return check(args.source, denylist)
    if args.dest is None:
        parser.error("dest is required unless --check is given")

    totals: Counter = Counter()
    files = 0
    for path in iter_files(args.source):
        target = args.dest / path.relative_to(args.source)
        target.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8", errors="replace")
        target.write_text(sanitize(text, denylist, totals), encoding="utf-8", newline="\n")
        files += 1

    print(f"sanitized {files} file(s) into {args.dest}")
    for name, count in totals.most_common():
        if count:
            print(f"  {name}: {count}")
    return check(args.dest, denylist)


if __name__ == "__main__":
    sys.exit(main())
