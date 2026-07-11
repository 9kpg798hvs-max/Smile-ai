"""Best-effort de-identification for preparing the real corpus.

Scrubs phone numbers, emails, dates, and any names you supply (day sheets
give you the exact patient/doctor names per thread, so pass those in).

LIMITS — this is regex, not magic: it will not catch names it wasn't given,
addresses, or free-text identifiers. Treat the output as still-sensitive
until reviewed. The safest path remains a BAA with the API vendor; this
scrubber reduces exposure, it does not eliminate it. Scrubbed or not, corpus
files live under data/ which is gitignored and must never be committed.

Usage:
    python -m triage.deidentify input.jsonl output.jsonl --names "Jane Doe" "Belani"
"""

import argparse
import json
import re
import sys

PHONE_RE = re.compile(
    r"(\+?1[\s.-]?)?(\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b"
)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?|"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(st|nd|rd|th)?)\b",
    re.IGNORECASE,
)


def scrub(text: str, names: list[str] | None = None) -> str:
    text = PHONE_RE.sub("[PHONE]", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    text = DATE_RE.sub("[DATE]", text)
    for name in sorted(names or [], key=len, reverse=True):
        if name.strip():
            text = re.sub(re.escape(name), "[NAME]", text, flags=re.IGNORECASE)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSONL file with a 'text' field per line")
    parser.add_argument("output", help="Scrubbed JSONL output path")
    parser.add_argument("--names", nargs="*", default=[], help="Names to redact")
    args = parser.parse_args()

    n = 0
    with open(args.input) as fin, open(args.output, "w") as fout:
        for line in fin:
            if not line.strip():
                continue
            row = json.loads(line)
            row["text"] = scrub(row["text"], args.names)
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    print(f"scrubbed {n} rows -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
