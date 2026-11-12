# Bind-mounted external storage ("extra paths")

## The gap

`pull.sh` backs up exactly one tree per container: whatever lives on the
source host at `$CONTAINERS_ROOT/<name>/` (`/var/lib/machines/<name>/` -
see `dispatch.sh`'s `CONTAINERS_ROOT`). That is the container's own rootfs
*as seen from the host's own root mount namespace*.

A systemd-nspawn container can also be given external storage via `Bind=`
in its `.nspawn` unit, e.g.:

```ini
[Files]
Bind=/srv/fhd-shares/fhdcore-vilhelmina:/usr/butik/samba
```

This bind mount is only active **inside the container's own running mount
namespace**. It is not propagated back out to the host's root namespace,
so from the host's point of view, `/var/lib/machines/<name>/usr/butik/samba`
is just an empty directory (the mountpoint itself, with nothing mounted on
it) - whether or not the container is currently running. `pull.sh`'s rsync
reads that exact path, from the host side, and therefore copies an empty
directory. **No error, no warning** - the pull reports success, every
time, because as far as rsync is concerned it faithfully copied everything
that was there.

Confirmed live on `vilhelmina-csrv.vpn.fhd.se`, 2026-11-12: two containers
(`fhdcore-vilhelmina`, `samba-fhdcore-vilhelmina`) both bind
`/srv/fhd-shares/fhdcore-vilhelmina` to `/usr/butik/samba` (one with
`:idmap`, one without - same source directory shared between both). Every
pull for both containers had been "succeeding" for weeks while backing up
zero bytes of that directory. The customer noticed missing files under
`/usr/butik/samba/public` when checking the vault side; that is how this
was found - **not** by any alert, since nothing in the existing
success/staleness/ransomware checks could have detected it.

## Confirmed instances fixed so far

- `vilhelmina-csrv.vpn.fhd.se`: `fhdcore-vilhelmina` + `samba-fhdcore-vilhelmina`
  → `/srv/fhd-shares/fhdcore-vilhelmina`, label `fhdcore-vilhelmina-shares`.
  Found 2026-11-12 (the customer themselves noticed missing files).
- `fhdcore-jf.vpn.fhd.se`: `fhdcore-jf2` + `samba-fhdcore-jf2` →
  `/srv/fhd-shares/fhdcore-jf2`, label `fhdcore-jf2-shares`. Found
  2026-11-12 during a fleet-wide `Bind=` sweep prompted by the vilhelmina
  case - same exact pattern, same exact bug, also already "succeeding" on
  every pull.

This `<container>` / `samba-<container>` pair sharing one host directory
via `Bind=` (one side `:idmap`, one side not) is evidently a **standard
pattern** in this fleet's architecture, not a one-off - the Samba
container is kept separate from the main container for a deliberate
security reason (the main container can write into the share; the Samba
container must not be able to write back into the main container), not an
accident worth "fixing" by removing the bind mount. Any host using this
pattern needs the extra-path treatment below for its share directory.

A fleet sweep for the same `Bind=` pattern also turned up a few other
hosts where it's configured but currently moot, because the affected
containers aren't in that host's `containers` file at all yet - the
bind-mount gap only matters once a container is actually being backed up
in the first place. Whether and when those should be onboarded is a
separate, operator-side decision, not covered by this document.

## How to spot this when onboarding any new host

Before considering a new source host "done", check every container's
`.nspawn` unit for `Bind=` lines:

```bash
grep -i '^Bind=' /etc/systemd/nspawn/*.nspawn
```

For each one found, decide whether the bind target holds anything that
actually needs backing up (a log directory or a cache mount usually
doesn't; a Samba share, an upload directory, or anything users/customers
put data into usually does). If it does, it needs the "extra path"
treatment below - it will **not** be covered by the normal per-container
pull no matter what.

As a sanity check after any pull of a container with a `Bind=` mount worth
backing up, confirm the vault actually has non-empty content at that exact
path:

```bash
ls -la /vault/<host>/<container>/<bind-target-path>
```

An empty directory there (and nowhere else to explain it) is this exact
bug, not a one-off fluke.

## The fix: a dedicated key per extra path

Rather than widening `dispatch.sh`'s existing `rrsync -ro
"$CONTAINERS_ROOT/"` confinement (which would mean a single compromised
key being able to read *every* bind target on the host, not just the one
actually needed - a real increase in blast radius for every existing
container too), each extra path gets its **own**, narrowly-scoped SSH key
whose `authorized_keys` forced command is a bare, single-purpose:

```
restrict,command="rrsync -ro /srv/fhd-shares/fhdcore-vilhelmina" ssh-ed25519 AAAA... nspawn-vault-extra:<label>
```

That key can read *exactly* that one directory tree and nothing else - not
`dispatch.sh`, not any container's rootfs, not any other extra path on the
same host. This matches the project's existing least-privilege pattern
(one key, one forced command, no server-side flexibility) rather than
introducing a new one.

On the vault side, `pull-extra.sh` (new in `nspawn-vault` 0.1.0-24) pulls
one extra path using its own key - **never** going through
`dispatch.sh`, and therefore never sending `pre-snapshot`/`snapshot-db`/
`list-capabilities` commands over that key (a forced command always runs
instead of whatever the client asked for, so those calls would just
silently re-trigger the same `rrsync` line and confuse the rsync wire
protocol - don't add them). It otherwise mirrors `pull.sh`: ZFS snapshot
after every pull, the same `zfs diff`-based ransomware heuristic, the same
per-name ransomware-threshold override and pause-on-suspicion behavior.

### Install steps

As of `nspawn-vault-web` 0.1.0-39, this is a guided flow in the Admin UI:
open a host's row, click the "+" button next to its container list, enter
a label and the source host's absolute path. The backend generates the
key and shows the exact `authorized_keys` line to paste onto the source
host, plus a "test connection" button. No manual `ssh-keygen` or file
editing needed on the vault side any more - only the one paste onto the
source host, which the vault can never do itself (it never holds
credentials that could reach there).

The steps below are what that flow actually does under the hood (and
still the only path if you need to do this entirely by hand - e.g.
scripting it, or before 0.1.0-39) - what was done for
`fhdcore-vilhelmina-shares` and `fhdcore-jf2-shares` before the UI flow
existed:

On the **vault**:

```bash
mkdir -p /etc/nspawn-vault/<host>/extra
chmod 700 /etc/nspawn-vault/<host>/extra
ssh-keygen -t ed25519 -N "" \
    -f /etc/nspawn-vault/<host>/extra/<label>.key \
    -C "nspawn-vault-extra:<label>"
chmod 600 /etc/nspawn-vault/<host>/extra/<label>.key
```

The private key never leaves the vault - only the matching `.pub` content
goes to the source host.

On the **source host**, append one line to `/root/.ssh/authorized_keys`
(back up the file first):

```
restrict,command="rrsync -ro <absolute-path-to-bind-target>" <contents of <label>.key.pub>
```

Back on the **vault**, add `<label>` as a line in the host's normal
`/etc/nspawn-vault/<host>/containers` file, same as any real container -
this is what makes it show up in the dashboard, the staleness dead-man's
switch, and the ransomware-threshold UI exactly like any other entry.
`pull-host.sh` decides whether a given line is a real container or an
extra path purely by whether `<host>/extra/<name>.key` exists - nothing in
`containers` itself marks it as special, so leave a `#` comment above the
line explaining what it actually is, for whoever edits that file next.

Test once, by hand, before trusting the timer with it:

```bash
bash /usr/libexec/nspawn-vault/pull-extra.sh <host> <label>
ls -la /vault/<host short>/<label>/
```

## What this does NOT do

This is a targeted fix for the one case found, not a general solution.
It does **not**:

- Auto-detect `Bind=` lines in `.nspawn` units - onboarding a new host
  still requires a human to grep for `Bind=` and judge which targets need
  backing up (see above). A future, more thorough version of this feature
  could have `pre-snapshot.sh` or a new dispatch command report a
  container's own `Bind=` targets back to the vault automatically, so a
  new host's onboarding flags any bind target that has no matching
  `containers` entry - not built, since it would mean `dispatch.sh` itself
  parsing/trusting `.nspawn` file contents, a bigger change to the trust
  boundary than this fix.
- Give the extra path any of the container-specific pull steps
  (`pre-snapshot`, DB dump, capability restore) - it is a plain directory
  tree, not a container.
- Handle two containers sharing the *same* bind target automatically - if
  N containers all bind the same host directory (as seen here), back it up
  **once**, under whichever label makes sense, not once per container.

## Recommendation for future installations

The cleanest fix is architectural, not operational: **avoid `Bind=`-mounted
external storage for anything that must be backed up, when it's practical
to instead keep that data inside the container's own rootfs tree.** That
sidesteps this entire class of problem - a normal `pull.sh` run already
covers it correctly, with no extra key, no extra script, no extra manual
step per host.

This isn't always practical - `Bind=` mounts are often specifically there
*because* several containers need to share one tree (as with the Samba
share found here), which a copy inside one container's own rootfs
couldn't do. In that case, the "extra path" pattern above is the supported
way to back it up; just budget the extra onboarding step (grep for
`Bind=`, one key, one `containers` line) into every new host's setup
checklist from here on, rather than discovering it the way this one was
found.
