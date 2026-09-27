#!/usr/bin/env python3
"""Turn lintian's output into GitHub annotations and a step summary
(docs/packaging.md, "Package contents": lintian runs on every build).

    lintian-report.py --mode warn|error [--status N] [--summary F]
                      [--outputs F] LINTIAN_OUTPUT

--mode warn:  every E: and W: tag is a warning annotation; never fails.
--mode error: E: tags are error annotations, and the exit status is 1 when
              there is one.
--status is lintian's own exit status (run with --fail-on none, so anything
but 0 means lintian itself failed). --summary and --outputs default to
$GITHUB_STEP_SUMMARY and $GITHUB_OUTPUT; the outputs are `errors` and
`warnings`, the number of E: and W: tags.
"""
import argparse
import collections
import os
import re
import sys
from dataclasses import dataclass

# "E: <package>[ source]: <tag>[ <detail>]", lintian 2.116 (bookworm) on.
LINE = re.compile(r"^([EWIPXOC]): (\S+(?: source)?): (\S+)(?: (.*))?$")


@dataclass
class Tag:
    level: str
    package: str
    tag: str
    detail: str


def parse(text: str) -> list[Tag]:
    tags = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            tags.append(Tag(m[1], m[2], m[3], m[4] or ""))
    return tags


def _data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(s: str) -> str:
    return _data(s).replace(":", "%3A").replace(",", "%2C")


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["warn", "error"], required=True)
    ap.add_argument("--status", default="0")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    ap.add_argument("--outputs", default=os.environ.get("GITHUB_OUTPUT"))
    ap.add_argument("log")
    args = ap.parse_args(argv)

    with open(args.log, encoding="utf-8", errors="replace") as f:
        tags = [t for t in parse(f.read()) if t.level in "EW"]
    errors = sum(t.level == "E" for t in tags)
    warnings = len(tags) - errors
    failed = False

    for t in tags:
        kind = "error" if t.level == "E" and args.mode == "error" else "warning"
        failed |= kind == "error"
        msg = f"{t.package}: {t.tag}" + (f" {t.detail}" if t.detail else "")
        print(f"::{kind} title={_prop(f'lintian {t.level}: {t.tag}')}::{_data(msg)}", file=out)
    if args.status != "0":
        kind = "error" if args.mode == "error" else "warning"
        failed |= kind == "error"
        print(f"::{kind} title=lintian::lintian itself failed (exit status {args.status})", file=out)

    if args.summary:
        counts = collections.Counter((t.level, t.package, t.tag) for t in tags)
        lines = [f"### lintian: {errors} errors, {warnings} warnings", ""]
        if counts:
            lines += ["| | package | tag | count |", "|---|---|---|---|"]
            lines += [f"| {lv} | {pkg} | `{tag}` | {n} |" for (lv, pkg, tag), n in sorted(counts.items())]
        else:
            lines.append("lintian found no errors or warnings.")
        if args.mode == "warn" and errors:
            lines += ["", "lintian errors don't fail the build yet (`lintian: warn`); they should be fixed."]
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if args.outputs:
        with open(args.outputs, "a", encoding="utf-8") as f:
            f.write(f"errors={errors}\nwarnings={warnings}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
