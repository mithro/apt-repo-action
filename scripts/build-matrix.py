#!/usr/bin/env python3
"""Plan the builds of the reusable build-deb.yml workflow.

From a source tree's declaration (.github/apt-packaging.toml) and its
debian/control, or from the workflow's inputs where they are given, works
out which suites and architectures to build (docs/packaging.md, "Suites" and
"Architectures"), and writes, as GitHub step outputs:

- ``build``: the build matrix, one job per suite and architecture, each with
  its runner and whether it also builds the Architecture: all packages
  (exactly one job per suite does, so no two jobs publish different files
  under the same name). An Architecture: all repository that bundles a
  dependency into raspbian-<codename> has no Raspbian job: the <codename>
  job carries ``also: raspbian-<codename>`` and uploads the same files for
  it too;
- ``install``: one install test per suite, on a native architecture;
- ``suites``: the suites, for publish-apt;
- ``architectures``: the architectures built, plus ``all`` when a package is
  architecture-independent, across all suites. For information only:
  publish-apt advertises each suite's own architectures, read from its
  packages.

The declaration's semantics are packaging.md's: ``architectures`` is "any"
(the default set), "all" or a list; ``suites`` is "default" or the exact
list. "default" suites are the Debian defaults, plus raspbian-<codename> for
each but sid when armhf is built.

Usage:
    build-matrix.py --source-dir <dir> [--suites declared|default|<list>]
                    [--architectures declared|any|all|<list>]
                    [--github-output <file>]

Standard library only.
"""
import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

# docs/packaging.md, "Suites" and "Architectures".
DEFAULT_DEBIAN = ["trixie", "forky", "sid"]
DEBIAN = ["bookworm", "trixie", "forky", "sid"]
RASPBIAN = ["raspbian-bookworm", "raspbian-trixie", "raspbian-forky"]   # no raspbian-sid
KNOWN_SUITES = DEBIAN + RASPBIAN
DEFAULT_ARCH = ["amd64", "i386", "arm64", "armhf", "riscv64"]
NO_RISCV64 = {"bookworm"}   # no official riscv64 in bookworm
# GitHub's arm64 runners run armhf natively; riscv64 is emulated either way.
RUNNER = {"amd64": "ubuntu-24.04", "i386": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm",
          "armhf": "ubuntu-24.04-arm", "riscv64": "ubuntu-24.04", "all": "ubuntu-24.04"}
# Which job of a suite builds the Architecture: all packages, and which
# architecture the install test runs on: the first of these that is built.
# All but riscv64 are native on their runner.
PREFERENCE = ["amd64", "arm64", "i386", "armhf", "riscv64"]


class Error(Exception):
    pass


def words(value, what: str) -> list[str]:
    """A TOML list, or a space-separated string (a workflow input)."""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        out = list(value)
    elif isinstance(value, str):
        out = value.split()
    else:
        raise Error(f"{what} must be a string or a list of strings, not {value!r}")
    dups = sorted({v for v in out if out.count(v) > 1})
    if dups:
        raise Error(f"{what} lists {', '.join(dups)} twice")
    return out


def bundled_suites(declaration: dict, suites: list[str]) -> set[str]:
    """The suites a `bundle` dependency applies to (docs/packaging.md,
    "Bundling a dependency repository"): every suite, or its `suites`."""
    out = set()
    for d in declaration.get("depends", []) or []:
        if isinstance(d, dict) and d.get("bundle") not in (None, False):
            only = d.get("suites")
            out |= set(suites) if not only else set(suites) & set(only if isinstance(only, list) else [])
    return out


def control_architectures(text: str) -> list[str]:
    """The Architecture: of each binary package in a debian/control."""
    archs = []
    # deb822(5): a line of only spaces and tabs also ends a paragraph.
    for stanza in re.split(r"\n[ \t]*\n", text):
        fields = {}
        for line in stanza.splitlines():
            if line[:1] in (" ", "\t", "#") or ":" not in line:
                continue
            name, value = line.split(":", 1)
            fields[name.strip().lower()] = value.strip()
        if "package" in fields and "architecture" in fields:
            archs.append(fields["architecture"])
    return archs


