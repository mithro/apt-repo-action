#!/usr/bin/env python3
"""Refuse to publish a keyring whose format does not match its extension.

apt decides how to read a keyring from its *filename*, not its contents:

  * ``<name>.gpg`` must be a **binary** OpenPGP keyring.
  * ``<name>.asc`` must be an **ASCII-armoured** public key block.

Get that backwards and gpgv cannot parse the file. apt then reports the
keyring as having "an unsupported filetype" and the repository as "not
signed", so nothing can be installed from it. Nothing in gpg stops you
writing the wrong one, so check it here, at publish time, where the failure
is loud and nobody has shipped it yet.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ARMOUR_HEADER = b"-----BEGIN PGP PUBLIC KEY BLOCK-----"

RULE = (
    "apt reads a keyring's format from its extension: .gpg must be a binary\n"
    "OpenPGP keyring and .asc must be ASCII-armoured. A mis-typed file makes\n"
    "apt report 'the file has an unsupported filetype' and then refuse the\n"
    "repository as 'not signed'."
)


def fingerprints(path: pathlib.Path) -> list[str]:
    """Fingerprints gpg can actually read out of the file, or [] if it cannot."""
    proc = subprocess.run(
        ["gpg", "--batch", "--no-options", "--with-colons", "--show-keys", str(path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line.split(":")[9] for line in proc.stdout.splitlines()
            if line.startswith("fpr:")]


def check(path: pathlib.Path, want_armoured: bool, problems: list[str]) -> list[str]:
    kind = "ASCII-armoured" if want_armoured else "binary"
    if not path.is_file():
        problems.append(f"{path}: missing; the {kind} keyring was never written")
        return []

    head = path.read_bytes()[:len(ARMOUR_HEADER)]
    is_armoured = head == ARMOUR_HEADER
    if is_armoured != want_armoured:
        found = "ASCII-armoured" if is_armoured else "binary"
        problems.append(
            f"{path}: named '{path.suffix or path.name}' so apt will read it as "
            f"{kind}, but its contents are {found} (first bytes: {head!r})"
        )

    fprs = fingerprints(path)
    if not fprs:
        problems.append(f"{path}: gpg --show-keys cannot read any key out of this file")
    return fprs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", required=True, type=pathlib.Path,
                    help="keyring that must hold a binary OpenPGP key")
    ap.add_argument("--armored", required=True, type=pathlib.Path,
                    help="keyring that must hold an ASCII-armoured key")
    args = ap.parse_args()

    problems: list[str] = []
    binary_fprs = check(args.binary, want_armoured=False, problems=problems)
    armored_fprs = check(args.armored, want_armoured=True, problems=problems)

    # Both files are meant to be the same key in two encodings. If they are
    # not, one of them is stale and consumers would trust the wrong thing.
    if binary_fprs and armored_fprs and binary_fprs != armored_fprs:
        problems.append(
            f"{args.binary} and {args.armored} hold different keys: "
            f"{binary_fprs} vs {armored_fprs}"
        )

    if problems:
        print("::error::published keyrings are not usable by apt", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\n" + RULE, file=sys.stderr)
        return 1

    print(f"{args.binary}: binary keyring, keys {binary_fprs}")
    print(f"{args.armored}: ASCII-armoured keyring, keys {armored_fprs}")
    print("both keyrings match their extension and are readable by gpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
