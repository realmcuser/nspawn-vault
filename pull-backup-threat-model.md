# Pull-baserad backup för cockpit-nspawn

Designförslag att lämna till Claude Code. Bygger vidare på den befintliga
push-modellen i `BackupDialog.jsx` / `RestoreDialog.jsx`, men inverterar
trust-pathen så att en komprometterad kund-server inte kan nå eller förstöra
backuparna.

---

## 1. Varför (hotmodell)

Befintlig push-modell:

```
[kund-server]  --(håller SSH-nyckel, rsync --delete + rm -rf)-->  [backup]
   ^ komprometteras                                                  ^ raderas med
```

Kund-servern är *gäst på kundens LAN* och kan komprometteras. Eftersom den håller
nyckeln och har skriv-/raderingsrätt till valvet, dör backuparna med produktionen.

Pull-modell:

```
[kund-server]  <--(VALVET initierar, läser read-only)--  [backup-valv (betrott)]
   ^ komprometteras                  inga creds            ^ håller alla nycklar
     håller INGA backup-creds          ut härifrån           tar ZFS-snapshots
     kan INTE nå valvet                                       prunar lokalt
```

Bärande principer:

1. **Valvet initierar.** Kund-servern har inga utgående backup-creds och ingen
   väg till valvet. Komprometterad host kan inte röra backuparna.
2. **Read-only på kund-sidan.** Även om *valvet* komprometteras kan det bara
   *läsa* kunddata via en forced-command-låst nyckel — aldrig skriva tillbaka.
3. **Immutabilitet på valvet.** ZFS-snapshots (read-only, oåtkomliga från källan)
   + offsite-replika. Retention/pruning sker enbart på den betrodda sidan.
4. **Dead-man's switch.** En krypterad/död host slutar bara producera färska
   pulls → valvet larmar. Mycket robustare än att varje host själv rapporterar fel.

Transport: allt går över Tailscale/Headscale, precis som idag.

---

## 2. Arkitektur

Ett **backup-valv** (kan vara centralt eller per region) på Tailnet. Per kund:

- En egen ed25519-nyckel på valvet (aldrig på kund-servern).
- En ZFS-dataset: `vault/<kund>/<container>` med snapshot efter varje lyckad pull.
- En offsite-tier (zfs send-replika *eller* restic append-only) för 3-2-1.

Kund-servern får bara två tillägg:
- En forced-command-låst `authorized_keys`-rad för backup-användaren.
- En liten dispatcher som tillåter exakt två operationer: `snapshot-db` och
  read-only rrsync av `/var/lib/machines`.

---

## 3. Kund-sidan (minimal, read-only)

### 3.1 Backup-användare + nyckel

Skapa en dedikerad användare (eller återanvänd root men lås nyckeln hårt —
forced command gör att rollen ändå bara kan läsa). `restrict` slår av agent/port/
X11-forwarding och pty.

`/home/nspawn-pull/.ssh/authorized_keys` (eller `root`s, låst):

```
restrict,command="/usr/local/lib/nspawn-pull/dispatch.sh" ssh-ed25519 AAAA...valvets-publika-nyckel... nspawn-pull@valv
```

### 3.2 Dispatcher (whitelist)

`/usr/local/lib/nspawn-pull/dispatch.sh` — släpper bara igenom två kommandon:

```bash
#!/bin/bash
set -euo pipefail
cmd="${SSH_ORIGINAL_COMMAND:-}"
ALLOWED_ROOT=/var/lib/machines

case "$cmd" in
    "snapshot-db "*)
        name="${cmd#snapshot-db }"
        # validera mot allowlist av kända containrar
        case "$name" in
            */*|*..*|"") echo "bad name" >&2; exit 1 ;;
        esac
        exec /usr/local/lib/nspawn-pull/snapshot-db.sh "$name"
        ;;
    "rsync --server --sender"*)
        # rrsync läser själv SSH_ORIGINAL_COMMAND och tvingar read-only
        exec rrsync -ro "$ALLOWED_ROOT"
        ;;
    *)
        echo "denied: $cmd" >&2
        exit 1
        ;;
esac
```

> `rrsync` ligger i `/usr/bin/rrsync` på AlmaLinux 9 (rsync-paketet). Faller den
> inte ut: `/usr/share/doc/rsync/support/rrsync`. `-ro` = read-only.

### 3.3 Applikationskonsistent DB-dump (lokalt, lösenordet stannar kvar)

`/usr/local/lib/nspawn-pull/snapshot-db.sh` — kör mysqldump *inuti* containern,
exakt som befintliga `mysqldumpBlock()`, men lösenordet bor i en lokal 600-fil
på kund-servern och skickas aldrig från valvet:

