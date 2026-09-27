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
