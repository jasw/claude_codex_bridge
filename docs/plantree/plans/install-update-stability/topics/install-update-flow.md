# Install Update Flow

Date: 2026-06-04

## Fresh Install

Fresh install is owned by `install.sh install`.

Required flow:

1. Detect language with `CCB_LANG`, `LANG`, `LC_ALL`, or `LC_MESSAGES`.
2. Confirm root/sudo profile if effective uid is root.
3. Refuse temporary-prefix installs that would write wrappers into an external
   bin directory, unless explicitly overridden.
4. Check WSL compatibility and backend environment.
5. Select Python 3.10+.
6. Install required or optional Python packages only when missing.
7. Check terminal backend requirements.
8. Prepare install tree:
   - source/dev mode uses a live source root
   - release mode copies release content to the install prefix
9. Create managed venv when policy says to use one.
10. Write install metadata for release installs.
11. Install wrappers and bin links.
12. Run real installed entrypoint smoke checks.
13. Install inherited skills, settings, tmux helpers, and other static assets.
14. Provision optional Role Packs and tools.
15. Print install identity and next actions.

Core install success stops at step 13. Role Pack and tool provisioning are
post-install checks unless the user explicitly forces them as required.

## Managed Update

Managed update is routed by Python `ccb update`, but mutation authority depends
on install provenance.

For an npm install, the outer `@seemseam/ccb` package owns the vendored release:

1. The npm runner passes the package name, root, and manifest version to the
   Python child on every invocation.
2. Python accepts npm provenance only when the outer `package.json` matches and
   the executing release is below that package's `.ccb-release` directory.
3. `ccb update` prints `npm install -g @seemseam/ccb@<target>` and does not
   download, extract, install, or relaunch a vendored payload.
4. Startup update acceptance prints the same command and defers the current
   prompt window without reporting a successful in-place update.
5. The npm runner continues requiring exact equality between the manifest
   version and vendored `VERSION`; equality is safe because only npm mutates
   that payload.

For release-package and source/dev installs, the transactional tarball path is:

Required flow:

1. Resolve supported platform and target version.
2. Download the matching release artifact.
3. Extract to a temporary staging root.
4. Run the staged release `install.sh install` with optional provisioning
   disabled:
   - `CCB_INSTALL_ROLES=0`
   - `CCB_INSTALL_NEOVIM=0`
5. Verify the newly installed entrypoint and read new build metadata.
6. Print update outcome.
7. Run post-update provisioning through the newly installed `ccb`, not through
   the old updater process.
8. Report optional provisioning warnings without making the core update look
   failed unless the user forced required provisioning.

The key boundary is step 7. Once release files are installed, Role Pack and
tool semantics belong to the new release. The old updater process should only
bootstrap the new post-update runner.

## Post-Update Runner

The post-update runner should be a CLI entrypoint in the newly installed CCB,
for example an internal command such as:

```text
ccb __post-update --from-version <old> --to-version <new>
```

Responsibilities:

- Refresh or locate the `agent-roles-spec` catalog.
- Canonicalize legacy Role Pack ids before status comparison.
- Refresh installed Role Packs only when source version or digest changed.
- Skip Role Pack updates when status is already `current`.
- Install newly available Role Packs only after interactive confirmation.
- Provision Neovim only when requested or accepted.
- Emit bilingual summary and remediation messages.

## Failure Classification

Failures must be separated:

- blocking core update failure:
  download failed, extraction unsafe, installer failed, installed entrypoint
  smoke check failed
- non-blocking optional provisioning warning:
  catalog unavailable, Role Pack tool install failed in optional mode, Neovim
  unavailable in optional mode, Droid registration failed
- required provisioning failure:
  user set a force/required env var and the dependency failed

User output should make this distinction explicit. A successful core update
with optional Role Pack warning should say the update completed and then show
the optional warning plus retry command.

## Current Known Drift

The v7.2.9 incident showed that old updater code can continue after installing
new files and try to update a legacy `ccb.archi` source that no longer exists.
Moving post-update provisioning into the new installed entrypoint prevents this
class of old-code/new-layout mismatch.

Issue 268 exposed a separate ownership collision: in-place tarball update of an
npm-vendored release changed only its inner `VERSION`; the next npm invocation
then correctly restored the manifest-pinned payload. Package provenance and
npm delegation remove that competing writer rather than weakening the runner's
version check.

The 2026-06-15 stable-entrypoint audit found a separate drift class: a
temporary release simulation prefix under `/tmp/ccb-v7.2.1-install-smoke` was
left as the user's bare `ccb` authority. The real `~/.local/bin/ccb` symlink
pointed into that temporary prefix, and multiple live project daemons were
running from the same prefix. Install/update validation must therefore prove
that isolated `CODEX_INSTALL_PREFIX` and `CODEX_BIN_DIR` runs cannot mutate the
real user wrapper or persistent shell startup files unless that real install is
the explicit target.

Closed for direct shell installs on 2026-06-15: `install.sh install` now fails
before preparing the install tree when `CODEX_INSTALL_PREFIX` is temporary and
`CODEX_BIN_DIR` is outside the same temporary prefix or temporary HOME. Use
`CCB_ALLOW_TEMP_INSTALL_GLOBAL_BIN=1` only when intentionally writing from a
temporary install prefix into an external bin directory.