```bash
#!/bin/bash
set -euo pipefail
NAME="$1"
MYCNF_SRC="/etc/cockpit-nspawn/pull/${NAME}.cnf"   # [client]\npassword=...  (chmod 600)
[ -f "$MYCNF_SRC" ] || exit 0                       # ingen DB konfigurerad → no-op

if ! machinectl show "$NAME" --property=State 2>/dev/null | grep -q running; then
    echo "container $NAME not running" >&2; exit 1
fi

DST="/var/lib/machines/${NAME}/root/.np-mycnf"
DUMP="/var/lib/machines/${NAME}/var/tmp/cockpit-nspawn-db.sql"
install -m600 "$MYCNF_SRC" "$DST"
trap 'rm -f "$DST"' EXIT

systemd-run --machine="$NAME" --wait -- \
    bash -c 'mysqldump --defaults-extra-file=/root/.np-mycnf --single-transaction --routines --events --all-databases > /var/tmp/cockpit-nspawn-db.sql'
echo "db snapshot ok: $DUMP" >&2
```

Dumpen hamnar i container-trädet och följer därför med i rsync-pullen. Inget
DB-lösenord lämnar någonsin kund-servern.

> Alternativ till mysqldump: om `/var/lib/machines` ligger på btrfs kan man ta en
> atomär subvolym-snapshot på kund-sidan och pulla från den. mysqldump är enklare
> och ger logisk konsistens för InnoDB — börja där, optimera vid behov.

---

## 4. Valv-sidan (betrodd, gör allt jobb)

### 4.1 Per-kund pull-skript

`/usr/local/lib/nspawn-vault/pull.sh <kund-host> <container> <ssh-key> <dataset>`:

```bash
#!/bin/bash
set -euo pipefail
HOST="$1"; NAME="$2"; KEY="$3"; DATASET="$4"   # ex: vault/kund17/sys900
MNT="/$(zfs get -H -o value mountpoint "$DATASET")"  # eller hårdkoda /vault-roten
SSH=(ssh -i "$KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
STATE="/var/lib/nspawn-vault/state/${DATASET//\//_}.json"
mkdir -p "$(dirname "$STATE")"

fail() { printf '{"result":"failed","ts":"%s","msg":"%s"}\n' "$(date -Iseconds)" "$1" > "$STATE"; exit 1; }

# 1) Be kunden ta en färsk DB-dump (forced command tillåter bara detta + rrsync)
"${SSH[@]}" "$HOST" "snapshot-db $NAME" || fail "snapshot-db failed"

# 2) Pulla read-only in i den levande dataseten. ZFS sköter inkrementellt;
#    ingen --link-dest behövs eftersom snapshots ger versionerna.
rsync -aH --delete --numeric-ids \
    -e "${SSH[*]}" \
    "$HOST:/var/lib/machines/$NAME/" "$MNT/" \
    || fail "rsync pull failed"

# 3) Atomär, read-only, oåtkomlig-från-källan snapshot
zfs snapshot "${DATASET}@$(date +%Y%m%d-%H%M%S)" || fail "zfs snapshot failed"

printf '{"result":"success","ts":"%s"}\n' "$(date -Iseconds)" > "$STATE"
```

Kör per kund via en systemd-timer på valvet (t.ex. var 30:e min, spridd).

### 4.2 GFS-retention — lokalt på valvet

Återanvänd din befintliga GFS-logik, men mot `zfs list -t snapshot` istället för
fjärr-`rm`. Skelett:

```bash
#!/bin/bash
# prune.sh <dataset> <hourly> <daily> <weekly> <monthly> <yearly>
set -euo pipefail
DATASET="$1"; shift
zfs list -H -o name -t snapshot -s creation "$DATASET" \
  | sed "s#^${DATASET}@##" \
  | python3 /usr/local/lib/nspawn-vault/gfs.py "$@" \
  | while read -r snap; do zfs destroy "${DATASET}@${snap}"; done
```

`gfs.py` = i princip din GFS_PYTHON, men den läser snapshotnamn från stdin och
*skriver ut dem som ska raderas* (istället för att radera filer själv). Då blir
borttagningen ett explicit `zfs destroy` på den betrodda sidan.

### 4.3 Dead-man's switch (det viktigaste larmet)

En separat timer på valvet som skannar alla `state/*.json` och larmar om någon
kund inte producerat en färsk lyckad pull inom tröskeln. En krypterad/död host
*kan inte* själv rapportera — den slutar bara dyka upp här.

