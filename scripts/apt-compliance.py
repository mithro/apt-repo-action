#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Find every apt packaging repository of some GitHub owners and check it
against docs/packaging.md and docs/conventions.md.

    uv run scripts/apt-compliance.py --owner mithro=welland \\
        --owner fpgas-online=fpgasonline \\
        --maintainer "Tim 'mithro' Ansell <me@mith.ro>" \\
        --html report.html --markdown report.md --json report.json

Nothing here is specific to one owner: the owners, their version tags and
the expected maintainer come from the command line, and everything about a
single repository comes from GitHub, from its live apt site, or from the
repository's own `.github/apt-packaging.toml` (see docs/packaging.md). What
is hard-coded is what the conventions mean: the rules, the default suites
and architectures, and the version forms.

Discovery. A repository is an apt packaging repository when a workflow on
its default branch, or on the branch its GitHub Pages site last deployed
from, calls this action repository (`uses: <action-repo>/...`) or indexes
an apt repository itself (dpkg-scanpackages, apt-ftparchive, reprepro).
Comments don't count. A repository that stops doing that stops being found.
A Pages site that still serves `<repo>.gpg` without such a workflow is
reported separately, as a site without packaging.

Needs: `gh` (authenticated), network access to the sites. Uses the GitHub
API only for reading.
"""
from __future__ import annotations

import argparse
import base64
import functools
import html
import json
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

# --------------------------------------------------------------------------
# What the conventions mean. Everything owner-specific is an argument.

# Debian codename -> release number, for the `~deb<R>` suffix.
DEBIAN_RELEASE = {"bookworm": 12, "trixie": 13, "forky": 14}
# The default suites: stable, testing, unstable. bookworm is opt-in.
DEFAULT_DEBIAN = ["trixie", "forky", "sid"]
# Every suite the checker probes for on a site, in display order.
KNOWN_SUITES = ["bookworm", "trixie", "forky", "sid",
                "raspbian-bookworm", "raspbian-trixie", "raspbian-forky"]
DEFAULT_ARCH = ["amd64", "i386", "arm64", "armhf", "riscv64"]
NO_RISCV64 = {"bookworm"}  # Debian suites without an official riscv64
DEFAULT_BRANCH = {"A": "packaging", "B": "main", "aggregate": "main"}
JOBS = {"test", "build-deb", "publish-apt", "release"}
WORKFLOW_FILE, WORKFLOW_NAME = "deb.yml", "Debian packages"
CONCURRENCY_GROUP = "deb-${{ github.ref }}"
CONCURRENCY_CANCEL = "${{ github.event_name == 'pull_request' }}"
DBGSYM_LIMIT = 10_000_000
DECLARATION = ".github/apt-packaging.toml"
HANDROLLED = re.compile(r"\b(dpkg-scanpackages|apt-ftparchive|reprepro)\b")
DATE = re.compile(r"(?<!\d)20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
FORBIDDEN_ROOT = ["dists/", "pool/", "Release", "InRelease", "Packages",
                  "pubkey.gpg", "KEY.gpg", "key.gpg", "public.key"]
SUITE_FILES = ["InRelease", "Release", "Release.gpg", "Packages", "Packages.gz"]
INDEX_FORBIDDEN = ["--dearmor", "lsb_release", "trusted=yes", "/usr/share/keyrings"]
ARMOUR = b"-----BEGIN PGP PUBLIC KEY BLOCK-----"

# (id, group, what compliance means, what to do when it fails)
RULES = [
    ("PKG-DECLARED", "Repository", f"the repository declares its kind in {DECLARATION}",
     f"Add {DECLARATION}: kind, and a reason for every exception."),
    ("PKG-BRANCH", "Repository", "default branch is `packaging` (Set A) or `main` (Set B), and it is what publishes",
     "Make the conventional branch the default branch and publish from it."),
    ("PKG-HISTORY", "Repository", "Set A carries upstream's history (a GitHub fork, or history imported)",
     "Re-create the repository as a fork of upstream, or import upstream's full history."),
    ("PKG-UPSTREAM", "Repository", "Set A has an `upstream` branch mirroring upstream",
     "Create an `upstream` branch at the upstream commit the packaging is based on."),
    ("PKG-SYNC", "Repository", "Set A has `sync-upstream.yml` (a backport: a scheduled rebuild)",
     "Add .github/workflows/sync-upstream.yml."),
    ("PKG-README", "Repository", "Set A has `packaging/README.md`",
     "Write packaging/README.md: upstream, what we change, how to update."),
    ("PKG-DEBIAN", "Repository", "`debian/` at the root of the default branch (a patch series: `packaging/debian/<name>/`)",
     "Move the packaging to debian/ at the root of the default branch."),
    ("PKG-CHANGELOG", "Repository", "Set B commits no `debian/changelog`, and `.gitignore` lists it",
     "Delete the committed debian/changelog and add `debian/changelog` to .gitignore (the build writes it)."),
    ("PKG-DEPENDS", "Repository", f"each `[[depends]]` in {DECLARATION} is well-formed, with a reason and known suites",
     f"Fix the [[depends]] entries in {DECLARATION} (docs/packaging.md, \"The declaration\")."),
    ("PKG-WORKFLOW", "Workflow", f"`.github/workflows/{WORKFLOW_FILE}` named `{WORKFLOW_NAME}`",
     f"Rename the build workflow to .github/workflows/{WORKFLOW_FILE} with `name: {WORKFLOW_NAME}`."),
    ("PKG-JOBS", "Workflow", "jobs are only `test`, `build-deb`, `publish-apt`, `release`",
     "Rename the jobs to test / build-deb / publish-apt / release."),
    ("PKG-TRIGGERS", "Workflow", "push to the default branch, pull_request, workflow_dispatch; nothing else; any `workflow_run` workflow ignores pull requests",
     "Set the triggers to push (default branch), pull_request and workflow_dispatch; guard every workflow_run job with `github.event.workflow_run.event != 'pull_request'`."),
    ("PKG-PREVIEW", "Workflow", "pull requests build, and `publish-apt` never runs for them",
     "Build on pull_request and guard publish-apt with the default-branch `if:`."),
    ("PKG-CONCURRENCY", "Workflow", "concurrency group `deb-${{ github.ref }}`, cancelling only pull requests",
     "Add the conventional concurrency block."),
    ("PKG-PUBLISHER", "Workflow", "publishes only through `publish-apt.yml@main`",
     "Publish through <action-repo>/.github/workflows/publish-apt.yml@main."),
    ("PKG-SHARED", "Workflow", "builds with the shared build at `@main`; no local `deb-version.py`",
     "Build with <action-repo>/build-deb@main and drop packaging/deb-version.py."),
    ("PKG-INSTALL-TEST", "Workflow", "an `Install test` step installs the packages in a clean container",
     "Add an `Install test` step."),
    ("PKG-SUITES", "Packages", "the default suites, or the declared ones with a reason",
     "Publish the default suites, or declare the difference with a reason."),
    ("PKG-ARCH", "Packages", "the default architectures, `all`, or the declared ones with a reason",
     "Build the default architectures (or `all`), or declare the difference with a reason."),
    ("PKG-NODATES", "Packages", "no date in any version", "Version from git describe counts, not dates."),
    ("PKG-VERSION", "Packages", "versions have their kind's form", "Switch to the shared version script."),
    ("PKG-SUITE-SUFFIX", "Packages", "`~deb<R>` on every suite but sid", "Add the ~deb<R> suite suffix."),
    ("PKG-DBGSYM", "Packages", "no `-dbgsym` over 10 MB in the apt repository",
     "Keep debug symbols over 10 MB out of apt (artifact and GitHub Release only)."),
    ("PKG-MAINTAINER", "Metadata", "`Maintainer:` is the expected maintainer", "Set Maintainer: in debian/control."),
    ("PKG-DOCS", "Metadata", "README has `## Install` with the setup block for one suite, every published suite named, the key's fingerprint, and each dependency repository's setup",
     "Give README.md (packaging/README.md for Set A) an `## Install` section with docs/conventions.md's setup block for one suite, a sentence naming every published suite to put in its place, the key's fingerprint, and each dependency repository's setup."),
    ("REPO-PAGES", "Published repository", "Pages built by GitHub Actions, HTTPS enforced",
     "Build Pages from GitHub Actions with HTTPS enforced."),
    ("REPO-KEYS", "Published repository", "`<repo>.gpg` (binary) and `<repo>.asc` (armoured) at the site root",
     "Publish <repo>.gpg and <repo>.asc (publish-apt.yml does)."),
    ("REPO-LAYOUT", "Published repository", "one flat signed repository per suite, nothing at the root",
     "Publish with publish-apt.yml; drop root and dists/pool layouts."),
    ("REPO-RELEASE", "Published repository", "Release has Origin = Label = `<repo>`, `Suite: stable`, `Codename: <suite>`",
     "Publish with publish-apt.yml."),
    ("REPO-INDEX", "Published repository", "the index page shows the one setup, for every suite",
     "Let publish-apt.yml generate index.html."),
]
RULE = {r[0]: r for r in RULES}


def _load_apt_sources():
    """scripts/apt-sources.py, whose validation of [[depends]] build-deb uses."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("apt_sources", str(Path(__file__).with_name("apt-sources.py")))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("apt_sources", loader))
    loader.exec_module(module)
    return module


