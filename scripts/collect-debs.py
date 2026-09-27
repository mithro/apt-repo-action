#!/usr/bin/env python3
"""Collect a run's .deb artifacts into apt-repo/<suite>/ (collect-debs/).

Build jobs upload one artifact per (suite, arch), named `debs-<suite>-<arch>`
(or just `debs-<suite>`, or with further `-<suffix>`es: README.md). Each
artifact's suite is read from its name, so every artifact has to arrive in a
directory of its own name.

actions/download-artifact doesn't guarantee that: from v5 on, when exactly one
artifact matches, it is extracted straight into `path` with no `<name>/`
directory (actions/download-artifact#455). So the artifacts are listed here
first, and the download is told which ones to fetch, by ID, and where:

    collect-debs.py plan    --pattern 'debs-*' --staging staging
        GITHUB_OUTPUT gets count, ids (comma-separated) and path: `staging`
        for several artifacts, `staging/<name>` for exactly one, so both land
        in staging/<name>/.
    collect-debs.py regroup --staging staging --suites "trixie raspbian-trixie" --dest apt-repo
        copies each staging/<name>/**/*.deb into apt-repo/<suite>/.

`plan` reads the current run's artifacts from the GitHub API
($GITHUB_API_URL, $GITHUB_REPOSITORY, $GITHUB_RUN_ID) with $GH_TOKEN or
$GITHUB_TOKEN. The pattern is a shell-style glob (fnmatch), matched against
the whole name.

Standard library only: it runs on the runner.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

PREFIX = "debs-"


class Error(Exception):
    pass


def select(artifacts: list[dict], pattern: str) -> list[dict]:
    """The artifacts to download: matching `pattern`, not expired, and only
    the newest of each name. A re-run of some of a run's jobs uploads new
    artifacts under names the first attempt already used; the newest is the
    one actions/download-artifact would take (its `latest: true`)."""
    newest: dict[str, dict] = {}
    for a in artifacts:
        if a.get("expired") or not fnmatch.fnmatchcase(a["name"], pattern):
            continue
        if a["name"] not in newest or a["id"] > newest[a["name"]]["id"]:
            newest[a["name"]] = a
    return [newest[n] for n in sorted(newest)]


def suite_of(name: str, suites: list[str]) -> str | None:
    """The suite an artifact name is for: the longest suite in `suites` that
    the name, less `debs-`, is or starts with followed by a dash. A suite may
    itself contain dashes (raspbian-trixie), and callers may add their own
    suffixes (bookworm-armhf-openocd-stable) or none (debs-trixie), so
    neither the first nor the last dash is the boundary."""
    if not name.startswith(PREFIX):
        return None
    rest = name[len(PREFIX):]
    found = None
    for s in suites:
        if (rest == s or rest.startswith(s + "-")) and (found is None or len(s) > len(found)):
            found = s
    return found


def regroup(staging: Path, suites: list[str], dest: Path) -> dict[str, list[str]]:
    """Copy each staging/<name>/**/*.deb into dest/<suite>/. Returns what
    went where, by suite; an artifact naming no suite is skipped, with a
    warning."""
    placed: dict[str, list[str]] = {s: [] for s in suites}
    for s in suites:
        (dest / s).mkdir(parents=True, exist_ok=True)
    for d in sorted(p for p in staging.iterdir() if p.is_dir()) if staging.is_dir() else []:
        suite = suite_of(d.name, suites)
        if suite is None:
            print(f"::warning::artifact {d.name} names no suite in '{' '.join(suites)}'; skipping")
            continue
        for deb in sorted(d.rglob("*.deb")):
            shutil.copy2(deb, dest / suite / deb.name)
            placed[suite].append(deb.name)
            print(f"{deb} -> {dest / suite}/")
    stray = sorted(p.name for p in staging.glob("*.deb")) if staging.is_dir() else []
    if stray:
        # Only a download that ignored `plan`'s path puts files here.
        raise Error(f"{staging}/ holds .debs outside any artifact directory: {' '.join(stray)}")
    return placed


def run_artifacts() -> list[dict]:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    repo = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    found: list[dict] = []
    page = 1
    while True:
        url = f"{api}/repos/{repo}/actions/runs/{run}/artifacts?per_page=100&page={page}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                body = json.load(r)
        except urllib.error.HTTPError as e:
            raise Error(f"can't list this run's artifacts ({url}: HTTP {e.code} {e.reason}). "
                        "A private repository's publish job may need `actions: read`.") from None
        except (urllib.error.URLError, OSError) as e:
            raise Error(f"can't list this run's artifacts ({url}: {getattr(e, 'reason', e)})") from None
        batch = body.get("artifacts", [])
        found += batch
        if len(batch) < 100:
            return found
        page += 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--pattern", required=True)
    p.add_argument("--staging", required=True)
    r = sub.add_parser("regroup")
    r.add_argument("--staging", required=True, type=Path)
    r.add_argument("--suites", required=True)
    r.add_argument("--dest", required=True, type=Path)
    args = ap.parse_args()
    try:
        if args.cmd == "plan":
            chosen = select(run_artifacts(), args.pattern)
            for a in chosen:
                print(f"{a['name']} (ID {a['id']})")
            if not chosen:
                print(f"::warning::no artifact of this run matches '{args.pattern}'")
            path = args.staging
            if len(chosen) == 1:
                path = f"{args.staging}/{chosen[0]['name']}"
            out = {"count": str(len(chosen)), "ids": ",".join(str(a["id"]) for a in chosen), "path": path}
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                for k, v in out.items():
                    f.write(f"{k}={v}\n")
        else:
            regroup(args.staging, args.suites.split(), args.dest)
    except Error as e:
        print(f"::error::{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