```bash
#!/bin/bash
set -euo pipefail
THRESHOLD_MIN=180
now=$(date +%s)
for f in /var/lib/nspawn-vault/state/*.json; do
    ts=$(python3 -c 'import json,sys,datetime;d=json.load(open(sys.argv[1]));print(int(datetime.datetime.fromisoformat(d["ts"]).timestamp()))' "$f" 2>/dev/null || echo 0)
    res=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["result"])' "$f" 2>/dev/null || echo missing)
    age=$(( (now - ts) / 60 ))
    if [ "$res" != success ] || [ "$age" -gt "$THRESHOLD_MIN" ]; then
        # återanvänd din send_notification (SMTP/Slack/Pushover)
        notify "STALE BACKUP: $(basename "$f") result=$res age=${age}min"
    fi
done
```

---

## 5. Offsite-tier (3-2-1, ransomware-säker)

Välj en (eller båda):

**A. ZFS send till en receive-only replika** (renast):
```bash
zfs send -I "${DATASET}@<förra>" "${DATASET}@<senaste>" \
  | ssh offsite "zfs recv -F vaultmirror/<kund>/<container>"
```
Offsite-boxen tar bara emot; den har inga creds tillbaka. Helst på annan
fysisk plats / annat moln.

**B. restic till objektlagring med append-only / object-lock** (moln):
```bash
restic -r b2:kund-vault backup "$MNT/.zfs/snapshot/<senaste>"
```
Använd en **append-only restic-nyckel** (`--append-only` på REST/rclone-servern,
eller B2 Object Lock). Då kan en komprometterad valv-server *inte* radera historiken.

Poängen: även om valvet faller ska minst en kopia vara fysiskt oraderbar.

---

## 6. Återställning (snabb väg)

Restore behöver skrivrätt mot kund-servern — men den rätten ska **inte** vara den
read-only backup-nyckeln. Aktivera en separat restore-väg medvetet vid behov
(interaktiv admin-SSH över Tailscale, eller en andra nyckel som normalt är
avstängd). Då finns ingen stående cred som kan skriva data åt något håll.

Hel container tillbaka:

```bash
# 1) Välj snapshot på valvet
zfs rollback "${DATASET}@<snapshot>"          # eller mounta .zfs/snapshot/<x> read-only

# 2) Rsynca tillbaka till kunden (restore-väg, ej backup-nyckeln)
machinectl stop "$NAME" 2>/dev/null || true
rsync -aH --delete "$MNT/" "kund:/var/lib/machines/$NAME/"

# 3) Starta och importera DB-dumpen inifrån
machinectl start "$NAME"
systemd-run --machine="$NAME" --wait -- \
    bash -c 'mysql < /var/tmp/cockpit-nspawn-db.sql'
```

Mät och dokumentera RTO per kund — en backup vars restore aldrig testats är en
hypotes.

---

## 7. Integration i cockpit-nspawn

Pull-logiken bor på **valvet**, inte på kund-servern. Förslag på uppdelning:

- **Kund-server (befintlig cockpit-nspawn):** lägg till en enkel toggle
  *"Enable pull backup"* som installerar `dispatch.sh`, `snapshot-db.sh`,
  forced-command-raden i `authorized_keys`, och (om DB) skriver `*.cnf`. Inget mer.
- **Valv (ny cockpit-sida eller egen modul `nspawn-vault`):** en
  översiktssida som listar alla kunder, senaste lyckade pull, snapshot-antal,
  offsite-status och stale-larm. Här bor pull/prune/dead-man-timrarna.
- **Behåll push-modellen** som den är för dem som vill — pull blir det säkra
  standardläget för flottan. `RestoreDialog` kan utökas så den känner igen
  "pull"-konfigurerade containrar och hämtar från valvet istället.

Config-format kan spegla befintligt `/etc/cockpit-nspawn/backup/<name>.json`
men med `mode: "pull"` och valv-sidans fält (`dataset`, `gfs_*`, offsite-mål).

---

## 8. Utrullningsordning (lågrisk → hög hävstång)

1. Sätt upp valvet + ZFS + en testkund. Verifiera pull → snapshot → restore.
2. Lägg till dead-man-larmet *innan* du litar på systemet.
3. Migrera kunderna en och en; kör pull parallellt med befintlig push tills du
   sett en lyckad restore per kund.
4. Lägg till offsite-tier (append-only) när on-site-flödet är stabilt.
5. Stäng av push-nycklarna på migrerade kunder — då försvinner den stående
   skrivrätten från kund-servrarna helt.

Resultatet: en komprometterad kund-server kan kryptera sin egen produktion, men
har varken nyckel eller väg till backuparna, och valvet larmar inom minuter på
att pullarna slutat komma. Restore blir en hanterbar händelse istället för en
katastrof.
