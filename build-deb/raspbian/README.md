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
rsa2048 keys anyway. Nothing else changes, and only inside these build
roots. A SHA-1 collision attack doesn't help against the binding signature,
which already exists: forging a replacement needs a second preimage. The
bookworm root has apt 2, which verifies with `gpgv`, so the hook does
nothing there.

Checked with sqv 1.3.0 on 2026-09-27 against the `InRelease` of trixie (apt
3.0.3) and forky (apt 3.3.3, same policy date): exit 1
under the default policy, exit 0 under the override.
