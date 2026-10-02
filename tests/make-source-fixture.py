#!/usr/bin/env python3
"""Make a throwaway Set B source tree for the build-deb self-test.

It gets a placeholder changelog, a v0.3 tag and one commit after it, so the
shared version script gives 0.3.post1 plus the suffixes. --legacy also commits
a packaging/deb-version.py of the kind repositories carried before the shared
one, which stamps 9.9 (build-deb's "auto" must still run it). --no-changelog
commits no debian/changelog and ignores it, as Set B does (docs/packaging.md,
"The changelog"), so the build's entry is the only one.

--depends <url> --depends-key <url> makes the package build-depend on
apt-repo-selftest-dep, which Debian doesn't have, and declares the flat
repository at <url>/<suite>/ that has it as a `[[depends]]` in
.github/apt-packaging.toml (docs/packaging.md, "The declaration").
--declare-only leaves out the Build-Depends, so a build that fails can only
have failed on the repository itself (a wrong key, no Release). --bundle
also makes the package depend on apt-repo-selftest-dep at run time and
declares the repository with bundle = "third-party" (docs/packaging.md,
"Bundling a dependency repository").

--patch-series <upstream-dir> also makes a patch series' fetched project: its
own git repository at <upstream-dir>, tagged v1.1.1 two commits back, with the
debian/ files written into it uncommitted, as a patch series renders them
(docs/packaging.md, "Versions"). Built there with <dir> as the version tree
and --owner-tag selftest, the version is 1.1.1.post2+selftest.0.3.post1.

--set-a makes a Set A repository instead (docs/packaging.md, "Set A"): an
`upstream` branch with upstream's history, tagged v2.93 one commit back, and
`packaging` (checked out), which is that plus the fixture and one more commit
of ours. With --owner-tag selftest --upstream-branch upstream the version is
2.93+git1.g<sha7>-0+selftest2, <sha7> being upstream's tip. With
--release-subject upstream has no tag, as when its release tags aren't on
the branch it is built from: the release is its "Release 2.93 RELEASE_2_93"
commit, which the declaration's `[version] release-subject` names.

--any adds an architecture-dependent package from tests/fixtures/hello/,
apt-repo-selftest-hello: a C command that prints its version for `apt-repo-selftest-hello --version` and,
for `--cpu-arch`, the ARM architecture it was compiled for (__ARM_ARCH), so
a test can tell a Raspbian ARMv6 build from a Debian ARMv7 one.

Usage: tests/make-source-fixture.py <dir> [--legacy | --no-changelog | --patch-series <dir> |
                                          --set-a [--release-subject]]
           [--depends <url> --depends-key <url> [--declare-only | --bundle]] [--any]
"""
import argparse
import os
import subprocess
from pathlib import Path

FILES = {
    "debian/control": """\
Source: apt-repo-selftest-src
Section: misc
Priority: optional
Maintainer: apt-repo-action self-test <selftest@invalid>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.7.0

Package: apt-repo-selftest-src
Architecture: all
Description: apt-repo-action build-deb self-test fixture
 Built by the self-test to prove build-deb stamps the right version.
""",
    "debian/changelog": """\
apt-repo-selftest-src (0.0) unstable; urgency=medium

  * Placeholder changelog.

 -- apt-repo-action self-test <selftest@invalid>  Thu, 01 Jan 1970 00:00:00 +0000
""",
    "debian/rules": "#!/usr/bin/make -f\n%:\n\tdh $@\n",
    "debian/source/format": "3.0 (native)\n",
}

# --any: the architecture-dependent source tree, which the reusable
# build-deb.yml's self-test also builds from this repository as it is.
HELLO = Path(__file__).parent / "fixtures" / "hello"
HELLO_FILES = ["debian/control", "debian/rules", "hello.c", "Makefile"]

LEGACY = """\
import argparse, pathlib, re
ap = argparse.ArgumentParser()
ap.add_argument("--write-changelog", action="store_true")
ap.parse_args()
p = pathlib.Path("debian/changelog")
p.write_text(re.sub(r"\\(0\\.0\\)", "(9.9)", p.read_text(), count=1))
"""


DEPENDS = """\
kind = "B"
architectures = "all"

[[depends]]
name = "apt-repo-selftest-dep"
url = "{url}/{{suite}}/"
suite = "./"
key = "{key}"
reason = "apt-repo-selftest-dep is not in Debian"
"""


