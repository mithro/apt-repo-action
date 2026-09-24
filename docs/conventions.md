# The apt repository convention

Every Debian package repository published from `github.com/mithro/*` or
`github.com/fpgas-online/*` follows this one layout, so that setting up any of
them is the same four lines with a different name in them. In what follows
`<repo>` is the GitHub repository name (`tmux`, `usdr-lib`,
`fpgas.online-fpga-tools`, ...) and `<site>` is that repository's GitHub Pages
URL as GitHub reports it (`https://mith.ro/tmux`, `https://fpgas.online/nfsroot-watchdog`,
`https://apt.fpgas.online`).

## One name

`<repo>` is the only name. It is used, unchanged, for:

| what | value |
|---|---|
| public key, binary | `<site>/<repo>.gpg` |
| public key, armoured | `<site>/<repo>.asc` |
| installed keyring | `/etc/apt/keyrings/<repo>.gpg` |
| sources file | `/etc/apt/sources.list.d/<repo>.list` (or `<repo>.sources`) |
| Release `Origin:` and `Label:` | `<repo>` |

No `mithro-` prefixes, no shortened stems (`netgear-switch`, `usdr`), no
`pubkey.gpg`, no `/usr/share/keyrings/`. An apt pin on the repository is
therefore always `Pin: release o=<repo>`.

## One layout

One flat repository per suite:

```
<site>/<repo>.gpg
<site>/<repo>.asc
<site>/index.html          generated, see below
<site>/<suite>/InRelease
<site>/<suite>/Release
<site>/<suite>/Release.gpg
<site>/<suite>/Packages{,.gz}
<site>/<suite>/*.deb
```

`<suite>` is the distribution codename the packages were built for:
`bookworm`, `trixie`, `forky`, `sid`. A build for a derivative whose archive
differs from Debian's for the same codename is named `<derivative>-<codename>`
(`raspbian-trixie`: Raspbian's armhf is ARMv6, Debian's is ARMv7).

Each suite's Release carries `Suite: stable` and `Codename: <suite>`. No
`dists/` or `pool/`, and no repository at the site root.

Each suite keeps earlier versions of its packages next to the new ones, all
indexed, for as long as the whole site stays under `size-limit-mb` (default
900 MB; GitHub Pages allows 1 GB). A deploy replaces the whole site, so
without this a client whose index is a moment old would fail mid-install on a
file the deploy just deleted, and nothing could go back a version
(`apt install <package>=<version>`). `publish-apt.yml` fetches them from the
live site (`scripts/keep-history.py`): every package's previous version
first, then the one before, dropping the oldest when space runs out. Only
versions lower than the new build's are kept, so publishing a lower version
on purpose still takes effect; packages the build stops producing are dropped.

## One setup

```sh
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL <site>/<repo>.gpg | sudo tee /etc/apt/keyrings/<repo>.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/<repo>.gpg] <site>/<suite>/ ./" \
  | sudo tee /etc/apt/sources.list.d/<repo>.list
sudo apt update
```

Nothing else: no `gpg --dearmor` (the `.gpg` file already is a binary
keyring), no `lsb_release` (not installed on a minimal Debian), no
`trusted=yes`.

## One signing setup

- Each repository has **its own** key; the private half is the repository
  secret `APT_GPG_PRIVATE_KEY`. New keys are RSA 4096, no expiry, user ID
  `<repo> apt repository <me@mith.ro>`:

  ```sh
  gpg --batch --passphrase '' --quick-gen-key \
    '<repo> apt repository <me@mith.ro>' rsa4096 sign never
  ```

- Indexing and signing happen only in the reusable workflow
  `mithro/apt-repo-action/.github/workflows/publish-apt.yml@main`. No
  repository runs its own `apt-ftparchive` / `gpg --clearsign`: that is how
  repositories ended up with armoured `.gpg` keyrings and unsigned
  Releases, one hand-rolled copy at a time.
- `publish-apt.yml` refuses to publish unsigned, and checks both published
  keyrings against the format their extension promises before deploying.

## One index page

`scripts/make-index.py` writes every `index.html` from the same template:
package table, the setup block above for each suite, the key fingerprint.
A repository with something to say about its packages puts an HTML fragment
in `packaging/apt-intro.html`; it is placed under the heading. A whole custom
`apt-index.html` is ignored.

## Moving a repository onto the convention

A repository whose layout, key filename or Origin changes would break every
client still configured the old way. `publish-apt.yml` bridges that with
`legacy-paths`: the listed paths are copied, byte for byte, from the live site
into each new deploy. Old clients keep resolving (to a frozen snapshot, with
its old signature and Origin, which is what their configuration expects) while
their configuration is updated; then the paths are dropped from the workflow.

```yaml
    with:
      suites: trixie
      legacy-paths: ". usdr.gpg usdr.asc"   # old root repo and old key names
```

A path naming a directory holding a `Release` is copied with its indices and
every `.deb` they reference; anything else is copied as a single file.
`old=new` serves the live file `new` under the old path `old`, for a file the
previous layout kept elsewhere (`debian/netplan.gpg=netplan.gpg`).
