#!/usr/bin/env python3
"""Write a landing page for a multi-suite flat APT repository.

Generated only when the caller has not supplied its own apt-index.html. Lists
every suite actually present, with copy-pasteable setup instructions and the
package list read back out of each suite's Packages file, so the page cannot
claim to ship something the repository does not contain.
"""

from __future__ import annotations

import argparse
import html
import pathlib
import re

TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{repo_name} apt repository</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 15px/1.6 system-ui, sans-serif; max-width: 52rem;
         margin: 2rem auto; padding: 0 1rem; }}
  code, pre {{ font-family: ui-monospace, Menlo, Consolas, monospace; }}
  pre {{ background: #8881; padding: .75rem 1rem; border-radius: 6px;
        overflow-x: auto; }}
  table {{ border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ text-align: left; padding: .3rem .9rem .3rem 0;
           border-bottom: 1px solid #8883; }}
  .muted {{ color: #8888; }}
</style>

<h1>{repo_name} apt repository</h1>
<p>Debian packages built from
   <a href="https://github.com/{repo}">{repo}</a>.
   Rolling release: every push to <code>main</code> publishes a new version.</p>

<h2>Packages</h2>
{packages}

<h2>Setup</h2>
{setup}

<p class="muted">Signed with the repository's own key
   (<a href="{keyring}">{keyring}</a>). Each suite is a separate flat
   repository, so the same version can ship for several Debian releases.</p>
"""

SETUP = """<h3>{suite}</h3>
<pre>sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL {base}/{keyring} \\
  | sudo tee /etc/apt/keyrings/{keyring} &gt; /dev/null
echo "deb [signed-by=/etc/apt/keyrings/{keyring}] {base}/{suite}/ ./" \\
  | sudo tee /etc/apt/sources.list.d/{stem}.list
sudo apt update</pre>
"""


def packages_in(suite_dir: pathlib.Path) -> list[tuple[str, str, str]]:
    """Read (package, version, architecture) out of a suite's Packages file."""
    packages_file = suite_dir / "Packages"
    if not packages_file.is_file():
        return []
    found: dict[tuple[str, str], tuple[str, str, str]] = {}
    for stanza in packages_file.read_text().split("\n\n"):
        def field(name: str) -> str:
            m = re.search(rf"^{name}: (.+)$", stanza, re.MULTILINE)
            return m.group(1).strip() if m else ""

        name, version, arch = field("Package"), field("Version"), field("Architecture")
        if not name:
            continue
        # Several versions accumulate; show the newest by dpkg ordering, which
        # for the git-describe scheme is plain string order on the .postN tail.
        key = (name, arch)
        if key not in found or version > found[key][1]:
            found[key] = (name, version, arch)
    return sorted(found.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apt-root", required=True)
    ap.add_argument("--suites", required=True)
    ap.add_argument("--keyring-name", required=True)
    ap.add_argument("--repo", required=True, help="owner/name")
    args = ap.parse_args()

    root = pathlib.Path(args.apt_root)
    repo_name = args.repo.split("/")[-1]
    base = f"https://{args.repo.split('/')[0]}.github.io/{repo_name}"

    rows = []
    for suite in args.suites.split():
        for name, version, arch in packages_in(root / suite):
            rows.append(
                f"<tr><td><code>{html.escape(name)}</code></td>"
                f"<td>{html.escape(version)}</td>"
                f"<td>{html.escape(arch)}</td>"
                f"<td>{html.escape(suite)}</td></tr>"
            )
    packages = (
        "<table><tr><th>Package<th>Version<th>Arch<th>Suite</tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p class='muted'>No packages indexed.</p>"
    )

    setup = "".join(
        SETUP.format(suite=suite, base=base, keyring=args.keyring_name,
                     stem=pathlib.Path(args.keyring_name).stem)
        for suite in args.suites.split()
        if (root / suite).is_dir()
    )

    (root / "index.html").write_text(
        TEMPLATE.format(repo=args.repo, repo_name=repo_name, packages=packages,
                        setup=setup, keyring=args.keyring_name)
    )
    print(f"wrote {root / 'index.html'}")


if __name__ == "__main__":
    main()
