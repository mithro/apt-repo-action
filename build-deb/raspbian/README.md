# The Raspbian archive key

`raspbian.public.key` is the key archive.raspbian.org signs its `InRelease`
files with. `build-deb` bootstraps the `raspbian-<codename>` build root with
it, and refuses to start unless the file's fingerprint is the one pinned in
`build-deb/action.yml`:

```
pub   rsa2048 2012-04-01 [SC]
      A0DA 38D0 D76E 8B5D 6388  7281 9165 938D 90FD DD2E
uid   Mike Thompson (Raspberry Pi Debian armhf ARMv6+VFP) <mpthompson@gmail.com>
```

Fetched on 2026-09-27 from <https://archive.raspbian.org/raspbian.public.key>
(sha256 `ca59cd4f2bcbc3a1d41ba6815a02a8dc5c175467a59bd87edeac458f4a5345de`),
and checked two ways before it was committed:

- `gpgv` with it verifies the `InRelease` of bookworm, trixie and forky on
  archive.raspbian.org;
- keyserver.ubuntu.com serves the same fingerprint, and it is the fingerprint
  other projects building Raspbian images pin (for example
  guysoft/CustomPiOS and ali1234/rpi-ramdisk).

The key has no expiry. If Raspbian ever changes it, replace the file and the
fingerprint in `build-deb/action.yml` in the same commit, after the same checks.

## apt 3 and the key's SHA-1 binding

The key's self-signatures, which bind its signing key to it, are SHA-1 (and
were never re-made with SHA-2). apt 3, from trixie on, verifies with
Sequoia's `sqv`, and its default policy
(`/usr/share/apt/default-sequoia.config`) stops trusting SHA-1 for
second-preimage resistance on 2026-02-01. So a stock Raspbian trixie or forky
apt refuses Raspbian's own archive:

```
Signing key on A0DA38D0D76E8B5D638872819165938D90FDDD2E is not bound:
           No binding signature at time 2026-09-26T22:21:38Z
  because: Policy rejected non-revocation signature (PositiveCertification) requiring second pre-image resistance
  because: SHA1 is not considered secure since 2026-02-01T00:00:00Z
```

`customize.sh` does what that file says to do: it copies it to
`/etc/crypto-policies/back-ends/apt-sequoia.config` (which replaces it) with
that one date moved to 2030-02-01, the date the same policy stops trusting
rsa2048 keys anyway. Nothing else in the file changes. The bookworm root has
apt 2, which verifies with `gpgv`, so the hook does nothing there.

### What the override covers

**It can't be limited to Raspbian's key.** apt 3.0.3 has no per-source or
per-key signature policy: the sqv method reads one policy for every source,
from `APT_SEQUOIA_CRYPTO_POLICY` or the first of
`/etc/crypto-policies/back-ends/apt-sequoia.config`,
`/var/lib/crypto-config/profiles/current/apt-sequoia.config`, … ,
`/usr/share/apt/default-sequoia.config` (the paths in
`/usr/lib/apt/methods/sqv`). None of the options in sources.list(5)
(`Signed-By`, `Trusted`, `Allow-Insecure`, `Allow-Weak`, …) sets one.
`Allow-Weak` doesn't help: `apt-get update` on Raspbian's trixie with
`[signed-by=… allow-weak=yes]` still fails, with sqv's same error. So in a
Raspbian root the override applies to every source apt verifies there:
Raspbian's archive, and any [dependency
repository](../../docs/packaging.md#dependency-repositories) declared for a
`raspbian-<codename>` suite.

**What it widens, for those sources, is one thing: a signing key whose
binding self-signatures are SHA-1 is usable again.** Sequoia only applies
`second_preimage_resistance` to signatures an attacker can't have
influenced. In the words of
[`sequoia_openpgp::policy::HashAlgoSecurity`](https://docs.rs/sequoia-openpgp/latest/sequoia_openpgp/policy/enum.HashAlgoSecurity.html):
"many self signatures only require second pre-image resistance … we need
collision resistance when a signature is over data that could have been
influenced by an attacker". A repository's signature over its `InRelease`
needs collision resistance, and SHA-1's is refused from 2013-02-01 in
Sequoia's own policy, which the override doesn't touch. Checked with
sqv 1.3.0 on 2026-09-27, with throwaway RSA-3072 keys, apt's default policy
and the override:

| key's self-signatures | `InRelease` signed with | default | override |
|---|---|---|---|
| SHA-512 | SHA-512 | accepted | accepted |
| SHA-512 | SHA-1 | refused (2013-02-01) | refused (2013-02-01) |
| SHA-1 | SHA-512 | refused (2026-02-01) | **accepted** |
| SHA-1 | SHA-1 | refused (2026-02-01) | refused (2013-02-01) |

Only the third row changes, and it is Raspbian's case. Abusing it needs a
new binding signature on the key that matches an existing SHA-1 one: a
second preimage, which nobody has shown for SHA-1 (the published attacks
are collisions). And that key still has to be the one the source's
`signed-by` names.

**Where the override is, and isn't:**
- in the Raspbian root image: the build container, and the install test's,
  which has to be the same root to install from Raspbian's archive;
- in the week's cache of that root, in the calling repository's Actions
  cache. The image is imported into the job's docker, never pushed;
- not in any built package. The build packages `debian/<package>/` only,
  and the self-test checks that no built `.deb` contains
  `etc/crypto-policies`;
- not in `debian:<suite>` builds or their install tests. The self-test
  checks that the Debian armhf install test's container has no override,
  and that the Raspbian root's differs from apt's default in that one line
  only.

Checked with sqv 1.3.0 on 2026-09-27 against the `InRelease` of trixie (apt
3.0.3) and forky (apt 3.3.3, same policy date): exit 1
under the default policy, exit 0 under the override.

## ARMv6 memory barriers on an arm64 runner

Raspbian is built for ARMv6, which has no `dmb` instruction, so its code
issues memory barriers as the CP15 operation `mcr p15, 0, rX, c7, c10, 5`.
build-deb runs armhf natively on the `ubuntu-24.04-arm` runners, and their
arm64 kernel traps and emulates those instructions by default
(`abi.cp15_barrier = 1`). Under that emulation Raspbian trixie's rustc
(1.85.0+dfsg3-1+rpi1) and forky's never finish: on 2026-09-29 a hello world
spun in rustc's codegen coordinator for as long as it was left, while the
same binaries took 2 s under QEMU and under 1 s with the barriers run by
the CPU (`abi.cp15_barrier = 2`; `abi.swp` made no difference). Debian's
armhf is ARMv7 and uses `dmb`, and Raspbian bookworm's rustc 1.63 doesn't
hit it.

So on an arm64 runner a Raspbian build (and an install test's preparation)
first sets `abi.cp15_barrier = 2`, which every ARMv8 CPU with AArch32
supports, and fails if it can't. It is the runner kernel's setting: GitHub's
runners are discarded after the job, and on a self-hosted runner it stays
set. Setting it needs passwordless sudo: a self-hosted arm64 runner without
it fails Raspbian builds with a message saying so, unless
`abi.cp15_barrier = 2` is set on it beforehand (for instance in
`/etc/sysctl.d/`). The self-test `build-deb-raspbian-rust` builds a small crate with cargo
in each Raspbian suite.