apt_sources = _load_apt_sources()


# --------------------------------------------------------------------------
# Debian version comparison (dpkg's algorithm).

def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return -1 if ac < bc else 1
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        first = 0
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            if not first:
                first = ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if first:
            return -1 if first < 0 else 1
    return 0


def _split(v: str):
    epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
    up, _, rev = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return int(epoch or 0), up, rev


def version_key(v: str):
    """A sort key that orders Debian versions as dpkg does."""
    def cmp(x, y):
        ex, ux, rx = _split(x)
        ey, uy, ry = _split(y)
        if ex != ey:
            return -1 if ex < ey else 1
        return _verrevcmp(ux, uy) or _verrevcmp(rx, ry)
    return functools.cmp_to_key(cmp)(v)


# --------------------------------------------------------------------------
# GitHub and HTTP.

def gh(*args: str, check: bool = True) -> str | None:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        if not check or "404" in r.stderr or "Not Found" in r.stderr:
            return None
        raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def api(path: str):
    out = gh("api", path)
    return json.loads(out) if out is not None else None


def api_pages(path: str) -> list:
    out = gh("api", "--paginate", "--slurp", path)
    return [x for page in json.loads(out) for x in page] if out else []


def graphql(query: str) -> dict:
    return json.loads(gh("api", "graphql", "-f", f"query={query}"))["data"]


@functools.cache
def our_people(owner: str) -> frozenset[str]:
    """The owner, or an organisation's members: whoever writes our own commits."""
    if api(f"users/{owner}")["type"] != "Organization":
        return frozenset({owner.lower()})
    return frozenset(m["login"].lower() for m in api_pages(f"orgs/{owner}/members?per_page=100"))


def file_at(repo: str, oid: str, path: str) -> str | None:
    j = api(f"repos/{repo}/contents/{path}?ref={oid}")
    if not isinstance(j, dict) or j.get("type") != "file":
        return None
    return base64.b64decode(j["content"]).decode(errors="replace")


def http(url: str, method: str = "GET") -> tuple[int, bytes]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": "apt-compliance",
                                                              "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, (r.read() if method == "GET" else b"")
    except urllib.error.HTTPError as e:
        return e.code, b""
    except urllib.error.URLError as e:
        print(f"warning: {url}: {e.reason}", file=sys.stderr)
        return 0, b""


# --------------------------------------------------------------------------
# Discovery.

def code_lines(text: str) -> str:
    return "\n".join(l for l in (text or "").splitlines() if not l.lstrip().startswith("#"))


def is_packaging(workflows: dict[str, str], action_repo: str) -> bool:
    uses = re.compile(rf"uses:\s*['\"]?{re.escape(action_repo)}/", re.I)
    return any(uses.search(code_lines(t)) or HANDROLLED.search(code_lines(t)) for t in workflows.values())


def list_repos(owner: str) -> list[dict]:
    kind = "orgs" if api(f"users/{owner}")["type"] == "Organization" else "users"
    return api_pages(f"{kind}/{owner}/repos?per_page=100&type=owner")


WF_TREE = "{... on Tree {entries {name object {... on Blob {text}}}}}"