SET_A = """\
kind = "A"
upstream = "https://example.org/upstream"
architectures = "all"

[version]
release-subject = '^Release (\\d+(?:\\.\\d+)+) RELEASE_\\d+(?:_\\d+)+$'
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--legacy", action="store_true")
    # The legacy script edits the committed placeholder, so it needs one.
    how.add_argument("--no-changelog", action="store_true")
    how.add_argument("--patch-series", metavar="UPSTREAM_DIR", type=Path,
                     help="also make a fetched upstream tree there, holding the debian/ files")
    how.add_argument("--set-a", action="store_true",
                     help="a Set A repository: an upstream branch, and packaging on top of it")
    ap.add_argument("--release-subject", action="store_true",
                    help="with --set-a: no upstream tag; the declaration names the release commit")
    ap.add_argument("--depends", metavar="URL", help="the dependency repository's site")
    ap.add_argument("--depends-key", metavar="URL", help="its key")
    ap.add_argument("--declare-only", action="store_true", help="declare it, but don't build-depend on it")
    ap.add_argument("--bundle", action="store_true", help="depend on it at run time too, and bundle it")
    ap.add_argument("--any", action="store_true", help="add an architecture-dependent package")
    ap.add_argument("--build-depends", metavar="RELATIONS",
                    help="more Build-Depends, e.g. texinfo (a Raspbian staging fallback case)")
    ap.add_argument("--runtime-depends", metavar="RELATIONS",
                    help="a Depends for the Architecture: all package, e.g. perl (>= 5.42.3)")
    args = ap.parse_args()
    if bool(args.depends) != bool(args.depends_key):
        ap.error("--depends and --depends-key go together")
    if args.release_subject and not args.set_a:
        ap.error("--release-subject goes with --set-a")
    files = dict(FILES)
    if args.release_subject:
        files[".github/apt-packaging.toml"] = SET_A
    if args.depends:
        if not args.declare_only:
            files["debian/control"] = files["debian/control"].replace(
                "Build-Depends: debhelper-compat (= 13)",
                "Build-Depends: debhelper-compat (= 13), apt-repo-selftest-dep")
        files[".github/apt-packaging.toml"] = DEPENDS.format(url=args.depends, key=args.depends_key)
        if args.bundle:
            files[".github/apt-packaging.toml"] = files[".github/apt-packaging.toml"].replace(
                'reason = "apt-repo-selftest-dep', 'bundle = "third-party"\nreason = "apt-repo-selftest-dep')
            files["debian/control"] = files["debian/control"].replace(
                "Package: apt-repo-selftest-src\nArchitecture: all\n",
                "Package: apt-repo-selftest-src\nArchitecture: all\nDepends: apt-repo-selftest-dep\n")
    if args.any:
        files.update({name: (HELLO / name).read_text() for name in HELLO_FILES})
    # After --any, whose debian/control replaces the default one.
    if args.build_depends:
        files["debian/control"] = files["debian/control"].replace(
            "Build-Depends: debhelper-compat (= 13)",
            f"Build-Depends: debhelper-compat (= 13), {args.build_depends}", 1)
    if args.runtime_depends:
        control = files["debian/control"]
        head = "Package: apt-repo-selftest-src\nArchitecture: all\n"
        assert head in control, "the fixture's control has no apt-repo-selftest-src stanza"
        files["debian/control"] = control.replace(head, f"{head}Depends: {args.runtime_depends}\n", 1)
    if args.legacy:
        files["packaging/deb-version.py"] = LEGACY
    if args.no_changelog:
        del files["debian/changelog"]
        files[".gitignore"] = "debian/changelog\n"
    for name, text in files.items():
        path = args.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (args.dir / "debian/rules").chmod(0o755)
    env = {**os.environ, "GIT_AUTHOR_NAME": "self-test", "GIT_AUTHOR_EMAIL": "selftest@invalid",
           "GIT_COMMITTER_NAME": "self-test", "GIT_COMMITTER_EMAIL": "selftest@invalid"}

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(args.dir), *a], check=True, env=env)

    if args.set_a:
        git("init", "-q", "-b", "upstream")
        git("commit", "-q", "--allow-empty", "-m", "Release 2.93 RELEASE_2_93")
        if not args.release_subject:
            git("tag", "-a", "v2.93", "-m", "v2.93")
        git("commit", "-q", "--allow-empty", "-m", "upstream, one after")
        git("checkout", "-q", "-b", "packaging")
        git("add", "-A")
        git("commit", "-q", "-m", "fixture")
        git("commit", "-q", "--allow-empty", "-m", "one more of ours")
        return
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    git("tag", "-a", "v0.3", "-m", "v0.3")
    git("commit", "-q", "--allow-empty", "-m", "one after the tag")

    if args.patch_series:
        up = args.patch_series

        def upgit(*a: str) -> None:
            subprocess.run(["git", "-C", str(up), *a], check=True, env=env)

        up.mkdir(parents=True)
        upgit("init", "-q", "-b", "master")
        upgit("commit", "-q", "--allow-empty", "-m", "upstream's release")
        upgit("tag", "-a", "v1.1.1", "-m", "v1.1.1")
        upgit("commit", "-q", "--allow-empty", "-m", "upstream, one after")
        upgit("commit", "-q", "--allow-empty", "-m", "upstream, two after")
        # Rendered, not committed: the build writes the whole changelog.
        for name, text in files.items():
            if name.startswith("debian/") and name != "debian/changelog":
                (up / name).parent.mkdir(parents=True, exist_ok=True)
                (up / name).write_text(text)
        (up / "debian/rules").chmod(0o755)


if __name__ == "__main__":
    main()
