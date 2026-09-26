#!/usr/bin/env python3
"""Make a throwaway Set B source tree for the build-deb self-test.

It gets a placeholder changelog, a v0.3 tag and one commit after it, so the
shared version script gives 0.3.post1 plus the suffixes. --legacy also commits
a packaging/deb-version.py of the kind repositories carried before the shared
one, which stamps 9.9 (build-deb's "auto" must still run it). --no-changelog
commits no debian/changelog and ignores it, as Set B does (docs/packaging.md,
"The changelog"), so the build's entry is the only one.

Usage: tests/make-source-fixture.py <dir> [--legacy | --no-changelog]
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

LEGACY = """\
import argparse, pathlib, re
ap = argparse.ArgumentParser()
ap.add_argument("--write-changelog", action="store_true")
ap.parse_args()
p = pathlib.Path("debian/changelog")
p.write_text(re.sub(r"\\(0\\.0\\)", "(9.9)", p.read_text(), count=1))
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--legacy", action="store_true")
    # The legacy script edits the committed placeholder, so it needs one.
    how.add_argument("--no-changelog", action="store_true")
    args = ap.parse_args()
    files = dict(FILES)
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

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")
    git("tag", "-a", "v0.3", "-m", "v0.3")
    git("commit", "-q", "--allow-empty", "-m", "one after the tag")


if __name__ == "__main__":
    main()