def workflows_batch(items: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """(full name, ref expression) -> default branch, last Pages deploy ref, and workflow texts."""
    out = {}
    for start in range(0, len(items), 20):
        chunk = items[start:start + 20]
        parts = []
        for n, (full, ref) in enumerate(chunk):
            owner, name = full.split("/")
            parts.append(
                f'r{n}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{'
                ' defaultBranchRef { name target { oid } }'
                ' deployments(environments: ["github-pages"], first: 1, orderBy: {field: CREATED_AT, direction: DESC})'
                ' { nodes { ref { name target { oid } } } }'
                f' wf: object(expression: {json.dumps(ref + ":.github/workflows")}) {WF_TREE} }}')
        data = graphql("query {" + " ".join(parts) + "}")
        for n, key in enumerate(chunk):
            r = data.get(f"r{n}") or {}
            dep = ((r.get("deployments") or {}).get("nodes") or [None])[0]
            wf = {e["name"]: (e.get("object") or {}).get("text") or ""
                  for e in ((r.get("wf") or {}).get("entries") or [])
                  if e["name"].endswith((".yml", ".yaml"))}
            out[key] = {"default": r.get("defaultBranchRef"), "deploy": (dep or {}).get("ref"), "workflows": wf}
    return out


def discover(owners: list[str], action_repo: str) -> tuple[list[dict], list[dict]]:
    repos = [r for o in owners for r in list_repos(o)]
    print(f"discover: {len(repos)} repositories", file=sys.stderr)
    return classify(repos, action_repo)


def named(names: list[str]) -> list[dict]:
    """The repositories `--repo` names, without listing their owners' others."""
    repos = []
    for n in names:
        r = api(f"repos/{n}")
        if r is None:
            raise SystemExit(f"error: no repository {n}")
        repos.append(r)
    return repos


def classify(repos: list[dict], action_repo: str) -> tuple[list[dict], list[dict]]:
    """The packaging repositories among `repos`, and the sites without packaging."""
    first = workflows_batch([(r["full_name"], "HEAD") for r in repos])
    found, second = {}, []
    for r in repos:
        f = first[(r["full_name"], "HEAD")]
        if not f["default"]:
            continue  # empty repository
        d = {"meta": r, "default": f["default"]["name"], "default_oid": f["default"]["target"]["oid"],
             "deploy": (f["deploy"] or {}).get("name"), "workflows_default": f["workflows"]}
        d["deploy_oid"] = ((f["deploy"] or {}).get("target") or {}).get("oid")
        found[r["full_name"]] = d
        if d["deploy"] and d["deploy"] != d["default"]:
            second.append((r["full_name"], d["deploy_oid"]))
    more = workflows_batch(second)
    packaging, orphans = [], []
    for full, d in found.items():
        if full.lower() == action_repo.lower():
            continue  # the publisher itself
        d["build_ref"], d["build_oid"], d["workflows"] = d["default"], d["default_oid"], d["workflows_default"]
        other = more.get((full, d["deploy_oid"]))
        if other and is_packaging(other["workflows"], action_repo):
            d["build_ref"], d["build_oid"], d["workflows"] = d["deploy"], d["deploy_oid"], other["workflows"]
        if is_packaging(d["workflows"], action_repo):
            packaging.append(d)
        elif d["meta"].get("has_pages"):
            orphans.append(d)
    # A Pages site with an apt key but no packaging workflow.
    def orphan(d):
        pages = api(f"repos/{d['meta']['full_name']}/pages") or {}
        site = (pages.get("html_url") or "").rstrip("/")
        name = d["meta"]["name"]
        if site and http(f"{site}/{name}.gpg", "HEAD")[0] == 200:
            return {"repo": d["meta"]["full_name"], "site": site,
                    "suites": [s for s in KNOWN_SUITES if http(f"{site}/{s}/InRelease", "HEAD")[0] == 200]}
    with ThreadPoolExecutor(8) as ex:
        sites = [s for s in ex.map(orphan, orphans) if s]
    return sorted(packaging, key=lambda d: d["meta"]["full_name"].lower()), sites


def local_files(path: Path) -> list[str]:
    """The files of a checkout that a commit of everything would hold:
    tracked ones and untracked ones git doesn't ignore."""
    r = subprocess.run(["git", "-C", str(path), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True, text=True, check=True)
    return sorted({p for p in r.stdout.split("\0") if p and (path / p).is_file()})


def local_entry(name: str, path: Path, action_repo: str) -> dict:
    """`name` as GitHub has it (default branch, what publishes, the live
    site), but with the workflows of the checkout at `path`."""
    meta = named([name])[0]
    found = classify([meta], action_repo)[0]
    if found:
        d = found[0]
    else:  # not publishing yet: a new repository's first pull request
        r = workflows_batch([(name, "HEAD")])[(name, "HEAD")]
        if not r["default"]:
            raise SystemExit(f"error: {name} is empty")
        d = {"meta": meta, "default": r["default"]["name"], "default_oid": r["default"]["target"]["oid"],
             "deploy": None, "deploy_oid": None}
        d["build_ref"], d["build_oid"] = d["default"], d["default_oid"]
    files = local_files(path)
    d["local"] = path
    d["workflows"] = {p.rpartition("/")[2]: (path / p).read_text(errors="replace") for p in files
                      if re.fullmatch(r"\.github/workflows/[^/]+\.ya?ml", p)}
    if not is_packaging(d["workflows"], action_repo):
        print(f"warning: {path}'s workflows don't publish an apt repository", file=sys.stderr)
    return d


# --------------------------------------------------------------------------
# Facts about one packaging repository.

def parse_stanzas(text: str) -> list[dict]:
    return [dict(re.findall(r"^([A-Za-z0-9-]+):[ \t]*(.*)$", s, re.M)) for s in text.split("\n\n") if s.strip()]


def site_facts(site: str, name: str) -> dict:
    s = {"site": site, "root": {}, "suites": {}}
    code, index = http(site + "/")
    s["index"] = index.decode(errors="replace") if code == 200 else ""
    for f in (f"{name}.gpg", f"{name}.asc"):
        s["root"][f] = http(f"{site}/{f}")
    s["forbidden"] = [p for p in FORBIDDEN_ROOT if http(f"{site}/{p}", "HEAD")[0] in (200, 301, 302)]
    for suite in KNOWN_SUITES:
        if http(f"{site}/{suite}/InRelease", "HEAD")[0] != 200 and f"{site}/{suite}/ ./" not in s["index"]:
            continue
        S = {"files": {f: http(f"{site}/{suite}/{f}", "HEAD")[0] for f in SUITE_FILES}}
        code, rel = http(f"{site}/{suite}/Release")
        S["release"] = parse_stanzas(rel.decode(errors="replace"))[0] if code == 200 and rel.strip() else {}
        code, pk = http(f"{site}/{suite}/Packages")
        S["packages"] = [{k: p.get(k, "") for k in ("Package", "Version", "Architecture", "Size")}
                         for p in parse_stanzas(pk.decode(errors="replace"))] if code == 200 else []
        s["suites"][suite] = S
    return s


def repo_facts(d: dict, action_repo: str) -> dict:
    full, oid = d["meta"]["full_name"], d["build_oid"]
    f = dict(d)
    f["repo"], f["name"] = full, d["meta"]["name"]
    info = api(f"repos/{full}")
    f["fork"], f["parent"] = info["fork"], (info.get("parent") or {}).get("full_name")
    f["created_at"] = info["created_at"]
    f["branches"] = [b["name"] for b in api_pages(f"repos/{full}/branches?per_page=100")]
    if d.get("local"):
        # --local: the checkout's files, as they would be pushed.
        f["files"], f["tree_truncated"] = local_files(d["local"]), False
        def read(p):
            return (d["local"] / p).read_text(errors="replace") if p in f["files"] else None
    else:
        tree = api(f"repos/{full}/git/trees/{oid}?recursive=1")
        f["files"] = [e["path"] for e in tree["tree"] if e["type"] == "blob"]
        f["tree_truncated"] = tree.get("truncated", False)
        def read(p):
            return file_at(full, oid, p) if p in f["files"] else None
    f["declaration"] = read(DECLARATION)
    for p in ("README.md", "packaging/README.md", "debian/control", ".gitignore"):
        f[p] = read(p)
    ctl = [p for p in f["files"] if re.fullmatch(r"packaging/debian/[^/]+/control(\.in)?", p)]
    f["other_controls"] = {p: read(p) for p in ctl[:3]}
    # History imported from upstream: commits by other people, older than the
    # repository itself. (A snapshot has only its importer's commits.)
    ours = our_people(full.split("/")[0])
    old = api(f"repos/{full}/commits?sha={oid}&until={info['created_at']}&per_page=100") or []
    authors = {(c.get("author") or {}).get("login") or c["commit"]["author"]["name"] for c in old}
    f["upstream_authors"] = sorted(a for a in authors
                                   if a.lower() not in ours and not a.endswith("[bot]") and a != "actions-user")
    pages = api(f"repos/{full}/pages")
    f["pages"] = pages or {}
    site = ((pages or {}).get("html_url") or "").rstrip("/")
    f["site"] = site_facts(site, f["name"]) if site else None
    return f


# --------------------------------------------------------------------------
# The declared (or inferred) target for a repository.

def published_versions(f: dict) -> dict[str, dict[str, str]]:
    """suite -> package -> newest published version."""
    out = {}
    for suite, S in ((f["site"] or {}).get("suites") or {}).items():
        newest = {}
        for p in S["packages"]:
            if p["Package"] and p["Version"]:
                cur = newest.get(p["Package"])
                if cur is None or version_key(p["Version"]) > version_key(cur):
                    newest[p["Package"]] = p["Version"]
        out[suite] = newest
    return out


def target(f: dict, owner_tag: str | None) -> dict:
    t = {"declared": False, "exceptions": {}}
    decl = {}
    if f["declaration"]:
        try:
            decl = tomllib.loads(f["declaration"])
            t["declared"] = True
        except tomllib.TOMLDecodeError as e:
            t["declaration_error"] = str(e)
    versions = [v for s in published_versions(f).values() for v in s.values()]
    all_arch = {p["Architecture"] for S in ((f["site"] or {}).get("suites") or {}).values() for p in S["packages"]}
    wf = " ".join(code_lines(x) for x in f["workflows"].values())
    has_build = bool(re.search(r"dpkg-buildpackage|build-deb|dpkg-deb|nfpm|debuild", wf))
    if decl.get("kind") in ("A", "B", "aggregate"):
        t["kind"] = decl["kind"]
    elif not has_build:
        t["kind"] = "aggregate"
    elif f["fork"] or "upstream" in f["branches"] or f["upstream_authors"] or any("~bpo" in v for v in versions) or \
            (owner_tag and any(re.search(rf"\+{owner_tag}\d", v) for v in versions)):
        t["kind"] = "A"
    else:
        t["kind"] = "B"
    variant = decl.get("variant")
    if variant is None:
        if any("~bpo" in v for v in versions):
            variant = "backport"
        elif "debian/control" not in f["files"] and any(p.startswith("packaging/debian/") for p in f["files"]):
            variant = "patch-series"
    t["variant"] = variant or ""
    t["nfpm"] = "nfpm" in wf
    arch = decl.get("architectures")
    if arch is None:
        arch = "all" if all_arch and all_arch <= {"all"} else "any"
    t["architectures"] = arch
    archs = [] if arch == "all" else (DEFAULT_ARCH if arch == "any" else list(arch))
    t["arch_default"] = arch in ("any", "all")
    suites = decl.get("suites", "default")
    debian = DEFAULT_DEBIAN if suites == "default" else [s for s in suites if not s.startswith("raspbian-")]
    t["suites_default"] = suites == "default"
    want = list(debian)
    if "armhf" in archs:  # Raspbian is armhf only, and has no sid
        want += [f"raspbian-{s}" for s in debian if s != "sid"]
    if suites != "default":
        want = list(suites)
    t["suites"] = sorted(want, key=KNOWN_SUITES.index)
    t["archs"] = archs
    t["exceptions"] = dict(decl.get("exceptions", {}))
    t["upstream"] = decl.get("upstream")
    raw = decl.get("depends", [])
    try:
        if not isinstance(raw, list) or not all(isinstance(d, dict) for d in raw):
            raise apt_sources.Error("`depends` must be an array of tables, [[depends]]")
        t["depends"], t["depends_error"] = apt_sources.validate(raw), None
    except apt_sources.Error as e:
        t["depends"], t["depends_error"] = [], str(e)
    return t


def arch_for(suite: str, t: dict) -> set[str]:
    if not t["archs"]:
        return {"all"}
    if suite.startswith("raspbian-"):
        return {"armhf"} & set(t["archs"])
    return set(t["archs"]) - ({"riscv64"} if suite in NO_RISCV64 else set())


# --------------------------------------------------------------------------
# Rules that read a workflow's structure.

BUILD_RUN = re.compile(r"\b(dpkg-buildpackage|debuild|dpkg-deb\s+(-b|--build)|nfpm)\b")
WORKFLOW_RUN_GUARD = re.compile(r"github\.event\.workflow_run\.event\s*!=\s*['\"]pull_request['\"]")
# An `if:` with one of these runs even when a job it needs was skipped.
RUNS_ANYWAY = re.compile(r"\b(always|failure|cancelled)\(\)")


def triggers(w: dict) -> dict:
    """A workflow's `on:`, as a dict whatever form it was written in."""
    on = w.get(True, w.get("on", {}))  # YAML 1.1 reads a bare `on` as True
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {k: None for k in on}
    return on if isinstance(on, dict) else {}


def unguarded_workflow_runs(parsed: dict[str, dict]) -> list[str]:
    """PKG-TRIGGERS, in every workflow: one chained by `workflow_run` must not
    act on a run that a pull request triggered. `branches: [main]` there
    matches the triggering run's branch *name*, which a pull request from a
    fork's own `main` has too; so each job needs
    `github.event.workflow_run.event != 'pull_request'` in its `if:`, or
    needs a job that has it (and doesn't run anyway, `always()`)."""
    out = []
    for n, w in sorted(parsed.items()):
        if not isinstance(w, dict) or "workflow_run" not in triggers(w):
            continue
        jobs = {k: j for k, j in (w.get("jobs") or {}).items() if isinstance(j, dict)}

        @functools.cache
        def guarded(name: str) -> bool:
            cond = str(jobs[name].get("if", ""))
            if WORKFLOW_RUN_GUARD.search(cond):
                return True
            needs = jobs[name].get("needs") or []
            needs = [needs] if isinstance(needs, str) else needs
            return not RUNS_ANYWAY.search(cond) and any(guarded(x) for x in needs if x in jobs)

        bad = [name for name in jobs if not guarded(name)]
        if bad:
            out.append(f"{n}: workflow_run job{'s' if len(bad) > 1 else ''} {' '.join(bad)} "
                       "not guarded against pull requests")
    return out


def install_test(jobs: dict, action_repo: str, workflows: dict | None = None) -> tuple[bool, str]:
    """PKG-INSTALL-TEST: a job that builds the packages has a step named
    `Install test` that runs something. A mention elsewhere (a comment, a
    script in the tree, a step in another job) doesn't count.

    A job that builds is the build-deb job, one using the shared build-deb
    action, or one running dpkg-buildpackage, debuild, dpkg-deb --build or
    nfpm; a job calling a local reusable workflow is read through, from
    `workflows` (file name -> parsed). When none of those shows (a build in
    a script), every job but test, release and the publish is taken as a
    build job."""
    shared_step = f"{action_repo}/build-deb".lower()
    shared_workflow = f"{action_repo}/.github/workflows/build-deb.yml".lower()
    workflows = workflows or {}

    def flatten(jobs, prefix="", depth=0):
        for name, j in jobs.items():
            if not isinstance(j, dict):
                continue
            local = re.fullmatch(r"\./\.github/workflows/([^/@]+\.ya?ml)", str(j.get("uses", "")))
            inner = workflows.get(local.group(1)) if local else None
            if isinstance(inner, dict) and depth < 3:
                yield from flatten(inner.get("jobs") or {}, f"{prefix}{name}/", depth + 1)
            else:
                yield f"{prefix}{name}", name, j

    all_jobs = list(flatten(jobs))
    for path, _, j in all_jobs:
        if str(j.get("uses", "")).partition("@")[0].lower() == shared_workflow:
            return True, f"{path}: the shared build-deb.yml install-tests"
    steps = {path: [s for s in (j.get("steps") or []) if isinstance(s, dict)] for path, _, j in all_jobs}
    builders = [path for path, name, _ in all_jobs if name == "build-deb" or any(
        str(s.get("uses", "")).partition("@")[0].lower() == shared_step
        or BUILD_RUN.search(code_lines(str(s.get("run", "")))) for s in steps[path])]
    if not builders:
        builders = [path for path, name, j in all_jobs if name not in ("test", "release")
                    and "/publish-apt.yml@" not in str(j.get("uses", ""))]
    if not builders:
        return False, "no job builds the packages"
    for path in builders:
        for s in steps[path]:
            if re.fullmatch(r"Install test( \(.*\))?", str(s.get("name", "")).strip()) and \
                    (code_lines(str(s.get("run", ""))).strip() or str(s.get("uses", "")).strip()):
                return True, f"`{s['name']}` in {path}"
    return False, f"no `Install test` step in {', '.join(builders)}"


def shared_build(jobs: dict, action_repo: str, local_ver: bool, nfpm: bool, variant: str) -> tuple[bool, str]:
    """PKG-SHARED (docs/packaging.md, "The shared actions"): the build is the
    shared one, at `@main`, and the repository carries no deb-version.py.

    Shared is the reusable build-deb.yml workflow (a job's `uses:`), the
    build-deb action (a step's `uses:`), or, for a build that can't go
    through build-deb, the deb-version action giving the shared version: an
    nfpm build or a patch series' own job. Every use of any of them must be
    at `@main`, the release job's included, since each repository runs
    exactly what's on apt-repo-action's main."""
    repo = action_repo.lower()
    workflow, action, version = (f"{repo}/.github/workflows/build-deb.yml", f"{repo}/build-deb",
                                 f"{repo}/deb-version")
    uses = []
    for j in jobs.values():
        if not isinstance(j, dict):
            continue
        uses.append(str(j.get("uses", "")))
        uses += [str(s.get("uses", "")) for s in (j.get("steps") or []) if isinstance(s, dict)]
    found = {}
    for u in uses:
        path, _, ref = u.partition("@")
        if path.lower() in (workflow, action, version):
            found.setdefault(path.lower(), set()).add(ref)
    off_main = [f"{path.rpartition('/')[2]}@{ref} (want @main)"
                for path, refs in sorted(found.items()) for ref in sorted(refs) if ref != "main"]
    if workflow in found:
        what = "the reusable build-deb.yml"
    elif action in found:
        what = "the build-deb action"
    elif version in found and (nfpm or variant == "patch-series"):
        what = "nfpm with the deb-version action" if nfpm else "the deb-version action in its own job"
    elif version in found:
        what = None
        off_main.insert(0, "deb-version without build-deb, but neither nfpm nor a patch series")
    else:
        what = None
    detail = "; ".join(off_main) if off_main else what or "own build steps"
    if local_ver:
        detail += "; local deb-version.py"
    return bool(what) and not off_main and not local_ver, detail


def key_fingerprints(data: bytes) -> list[str]:
    """The fingerprints of the primary keys in a binary OpenPGP keyring, as
    40 (v4) or 64 (v6) upper-case hex digits (RFC 9580, 5.5.4)."""
    import hashlib
    out, i = [], 0
    while i < len(data):
        b = data[i]
        if not b & 0x80:
            break  # not a packet: armoured, or garbage
        if b & 0x40:  # new format
            tag, o = b & 0x3F, data[i + 1]
            if o < 192:
                n, h = o, 2
            elif o < 224:
                n, h = ((o - 192) << 8) + data[i + 2] + 192, 3
            elif o == 255:
                n, h = int.from_bytes(data[i + 2:i + 6], "big"), 6
            else:
                break  # partial lengths: not in a key
        else:  # old format
            tag, lt = (b >> 2) & 0x0F, b & 3
            if lt == 3:
                break
            size = 1 << lt
            n, h = int.from_bytes(data[i + 1:i + 1 + size], "big"), 1 + size
        body = data[i + h:i + h + n]
        if tag == 6 and body:
            if body[0] == 4:
                out.append(hashlib.sha1(b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper())
            elif body[0] == 6:
                out.append(hashlib.sha256(b"\x9b" + len(body).to_bytes(4, "big") + body).hexdigest().upper())
        i += h + n
    return out


def install_section(doc: str) -> str | None:
    """The `## Install` section of a README: from a heading that is exactly
    `## Install` to the next level-2 heading, ignoring `#` lines in code
    blocks (a shell comment isn't a heading)."""
    lines, start, fence = doc.splitlines(), None, False
    for n, l in enumerate(lines):
        if l.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        if start is None and re.fullmatch(r"## Install[ \t]*#*[ \t]*", l):
            start = n + 1
        elif start is not None and re.match(r"##(?!#)", l):
            return "\n".join(lines[start:n])
    return None if start is None else "\n".join(lines[start:])


def setup_problems(section: str, name: str, site: str, fingerprints: list[str]) -> list[str]:
    """What the setup block of docs/conventions.md ("One setup") for `name` at
    `site` is missing from `section`."""
    # A shell line continuation, inside quotes or not, is one line.
    section = re.sub(r"[ \t]*\\\n[ \t]*", " ", section)
    probs = []
    if f"{site}/{name}.gpg" not in section or f"/etc/apt/keyrings/{name}.gpg" not in section:
        probs.append(f"no key download from {site}/{name}.gpg into /etc/apt/keyrings")
    if not re.search(rf"deb \[signed-by=/etc/apt/keyrings/{re.escape(name)}\.gpg\] {re.escape(site)}/[^\s/]+/ \./",
                     section):
        probs.append(f"no sources line for {site}/<suite>/")
    flat = re.sub(r"\s", "", section).upper()
    if fingerprints and not any(fp in flat for fp in fingerprints):
        probs.append("no key fingerprint")
    return probs


@functools.cache
def pages_site(repo: str) -> str:
    return ((api(f"repos/{repo}/pages") or {}).get("html_url") or "").rstrip("/")


def docs(doc: str | None, name: str, site: str | None, key: bytes, depends: list[dict],
         suites: list[str] = ()) -> tuple[bool, str]:
    """PKG-DOCS (docs/packaging.md, "Documentation"): an `## Install` section
    with this repository's setup block (for one suite; the reader puts in
    their own), naming every published suite, with its key's fingerprint and
    the setup of each dependency repository, and nothing conventions.md
    forbids."""
    section = install_section(doc or "")
    if section is None:
        return False, "no `## Install` section"
    probs = setup_problems(section, name, site, key_fingerprints(key)) if site else ["no Pages site to set up"]
    # A suite name, but not `trixie` inside `raspbian-trixie`.
    unnamed = [x for x in suites if not re.search(rf"(?<![\w-]){re.escape(x)}(?![\w-])", section)]
    if unnamed:
        probs.append(f"doesn't name the published suite{'s' if len(unnamed) > 1 else ''} {' '.join(unnamed)}")
    for e in depends:
        if "repo" in e:
            dep_site = pages_site(e["repo"])
            probs += [f"dependency {e['name']}: {p}" for p in
                      (setup_problems(section, e["name"], dep_site, []) if dep_site else ["no Pages site"])]
        else:
            url = e["url"].partition("{")[0]
            if e["key"] not in section or f"/etc/apt/keyrings/{e['name']}." not in section or url not in section:
                probs.append(f"dependency {e['name']}: no setup for {url} with {e['key']}")
    probs += [f"mentions {p}" for p in INDEX_FORBIDDEN if p in section]
    return not probs, "; ".join(probs) if probs else "Install section with the setup and the key fingerprint"


def changelog(files: list[str], gitignore: str | None, kind: str, nfpm: bool) -> tuple[bool | None, str]:
    """PKG-CHANGELOG (docs/packaging.md, "The changelog"): Set B commits no
    changelog, a patch series' templates included, and with `debian/` at the
    root lists `debian/changelog` in .gitignore, so a local build doesn't
    dirty the tree. Set A keeps its committed one."""
    if kind != "B":
        return None, "Set A keeps its changelog" if kind == "A" else "no build of its own"
    tracked = sorted(p for p in files if p == "debian/changelog" or p.endswith("/debian/changelog")
                     or re.fullmatch(r"packaging/debian/[^/]+/changelog", p))
    if tracked:
        return False, f"commits {', '.join(tracked[:2])}"
    if "debian/control" not in files:
        return (None, "nfpm build") if nfpm else (True, "no committed changelog")
    ignored = {l.strip() for l in (gitignore or "").splitlines()} & {"debian/changelog", "/debian/changelog"}
    return (True, "not committed; ignored") if ignored else (False, "not committed, but not in .gitignore")


# --------------------------------------------------------------------------
# The checks.

def check(f: dict, t: dict, args, owner_tag: str | None, packaging: frozenset[str] = frozenset()) -> dict:
    C = {}
    kind, variant = t["kind"], t["variant"]

    def put(rule, ok, detail=""):
        status = "pass" if ok is True else "na" if ok is None else "fail"
        if status == "fail" and rule in t["exceptions"]:
            status, detail = "exception", f"{detail} [{t['exceptions'][rule]}]"
        C[rule] = {"status": status, "detail": detail}

    def excepted(rule, ok, detail, default):
        """A declared non-default target: fine only with a reason."""
        if ok and not default:
            if rule in t["exceptions"]:
                C[rule] = {"status": "exception", "detail": f"{detail} [{t['exceptions'][rule]}]"}
            else:
                C[rule] = {"status": "fail", "detail": f"{detail}: not a default, and no reason declared"}
        else:
            put(rule, ok, detail)

    # --- Repository
    if t.get("declaration_error"):
        put("PKG-DECLARED", False, f"{DECLARATION} doesn't parse: {t['declaration_error']}")
    else:
        put("PKG-DECLARED", t["declared"], f"kind {kind}" + (f" ({variant})" if variant else "")
            + ("" if t["declared"] else " — inferred"))
    want = DEFAULT_BRANCH[kind]
    put("PKG-BRANCH", f["default"] == want and f["build_ref"] == f["default"],
        f"default {f['default']}" + (f", publishes from {f['build_ref']}" if f["build_ref"] != f["default"] else "")
        + f" (want {want})")
    set_a = kind == "A" and variant != "backport"
    if set_a:
        others = f["upstream_authors"]
        put("PKG-HISTORY", f["fork"] or bool(others),
            f"fork of {f['parent']}" if f["fork"] else
            f"history imported ({', '.join(others[:3])}{' …' if len(others) > 3 else ''})" if others else
            "no upstream history (a snapshot)")
        put("PKG-UPSTREAM", "upstream" in f["branches"], "`upstream` branch" if "upstream" in f["branches"] else "no `upstream` branch")
        has = ".github/workflows/sync-upstream.yml" in f["files"]
        put("PKG-SYNC", has, "sync-upstream.yml" if has else "no sync-upstream.yml")
    else:
        for r in ("PKG-HISTORY", "PKG-UPSTREAM"):
            put(r, None, "backport" if kind == "A" else f"Set {kind}")
        if kind == "A":
            sched = any(re.search(r"^\s*schedule:", code_lines(x), re.M) for x in f["workflows"].values())
            put("PKG-SYNC", sched, "scheduled rebuild" if sched else "no scheduled rebuild")
        else:
            put("PKG-SYNC", None, f"Set {kind}")
    if kind == "A":
        has = f["packaging/README.md"] is not None
        put("PKG-README", has, "packaging/README.md" if has else "no packaging/README.md")
    else:
        put("PKG-README", None, f"Set {kind}")
    if kind == "aggregate" or variant == "backport":
        put("PKG-DEBIAN", None, "no build of its own" if kind == "aggregate" else "Debian's source package")
    elif "debian/control" in f["files"]:
        put("PKG-DEBIAN", True, "debian/ at the root")
    elif variant == "patch-series" and any(p.startswith("packaging/debian/") for p in f["files"]):
        put("PKG-DEBIAN", True, "packaging/debian/<name>/")
    elif t["nfpm"]:
        put("PKG-DEBIAN", True, "nfpm build")
    else:
        where = sorted({p.rsplit("/control", 1)[0] for p in f["files"] if p.endswith("debian/control")})
        put("PKG-DEBIAN", False, "no debian/ at the root" + (f" (found {', '.join(where)})" if where else ""))
    put("PKG-CHANGELOG", *changelog(f["files"], f[".gitignore"], kind, t["nfpm"]))
    if t["depends_error"]:
        put("PKG-DEPENDS", False, t["depends_error"])
    elif not t["depends"]:
        put("PKG-DEPENDS", None, "no [[depends]]")
    else:
        # A `repo` dependency is one of ours; say whether it was found as a
        # packaging repository in this scan (it may be another owner's).
        put("PKG-DEPENDS", True, "; ".join(
            (f"{e['repo']} ({'packaging repository' if e['repo'].lower() in packaging else 'not found in this scan'})"
             if "repo" in e else e["url"]) + (f" for {' '.join(e['suites'])}" if e["suites"] else "")
            for e in t["depends"]))

    # --- Workflow
    publish_uses = f"{args.action_repo}/.github/workflows/publish-apt.yml@"
    parsed = {}
    for n, text in f["workflows"].items():
        try:
            parsed[n] = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            parsed[n] = {}
    def publishes(w):
        return any(isinstance(j, dict) and str(j.get("uses", "")).startswith(publish_uses)
                   for j in (w.get("jobs") or {}).values())
    candidates = [n for n, w in parsed.items() if isinstance(w, dict) and publishes(w)] or \
        [n for n, x in f["workflows"].items() if HANDROLLED.search(code_lines(x))]
    wf_name = WORKFLOW_FILE if WORKFLOW_FILE in candidates else (candidates[0] if candidates else None)
    w = parsed.get(wf_name) or {}
    f["build_workflow"] = wf_name
    put("PKG-WORKFLOW", wf_name == WORKFLOW_FILE and w.get("name") == WORKFLOW_NAME,
        f"{wf_name} “{w.get('name', '')}”" if wf_name else "no publishing workflow")
    jobs = w.get("jobs") or {}
    need = {"publish-apt"} if kind == "aggregate" else {"build-deb", "publish-apt"}
    put("PKG-JOBS", need <= set(jobs) and set(jobs) <= JOBS, " ".join(jobs) or "none")
    on = triggers(w)
    bad = []
    push = on.get("push") or {}
    if "push" not in on:
        bad.append("no push")
    elif (push.get("branches") if isinstance(push, dict) else None) != [f["default"]]:
        bad.append(f"push branches {push.get('branches') if isinstance(push, dict) else None}")
    for trig in ("pull_request", "workflow_dispatch"):
        if trig not in on:
            bad.append(f"no {trig}")
    extra = set(on) - {"push", "pull_request", "workflow_dispatch", "schedule"}
    if extra:
        bad.append(" ".join(sorted(extra)))
    if "schedule" in on and kind != "A":
        bad.append("schedule")
    if any(isinstance(v, dict) and ({"paths", "paths-ignore"} & set(v)) for v in on.values()):
        bad.append("paths filter")
    # The other workflows too: one chained to this one by workflow_run.
    bad += unguarded_workflow_runs({n: x for n, x in parsed.items() if n != wf_name})
    put("PKG-TRIGGERS", not bad, " ".join(str(k) for k in on) + (f" — {'; '.join(bad)}" if bad else ""))
    pub = next((j for j in jobs.values() if isinstance(j, dict) and str(j.get("uses", "")).startswith(publish_uses)), {})
    cond = str(pub.get("if", ""))
    guarded = "pull_request" in cond and "!=" in cond
    put("PKG-PREVIEW", "pull_request" in on and guarded,
        ("pull requests build" if "pull_request" in on else "pull requests don't build")
        + ("; publish guarded" if guarded else "; publish-apt not guarded against pull requests"))
    conc = w.get("concurrency") or {}
    ok = isinstance(conc, dict) and conc.get("group") == CONCURRENCY_GROUP and \
        str(conc.get("cancel-in-progress")) == CONCURRENCY_CANCEL
    put("PKG-CONCURRENCY", ok, "deb-<ref>, pull requests only" if ok else
        (f"group {conc.get('group') if isinstance(conc, dict) else conc}" if conc else "none"))
    ref = str(pub.get("uses", "")).rpartition("@")[2] if pub else None
    put("PKG-PUBLISHER", ref == "main", f"publish-apt.yml@{ref}" if ref else "not through publish-apt.yml")
    if kind == "aggregate":
        put("PKG-SHARED", None, "nothing to build")
    else:
        put("PKG-SHARED", *shared_build(jobs, args.action_repo, "packaging/deb-version.py" in f["files"],
                                        t["nfpm"], variant))
    if kind == "aggregate":
        put("PKG-INSTALL-TEST", None, "nothing to build")
    else:
        put("PKG-INSTALL-TEST", *install_test(jobs, args.action_repo, parsed))

    # --- Packages, from the live site
    site = f["site"] or {"suites": {}}
    have = sorted(site["suites"], key=KNOWN_SUITES.index)
    missing = [s for s in t["suites"] if s not in have]
    extra_s = [s for s in have if s not in t["suites"]]
    excepted("PKG-SUITES", not missing and not extra_s,
             " ".join(have) + (f" — add {' '.join(missing)}" if missing else "") + (f" — drop {' '.join(extra_s)}" if extra_s else ""),
             t["suites_default"])
    arch_bad = []
    for suite in have:
        built = {p["Architecture"] for p in site["suites"][suite]["packages"]}
        want_a = arch_for(suite, t)
        miss = sorted(want_a - built)
        more = sorted(built - want_a - {"all"})
        if miss or more:
            arch_bad.append(f"{suite}: " + (f"add {' '.join(miss)}" if miss else "") + (f" drop {' '.join(more)}" if more else ""))
        adv = set(site["suites"][suite]["release"].get("Architectures", "").split())
        if adv and adv != built:
            arch_bad.append(f"{suite} advertises {' '.join(sorted(adv - built))} with no packages" if adv - built else
                            f"{suite} doesn't advertise {' '.join(sorted(built - adv))}")
    union = sorted({p["Architecture"] for S in site["suites"].values() for p in S["packages"]})
    excepted("PKG-ARCH", not arch_bad, " ".join(union) + (" — " + "; ".join(arch_bad) if arch_bad else ""), t["arch_default"])
    newest = published_versions(f)
    flat = sorted({v for s in newest.values() for v in s.values()})
    dated = [v for v in flat if DATE.search(v)]
    put("PKG-NODATES", None if kind == "aggregate" and not dated else not dated,
        ("date-based: " + ", ".join(dated[:2])) if dated else "count-based")
    tag = re.escape(owner_tag) if owner_tag else "[a-z]+"
    suffix = r"(~deb\d+)?"
    if kind == "aggregate":
        forms = None
    elif variant == "backport":
        forms = re.compile(r".+~bpo\d+\+\d+")
    elif kind == "A":
        forms = re.compile(rf"(\d+:)?[^:]+-[^-+]*\+{tag}\d+{suffix}")
    elif variant == "patch-series":
        forms = re.compile(rf"(\d+:)?.+\+{tag}\.\d+\.\d+(\.\d+)?(\.post\d+)?{suffix}")
    else:
        forms = re.compile(rf"(\d+:)?\d+\.\d+(\.\d+)?(\.post\d+)?{suffix}")
    if forms is None:
        put("PKG-VERSION", None, "versions come from each package's own repository")
    else:
        bad_v = [v for v in flat if not forms.fullmatch(v)]
        epoch = [v for v in flat if re.match(r"\d+:", v)]
        put("PKG-VERSION", not bad_v and not epoch,
            ("; ".join(bad_v[:2]) + (" …" if len(bad_v) > 2 else "")) if bad_v else
            (f"epoch: {epoch[0]}" if epoch else ("e.g. " + flat[-1] if flat else "no packages")))
    if kind == "aggregate" or variant == "backport":
        put("PKG-SUITE-SUFFIX", None, "no build of its own" if kind == "aggregate" else "~bpo<R> orders it")
    else:
        wrong = {}
        for suite, pkgs in newest.items():
            codename = suite.removeprefix("raspbian-")
            for p, v in pkgs.items():
                if (codename == "sid" and "~deb" in v) or \
                        (codename in DEBIAN_RELEASE and f"~deb{DEBIAN_RELEASE[codename]}" not in v):
                    wrong.setdefault(suite, v)
        multi = [s for s in have if s != "sid"]
        put("PKG-SUITE-SUFFIX", None if not multi else not wrong,
            "sid only" if not multi else ("; ".join(f"{s} {v}" for s, v in list(wrong.items())[:2]) if wrong else "~deb<R> on every suite"))
    big = sorted({f"{p['Package']} {int(p['Size'] or 0) / 1e6:.0f} MB" for S in site["suites"].values()
                  for p in S["packages"] if p["Package"].endswith("-dbgsym") and int(p["Size"] or 0) > DBGSYM_LIMIT})
    put("PKG-DBGSYM", not big, ", ".join(big[:2]) if big else "none over 10 MB")

    # --- Metadata
    ctl = f["debian/control"] or next((c for c in f["other_controls"].values() if c), None)
    m = re.search(r"^Maintainer:\s*(.*)$", ctl or "", re.M)
    if not ctl or not args.maintainer:
        put("PKG-MAINTAINER", None, "no debian/control" if not ctl else "no --maintainer given")
    else:
        found = m.group(1).strip() if m else "none"
        put("PKG-MAINTAINER", found == args.maintainer, found)
    doc_path = "packaging/README.md" if kind == "A" else "README.md"
    site_url = (f["site"] or {}).get("site")
    key = ((f["site"] or {}).get("root") or {}).get(f"{f['name']}.gpg", (0, b""))[1]
    ok, detail = docs(f[doc_path], f["name"], site_url, key, t["depends"],
                      sorted((f["site"] or {}).get("suites") or {}, key=KNOWN_SUITES.index))
    put("PKG-DOCS", ok, f"{doc_path}: {detail}")

    # --- Published repository
    pages = f["pages"]
    put("REPO-PAGES", pages.get("build_type") == "workflow" and bool(pages.get("https_enforced")),
        f"build {pages.get('build_type')}, https_enforced {pages.get('https_enforced')}" if pages else "no Pages site")
    if not f["site"]:
        for r in ("REPO-KEYS", "REPO-LAYOUT", "REPO-RELEASE", "REPO-INDEX"):
            put(r, False, "no Pages site")
        return C
    gpg, asc = site["root"][f"{f['name']}.gpg"], site["root"][f"{f['name']}.asc"]
    probs = []
    if gpg[0] != 200:
        probs.append(f"no {f['name']}.gpg")
    elif gpg[1].startswith(ARMOUR):
        probs.append(f"{f['name']}.gpg is armoured")
    if asc[0] != 200:
        probs.append(f"no {f['name']}.asc")
    elif not asc[1].startswith(ARMOUR):
        probs.append(f"{f['name']}.asc isn't armoured")
    put("REPO-KEYS", not probs, "; ".join(probs) or f"{f['name']}.gpg, {f['name']}.asc")
    probs = [f"root has {p}" for p in site["forbidden"]]
    for suite, S in site["suites"].items():
        miss = [x for x, c in S["files"].items() if c != 200]
        if miss:
            probs.append(f"{suite} lacks {' '.join(miss)}")
    if not site["suites"]:
        probs.append("no suites")
    put("REPO-LAYOUT", not probs, "; ".join(probs[:3]) or f"{len(site['suites'])} flat suites")
    probs = []
    for suite, S in site["suites"].items():
        r = S["release"]
        if r.get("Origin") != f["name"] or r.get("Label") != f["name"]:
            probs.append(f"{suite}: Origin {r.get('Origin')} Label {r.get('Label')}")
        if r.get("Suite") != "stable" or r.get("Codename") != suite:
            probs.append(f"{suite}: Suite {r.get('Suite')} Codename {r.get('Codename')}")
    put("REPO-RELEASE", not probs, "; ".join(probs[:2]) or "Origin = Label = repo")
    idx = site["index"]
    probs = [f"no setup for {s}" for s in site["suites"]
             if f"signed-by=/etc/apt/keyrings/{f['name']}.gpg] {site['site']}/{s}/ ./" not in idx]
    probs += [f"mentions {p}" for p in INDEX_FORBIDDEN if p in idx]
    put("REPO-INDEX", bool(idx) and not probs, "; ".join(probs[:2]) if probs else ("the one setup" if idx else "no index page"))
    return C


# --------------------------------------------------------------------------
# Reports.

def todo(rule: str, c: dict, action_repo: str) -> str:
    return RULE[rule][3].replace("<action-repo>", action_repo) + (f" Now: {c['detail']}." if c["detail"] else "")


def to_markdown(report: dict) -> str:
    out = [f"# apt compliance, {report['date']}", "",
           f"{len(report['repos'])} packaging repositories in {', '.join(report['owners'])}.", ""]
    for r in report["repos"]:
        fails = [k for k, c in r["checks"].items() if c["status"] == "fail"]
        out += [f"## {r['repo']}", "", f"Kind {r['kind']}{' (' + r['variant'] + ')' if r['variant'] else ''}; "
                f"{len(fails)} failing.", ""]
        out += [f"- [ ] **{k}** {todo(k, r['checks'][k], report['action_repo'])}" for k in fails] or ["All rules pass."]
        out.append("")
    if report["sites_without_packaging"]:
        out += ["## Sites without packaging", ""]
        out += [f"- {s['repo']}: {s['site']} serves an apt key ({' '.join(s['suites']) or 'no suites'})"
                for s in report["sites_without_packaging"]]
    return "\n".join(out) + "\n"


CSS = """
:root{--ground:#f4f7f6;--surface:#ffffff;--ink:#17211e;--muted:#5a6964;--line:#d3dcd8;--accent:#2c5a86;
--pass:#2e7d4f;--pass-bg:#e3f2e8;--fail:#b3261e;--fail-bg:#fbe5e3;--exc:#9a5b00;--exc-bg:#fcefd9;--na:#8a9793;--na-bg:transparent;
--kind-A:#2f5f9e;--kind-A-bg:#e8f0fa;--kind-B:#6a4a9c;--kind-B-bg:#f1ebf8;--kind-aggregate:#4d5d66;--kind-aggregate-bg:#eceff1}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--ground:#101615;--surface:#172020;
--ink:#e2ebe8;--muted:#98a8a2;--line:#2c3936;--accent:#8cb7e0;--pass:#7fd19c;--pass-bg:#16301f;--fail:#ff9d94;
--fail-bg:#3a1a18;--exc:#f2c071;--exc-bg:#35280f;--na:#667570;
--kind-A:#93b8ea;--kind-A-bg:#18233a;--kind-B:#c2a8ea;--kind-B-bg:#241b35;--kind-aggregate:#a9b7bf;--kind-aggregate-bg:#1d2427}}
:root[data-theme="dark"]{color-scheme:dark;--ground:#101615;--surface:#172020;--ink:#e2ebe8;--muted:#98a8a2;--line:#2c3936;
--accent:#8cb7e0;--pass:#7fd19c;--pass-bg:#16301f;--fail:#ff9d94;--fail-bg:#3a1a18;--exc:#f2c071;--exc-bg:#35280f;--na:#667570;
--kind-A:#93b8ea;--kind-A-bg:#18233a;--kind-B:#c2a8ea;--kind-B-bg:#241b35;--kind-aggregate:#a9b7bf;--kind-aggregate-bg:#1d2427}
body{background:var(--ground);color:var(--ink);font:15px/1.55 "IBM Plex Sans",system-ui,sans-serif;padding:32px 20px 64px}
main{max-width:1280px;margin:0 auto;display:grid;gap:36px}
h1,h2,h3{text-wrap:balance;line-height:1.2;margin:0}h1{font-size:28px;font-weight:600}h2{font-size:20px;font-weight:600}
h3{font-size:15px;font-weight:600;font-family:"IBM Plex Mono",ui-monospace,monospace}
p{margin:0;max-width:70ch}a{color:var(--accent)}code,.mono{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.88em}
section{display:grid;gap:14px}.lede{color:var(--muted)}
.stats{display:flex;flex-wrap:wrap;gap:10px 28px}.stats div{display:grid}.stats b{font-size:24px;font-variant-numeric:tabular-nums}
.stats span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
table{border-collapse:collapse;font-size:13px;width:max-content;min-width:100%}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:center;white-space:nowrap}
thead th{font:500 11px "IBM Plex Mono",ui-monospace,monospace;color:var(--muted);vertical-align:bottom;
writing-mode:vertical-rl;transform:rotate(180deg);padding:10px 6px;text-align:left}
th.repo,td.repo{position:sticky;left:0;background:var(--surface);text-align:left;writing-mode:horizontal-tb;transform:none;
border-right:1px solid var(--line)}td.repo a{text-decoration:none;color:var(--ink)}td.kind{color:var(--muted);font-size:12px}
td.num{font-variant-numeric:tabular-nums;font-weight:600}
.s{display:inline-block;min-width:22px;border-radius:4px;font-weight:700;font-family:"IBM Plex Mono",ui-monospace,monospace;cursor:help}
.pass{color:var(--pass);background:var(--pass-bg)}.fail{color:var(--fail);background:var(--fail-bg)}
.exception{color:var(--exc);background:var(--exc-bg)}.na{color:var(--na)}
.todos{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,380px),1fr));gap:14px}
.todo{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px 16px;display:grid;gap:8px;align-content:start}
.todo .meta{color:var(--muted);font-size:13px}.todo ul{margin:0;padding-left:18px;display:grid;gap:6px;font-size:14px}
.todo li b{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;font-weight:600}
tr.kind-A td{background:var(--kind-A-bg)}tr.kind-B td{background:var(--kind-B-bg)}
tr.kind-aggregate td{background:var(--kind-aggregate-bg)}
tr.kind-A td.repo{box-shadow:inset 4px 0 var(--kind-A)}tr.kind-B td.repo{box-shadow:inset 4px 0 var(--kind-B)}
tr.kind-aggregate td.repo{box-shadow:inset 4px 0 var(--kind-aggregate)}
tr[class^="kind-"] td.kind{font-weight:600}tr.kind-A td.kind{color:var(--kind-A)}tr.kind-B td.kind{color:var(--kind-B)}
tr.kind-aggregate td.kind{color:var(--kind-aggregate)}
.k{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:4px}
.k-A{background:var(--kind-A)}.k-B{background:var(--kind-B)}.k-aggregate{background:var(--kind-aggregate)}
.rules{font-size:14px}.rules td{text-align:left;white-space:normal}.rules td:first-child{white-space:nowrap}
"""
KIND_ORDER = {"A": 0, "B": 1, "aggregate": 2}
SYMBOL = {"pass": "✓", "fail": "✗", "exception": "E", "na": "·"}


def to_html(report: dict) -> str:
    e = html.escape
    repos = report["repos"]
    fails = sum(c["status"] == "fail" for r in repos for c in r["checks"].values())
    clean = sum(all(c["status"] != "fail" for c in r["checks"].values()) for r in repos)
    groups = list(dict.fromkeys(r[1] for r in RULES))
    out = ['<title>Apt Repository Compliance</title>',
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700'
           '&family=IBM+Plex+Sans:wght@400;500;600&display=swap">', f"<style>{CSS}</style>", "<main>",
           "<section><h1>Apt repository compliance</h1>",
           f'<p class="lede">Every repository in {e(", ".join(report["owners"]))} whose workflows publish an apt '
           f'repository, checked against <code>docs/packaging.md</code> and <code>docs/conventions.md</code> on '
           f'{e(report["date"])}. Hover a cell for the detail.</p>',
           '<div class="stats">'
           f'<div><b>{len(repos)}</b><span>packaging repositories</span></div>'
           f'<div><b>{clean}</b><span>passing every rule</span></div>'
           f'<div><b>{fails}</b><span>failing checks</span></div>'
           f'<div><b>{len(report["sites_without_packaging"])}</b><span>sites without packaging</span></div></div>',
           '<div class="legend"><span><span class="s pass">✓</span> passes</span><span><span class="s fail">✗</span> fails</span>'
           '<span><span class="s exception">E</span> declared exception</span><span><span class="s na">·</span> does not apply</span></div>'
           '<div class="legend">Rows by kind: <span><span class="k k-A"></span>Set A, someone else\'s code</span>'
           '<span><span class="k k-B"></span>Set B, our code</span>'
           '<span><span class="k k-aggregate"></span>aggregate, collects other repositories\' packages</span></div>'
           "</section>"]
    for g in groups:
        ids = [r[0] for r in RULES if r[1] == g]
        out.append(f"<section><h2>{e(g)}</h2><div class=\"scroll\"><table><thead><tr><th class=\"repo\">repository</th>"
                   "<th class=\"repo\">kind</th><th class=\"repo\">fails</th>"
                   + "".join(f'<th title="{e(RULE[i][2])}">{e(i)}</th>' for i in ids) + "</tr></thead><tbody>")
        for r in sorted(repos, key=lambda r: (KIND_ORDER.get(r["kind"], len(KIND_ORDER)), r["repo"])):
            n = sum(r["checks"][i]["status"] == "fail" for i in ids)
            cells = "".join(f'<td><span class="s {r["checks"][i]["status"]}" title="{e(i)}: {e(r["checks"][i]["detail"])}">'
                            f'{SYMBOL[r["checks"][i]["status"]]}</span></td>' for i in ids)
            kind = r["kind"] + (f" · {r['variant']}" if r["variant"] else "")
            out.append(f'<tr class="kind-{e(r["kind"])}"><td class="repo"><a href="#todo-{e(r["repo"])}">{e(r["repo"])}</a></td>'
                       f'<td class="kind">{e(kind)}</td><td class="num">{n or ""}</td>{cells}</tr>')
        out.append("</tbody></table></div></section>")
    out.append('<section><h2>What to do, per repository</h2><div class="todos">')
    for r in sorted(repos, key=lambda r: -sum(c["status"] == "fail" for c in r["checks"].values())):
        items = [f'<li><b>{e(k)}</b> {e(todo(k, c, report["action_repo"]))}</li>'
                 for k, c in r["checks"].items() if c["status"] == "fail"]
        site = f' · <a href="{e(r["site"])}">{e(r["site"])}</a>' if r["site"] else ""
        out.append(f'<div class="todo" id="todo-{e(r["repo"])}"><h3><a href="https://github.com/{e(r["repo"])}">'
                   f'{e(r["repo"])}</a></h3><div class="meta">kind {e(r["kind"])}'
                   f'{" · " + e(r["variant"]) if r["variant"] else ""} · publishes from '
                   f'<code>{e(r["build_ref"])}</code>{site}</div>'
                   + (f"<ul>{''.join(items)}</ul>" if items else "<p>Passes every rule.</p>") + "</div>")
    out.append("</div></section>")
    out.append("<section><h2>Sites without packaging</h2>")
    if report["sites_without_packaging"]:
        out.append("<p>These Pages sites serve an apt key, but no workflow in the repository publishes packages any "
                   "more: whatever they serve is frozen.</p><ul>")
        out += [f'<li><a href="https://github.com/{e(s["repo"])}">{e(s["repo"])}</a>: <a href="{e(s["site"])}">'
                f'{e(s["site"])}</a> <span class="mono">{e(" ".join(s["suites"]) or "no suites")}</span></li>'
                for s in report["sites_without_packaging"]]
        out.append("</ul>")
    else:
        out.append("<p>None.</p>")
    out.append('</section><section><h2>Rules</h2><div class="scroll"><table class="rules"><tbody>')
    out += [f"<tr><td><code>{e(i)}</code></td><td>{e(g)}</td><td>{e(m)}</td></tr>" for i, g, m, _ in RULES]
    out.append("</tbody></table></div></section></main>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------

def origin_repo(path: Path) -> str | None:
    """owner/name of a checkout's GitHub origin."""
    r = subprocess.run(["git", "-C", str(path), "remote", "get-url", "origin"], capture_output=True, text=True)
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?/?$", r.stdout.strip())
    return m.group(1) if m else None


def action_repo_default() -> str | None:
    return origin_repo(Path(__file__).resolve().parent)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--owner", action="append", required=True, metavar="OWNER[=TAG]",
                   help="a GitHub user or organisation to scan, with its Set A version tag (repeatable)")
    p.add_argument("--maintainer", help="the Maintainer: every package must have")
    p.add_argument("--action-repo", default=action_repo_default(),
                   help="the repository holding publish-apt.yml (default: this checkout's origin)")
    p.add_argument("--repo", action="append", default=[],
                   help="check only these repositories (owner/name), without scanning their owners' others")
    p.add_argument("--local", type=Path, metavar="PATH",
                   help="check the checkout at PATH (its workflows, declaration, debian/, README) instead of what "
                        "GitHub has; the branch, history and live site still come from GitHub. The repository is "
                        "--repo, or PATH's origin")
    p.add_argument("--json", type=Path)
    p.add_argument("--markdown", type=Path)
    p.add_argument("--html", type=Path)
    args = p.parse_args()
    if not args.action_repo:
        p.error("--action-repo is needed: it can't be read from this checkout's origin")
    owners = dict((o.split("=", 1) + [None])[:2] for o in args.owner)
    if args.local:
        if len(args.repo) > 1:
            p.error("--local checks one repository: give at most one --repo")
        name = args.repo[0] if args.repo else origin_repo(args.local)
        if not name:
            p.error(f"--repo is needed: {args.local} has no GitHub origin")
        args.repo = [name]
        packaging, orphans = [local_entry(name, args.local.resolve(), args.action_repo)], []
    elif args.repo:
        # Only the named repositories: no scan of their owners' others.
        packaging, orphans = classify(named(args.repo), args.action_repo)
        for n in sorted(set(args.repo) - {d["meta"]["full_name"] for d in packaging}):
            print(f"warning: {n} is not a packaging repository", file=sys.stderr)
    else:
        packaging, orphans = discover(list(owners), args.action_repo)
    found = {d["meta"]["full_name"].lower() for d in packaging}
    print(f"discover: {len(packaging)} packaging repositories, {len(orphans)} sites without packaging", file=sys.stderr)
    with ThreadPoolExecutor(6) as ex:
        facts = list(ex.map(lambda d: repo_facts(d, args.action_repo), packaging))
    targets = [target(f, owners.get(f["repo"].split("/")[0])) for f in facts]
    if args.repo:
        # Without a scan, look at each dependency repository itself.
        deps = sorted({e["repo"] for t in targets for e in t["depends"] if "repo" in e} - found)
        if deps:
            found |= {d["meta"]["full_name"].lower() for d in classify(named(deps), args.action_repo)[0]}
    repos = []
    for f, t in zip(facts, targets):
        tag = owners.get(f["repo"].split("/")[0])
        checks = check(f, t, args, tag, frozenset(found))
        repos.append({"repo": f["repo"], "kind": t["kind"], "variant": t["variant"], "declared": t["declared"],
                      "build_ref": f["build_ref"], "build_workflow": f["build_workflow"],
                      "site": (f["site"] or {}).get("site"), "target_suites": t["suites"],
                      "target_architectures": t["archs"] or ["all"], "checks": checks,
                      **({"local": str(f["local"])} if f.get("local") else {})})
    import datetime
    report = {"date": datetime.date.today().isoformat(), "owners": list(owners), "action_repo": args.action_repo,
              "repos": repos, "sites_without_packaging": orphans}
    if args.json:
        args.json.write_text(json.dumps(report, indent=1) + "\n")
    if args.markdown:
        args.markdown.write_text(to_markdown(report))
    if args.html:
        args.html.write_text(to_html(report))
    for r in repos:
        n = sum(c["status"] == "fail" for c in r["checks"].values())
        print(f"{r['repo']:45} {r['kind']:9} {r['variant']:13} {n:2} failing")
    for s in orphans:
        print(f"{s['repo']:45} site without packaging: {s['site']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