def plan(declaration: dict, control: list[str], suites_input: str, archs_input: str) -> dict:
    has_all = "all" in control
    has_dependent = any(a != "all" for a in control)

    arch = declaration.get("architectures", "any") if archs_input == "declared" else archs_input
    if arch in ("any", "default"):
        archs = list(DEFAULT_ARCH)
    elif arch == "all":
        archs = ["all"]
    else:
        archs = words(arch, "architectures")
        unknown = [a for a in archs if a not in DEFAULT_ARCH]
        if unknown:
            raise Error(f"unknown architecture {', '.join(unknown)} (known: {' '.join(DEFAULT_ARCH)}, "
                        "or all)")
    all_arch = archs == ["all"]
    if all_arch and has_dependent:
        raise Error('architectures = "all", but debian/control has architecture-dependent packages, '
                    "which an Architecture: all build (dpkg-buildpackage -A) would leave out")
    if not all_arch and not has_dependent:
        raise Error('every package in debian/control is Architecture: all: declare '
                    'architectures = "all" (docs/packaging.md, "Architectures")')

    suite = declaration.get("suites", "default") if suites_input == "declared" else suites_input
    if suite == "default":
        suites = list(DEFAULT_DEBIAN)
        if "armhf" in archs:   # Raspbian is armhf only, and has no sid
            suites += [f"raspbian-{s}" for s in DEFAULT_DEBIAN if s != "sid"]
    else:
        suites = words(suite, "suites")
        unknown = [s for s in suites if s not in KNOWN_SUITES]
        if unknown:
            raise Error(f"unknown suite {', '.join(unknown)} (known: {' '.join(KNOWN_SUITES)})")
        suites.sort(key=KNOWN_SUITES.index)
    raspbian = [s for s in suites if s in RASPBIAN]
    # An Architecture: all repository publishes only the Debian suites, which
    # Raspbian hosts use: its packages are the same files. Unless it bundles a
    # dependency repository into a raspbian suite: that suite then carries the
    # dependency's ARMv6 build (docs/packaging.md, "Suites"), and our packages
    # are the ones built for the Debian suite of the same codename.
    copied = {}
    if raspbian and all_arch:
        bundled = bundled_suites(declaration, raspbian)
        unbundled = [s for s in raspbian if s not in bundled]
        if unbundled:
            raise Error(f"{' '.join(unbundled)}: a repository whose packages are all Architecture: all "
                        "publishes only the Debian suites, which Raspbian hosts use, unless it bundles "
                        "a dependency repository into the Raspbian suite")
        missing = [s for s in raspbian if s.removeprefix("raspbian-") not in suites]
        if missing:
            raise Error(f"{' '.join(missing)}: an Architecture: all repository's Raspbian suite carries the "
                        f"packages built for the Debian suite of its codename, so that suite is needed too")
        copied = {s.removeprefix("raspbian-"): s for s in raspbian}
    elif raspbian and "armhf" not in archs:
        raise Error(f"{' '.join(raspbian)}: the Raspbian suites are armhf only, and the "
                    "architectures leave armhf out")

    build, install = [], []
    for s in suites:
        if s in copied.values():
            # Built with its Debian suite (below); installed in the Raspbian root.
            install.append({"suite": s, "arch": "armhf", "runner": RUNNER["armhf"]})
            continue
        if s in RASPBIAN:
            built = ["armhf"]
        else:
            built = [a for a in archs if not (a == "riscv64" and s in NO_RISCV64)]
        if not built:
            continue
        first = next(a for a in PREFERENCE + ["all"] if a in built)
        for a in built:
            job = {"suite": s, "arch": a, "runner": RUNNER[a],
                   "arch-all": "true" if a == first else "false"}
            if s in copied:
                job["also"] = copied[s]   # uploaded again as debs-<also>-<arch>
            build.append(job)
        install.append({"suite": s, "arch": first, "runner": RUNNER[first]})
    if not build:
        raise Error(f"nothing to build: no architecture of {' '.join(archs)} is built for "
                    f"{' '.join(suites)}")

    built = {j["arch"] for j in build}
    advertised = [a for a in DEFAULT_ARCH if a in built]
    if has_all or all_arch:
        advertised.append("all")
    return {"build": build, "install": install, "suites": [s for s in suites if any(
        j["suite"] == s or j.get("also") == s for j in build)], "architectures": " ".join(advertised)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source-dir", type=Path, required=True)
    ap.add_argument("--suites", default="declared")
    ap.add_argument("--architectures", default="declared")
    ap.add_argument("--github-output", type=Path, help="append the outputs here (default: stdout)")
    args = ap.parse_args()
    try:
        declared = args.source_dir / ".github/apt-packaging.toml"
        declaration = tomllib.loads(declared.read_text()) if declared.exists() else {}
        control = args.source_dir / "debian/control"
        if not control.exists():
            raise Error(f"{control} does not exist")
        p = plan(declaration, control_architectures(control.read_text()),
                 args.suites.strip() or "declared", args.architectures.strip() or "declared")
    except (Error, tomllib.TOMLDecodeError) as e:
        print(f"build-matrix.py: error: {e}", file=sys.stderr)
        print(f"::error::{e}")
        return 1
    lines = [f"build={json.dumps(p['build'])}", f"install={json.dumps(p['install'])}",
             f"suites={' '.join(p['suites'])}", f"architectures={p['architectures']}"]
    for line in lines:
        print(line, file=sys.stderr)
    text = "\n".join(lines) + "\n"
    if args.github_output:
        with args.github_output.open("a") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
