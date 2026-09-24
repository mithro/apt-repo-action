#!/usr/bin/env python3
"""Copy paths of a repository's previous layout from the live site into a deploy.

A GitHub Pages deploy replaces the whole site, so moving a repository onto the
convention in docs/conventions.md (new suite directories, new key filename, new
Origin) would break every client still configured the old way. Each path
listed here is fetched from the live site and written, byte for byte, into the
tree about to be deployed, so old clients keep resolving until their
configuration moves. The copy is frozen: it keeps its old signature and Origin,
which is exactly what those clients' configuration expects.

A path naming a directory that holds a Release file ("." for a repository at
the site root, "dists/bookworm" for a classic archive) is copied with every
index file its Release lists and every .deb those indices reference. Any other
path is copied as a single file. "old=new" serves the live site's file "new"
under the old path "old", for a file the previous layout kept somewhere else
(netplan's key under debian/ and raspbian/ is its root key). Every index file
is checked against the SHA256 its Release promises, so a half-written copy is
never published.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import pathlib
import posixpath
import re
import sys
import urllib.error
import urllib.request

SIGNATURE_FILES = ("InRelease", "Release", "Release.gpg")


def fetch(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": "apt-repo-action carry-over"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def sha256_section(release: str) -> dict[str, tuple[str, int]]:
    """{path: (sha256, size)} from a Release file's SHA256 section."""
    files: dict[str, tuple[str, int]] = {}
    in_section = False
    for line in release.splitlines():
        if not line.startswith(" "):
            in_section = line.startswith("SHA256:")
            continue
        if in_section:
            digest, size, name = line.split()
            files[name] = (digest, int(size))
    return files


class Copier:
    def __init__(self, site: str, dest: pathlib.Path) -> None:
        self.site = site.rstrip("/")
        self.dest = dest
        self.written = 0

    def put(self, rel: str, data: bytes) -> None:
        rel = posixpath.normpath(rel)
        if rel.startswith("..") or rel.startswith("/"):
            raise SystemExit(f"refusing to write outside the deploy: {rel}")
        out = self.dest / rel
        if out.exists():
            if out.read_bytes() == data:
                return
            raise SystemExit(f"{rel}: the new layout already publishes a different file here")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        self.written += 1

    def get(self, rel: str) -> bytes | None:
        return fetch(f"{self.site}/{posixpath.normpath(rel)}")

    def repository(self, path: str, release: bytes) -> None:
        # A classic archive's Filename: fields are relative to the archive
        # root, two levels above dists/<suite>; a flat repository's are
        # relative to the directory itself.
        root = posixpath.normpath(posixpath.join(path, "..", "..")) if \
            re.match(r"^(.*/)?dists/[^/]+$", path) else path
        for name in SIGNATURE_FILES:
            data = release if name == "Release" else self.get(f"{path}/{name}")
            if data is not None:
                self.put(f"{path}/{name}", data)
        debs: dict[str, int] = {}
        for name, (digest, size) in sha256_section(release.decode()).items():
            if name in SIGNATURE_FILES:
                # apt-ftparchive run inside a flat repository lists the
                # half-written Release itself; that entry means nothing.
                continue
            data = self.get(f"{path}/{name}")
            if data is None:
                continue  # apt tolerates absent compression variants
            if hashlib.sha256(data).hexdigest() != digest or len(data) != size:
                raise SystemExit(f"{path}/{name}: does not match its Release (site mid-deploy?)")
            self.put(f"{path}/{name}", data)
            base = posixpath.basename(name)
            if base == "Packages.gz":
                data = gzip.decompress(data)
            elif base != "Packages":
                continue
            for stanza in data.decode().split("\n\n"):
                m = re.search(r"^Filename: (.+)$", stanza, re.M)
                s = re.search(r"^Size: (\d+)$", stanza, re.M)
                if m:
                    debs[posixpath.join(root, m.group(1).strip())] = int(s.group(1)) if s else -1
        for rel, size in sorted(debs.items()):
            data = self.get(rel)
            if data is None or (size >= 0 and len(data) != size):
                raise SystemExit(f"{rel}: missing or wrong size on the live site")
            self.put(rel, data)
        print(f"carried over repository {path}/ with {len(debs)} packages")

    def path(self, path: str) -> None:
        if "=" in path:
            dest, src = (posixpath.normpath(p.strip("/")) for p in path.split("=", 1))
            data = self.get(src)
            if data is None:
                raise SystemExit(f"{src}: not on the live site {self.site}")
            self.put(dest, data)
            print(f"carried over file {src} as {dest}")
            return
        path = posixpath.normpath(path.strip("/")) or "."
        release = self.get(f"{path}/Release")
        if release is not None:
            self.repository(path, release)
            return
        data = self.get(path)
        if data is None:
            raise SystemExit(f"{path}: not on the live site {self.site}")
        self.put(path, data)
        print(f"carried over file {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--site", required=True, help="live site URL")
    ap.add_argument("--dest", required=True, help="tree about to be deployed")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    c = Copier(args.site, pathlib.Path(args.dest))
    for p in args.paths:
        c.path(p)
    print(f"{c.written} files carried over", file=sys.stderr)


if __name__ == "__main__":
    main()
