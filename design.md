# nspawn-vault — Designdokument

Fristående webb-UI för backup-valvet (INTE en Cockpit-modul — se beslut nedan).
Bygger på infrastrukturen i `/usr/libexec/nspawn-vault/` på valv-servern.

Se även: `nspawn-pull-backup-design.md` (hotmodell och arkitektur)

---

## Beslut: fristående webb-UI, inte en Cockpit-modul (2026-07-01)

Ursprungligen planerad som en Cockpit-modul (`cockpit-nspawn-vault`), men beslutat
att bygga ett helt fristående webb-frontend istället, av två skäl:

1. **Renare översikter**: valvet ska kunna visa tydliga varningssignaler (stora
   röda varningar när en källas pull-backuper slutat fungera) utan att behöva
   anpassa sig efter Cockpits UI-ramverk och begränsningar.
2. **Inget Cockpit-beroende**: valvet är redan en separat server med ett eget
   syfte (ta emot pull-backuper, hantera ZFS, larma) — kräver inte `machinectl`
   eller någon av Cockpits containerhanteringsfunktioner.

Tänkt stack: samma mönster som the internal build tool (FastAPI-backend + enkel frontend),
med Caddy som reverse proxy/TLS framför. Inte påbörjat — ren framtidsplan.
Autentisering (eftersom Cockpits PAM-inloggning inte längre finns gratis) är
olöst och måste beslutas när UI:t faktiskt börjar byggas.

Kund-sidan (dispatcher + authorized_keys i `/usr/local/lib/nspawn-pull/` på
nspawn-hosten, oförändrad idag) förblir dock kopplad till cockpit-nspawn-
paketeringen — se
"Kund-sidan i cockpit-nspawn (toggle)" nedan. Det är bara valvets EGEN
administrationsyta som blir fristående.

---

## Valv-OS: byte från Ubuntu till AlmaLinux (planerat, 2026-07-01)

Nuvarande valv (192.0.2.10) kör Ubuntu 26.04 + native `zfsutils-linux` och
fortsätter göra det tills vidare — det är referens/produktion under tiden.

Ny plan: en AlmaLinux 10 libvirt/KVM-VM (byggs på build-host) med ZFS via
OpenZFS officiella DKMS-repo för EL, för att få samma bekanta RHEL-stack som
resten av miljön. Skälet till att Ubuntu valdes ursprungligen (ZFS kräver DKMS
på RHEL pga CDDL/GPL-licenskonflikt, Ubuntu har det inbyggt) kvarstår som
motivering för *varför* det inte är trivialt — men bedöms värt besväret för
konsistensens skull.

**RPM-paketering redan skissad och lokalt byggtestad**: `/root/nspawn-vault/engine/`
(på build-host — flyttat dit 2026-07-03 när `nspawn-vault` och `nspawn-vault-web`
slogs ihop till en gemensam utvecklingskatalog, se `CLAUDE.md`), med
`nspawn-vault.spec`. Innehåller:

- Skript flyttade från `/usr/local/lib` (fel FHS-plats för paketerad mjukvara)
  till `/usr/libexec/nspawn-vault/`
- **Buggfix**: `pull.sh`s dataset-default var hårdkodad till `vault/source0/...`
  oavsett `$HOST` — fixat till att härledas från `${HOST%%.*}`, annars kolliderar
  flera källservrar i samma dataset-namnrymd
- Systemd **template unit** `nspawn-vault-pull@.service`/`.timer` ersätter
  handskrivna per-host-filer — ny källa: `systemctl enable --now
  nspawn-vault-pull@<host>.timer`
- `setup-zfs.sh` och `init-pool.sh` som explicita manuella first-run-steg
  (medvetet INTE i `%post` — repo-tillägg och poolskapande för känsligt att
  göra tyst)
- **Helt verifierat i praktiken 2026-07-01** mot en riktig AlmaLinux 10.2-VM
(198.51.100.20, byggd på build-host): `setup-zfs.sh` körd (rätt repo-URL hittad
efter att första gissningen 404:ade — `zfs-release-3-0.el10`, inte `2-3`),
`init-pool.sh /dev/vdb` körd, RPM byggd via the internal build tool (eget projekt skapat,
`nspawn-vault-1.0.0-2.el10.noarch.rpm`) och installerad rent med `dnf install
<url>` — `Requires: zfs` löstes automatiskt. SSH-nyckel genererad, kundsidans
`authorized_keys` på source0 utökad additivt (ny rad, rör inte produktionsvalvets
befintliga nyckel), containerlista + timers konfigurerade, en full pull-cykel mot
`testapp1` kördes och gav `{"result":"success",...}` med en 1.49 GB ZFS-snapshot.
Fullständig steg-för-steg-dokumentation i `/root/nspawn-vault/engine/README.md`
(skriven för att bli GitHub-projektets startsida).

Källträd finns som tarboll `/root/nspawn-vault.tar.gz`, the internal build tool-projekt skapat
(separat från cockpit-nspawns project_id 4).

---

## Framtida: replikering mellan flera valv (syncoid) — bara en fundering

Om fler valv (2-3 st) sätts upp för redundans: rekommenderat är `zfs send |
zfs receive` via `syncoid` (sanoid-projektets omslag), inte ett block-nivå-
mirror-vdev (ZFS-mirrors är inte designade för separata hosts/WAN). Detta
återanvänder samma snapshots som redan tas av GFS-schemat.

**Viktigt att komma ihåg**: detta är asynkron replikering, inte synkron
spegling — en sekundär valv ligger alltid en pull-cykel efter (idag ~30 min).
Ger redundans/DR, inte failover utan dataförlustfönster. Inget beslutat,
inget att bygga just nu.

---

## Infrastruktur på valvet (blir framtida webb-UI:ts backend)

### Katalogstruktur på valvet

Nuvarande Ubuntu-valv (192.0.2.10) använder fortfarande `/usr/local/lib/`.
Den nya RPM-paketeringen (se ovan) flyttar detta till `/usr/libexec/nspawn-vault/`
— strukturen nedan visar den nya, paketerade layouten:

```
/etc/nspawn-vault/
├── notify.conf                  # Pushover/Slack-creds för valv-larm
└── source0.example.com/
    └── containers               # En container per rad

/usr/libexec/nspawn-vault/
├── pull.sh                      # Pull en container: pull.sh <host> <name>
├── pull-host.sh                 # Pull alla containers för en host
├── check-stale.sh               # Dead-man's switch
├── gfs-prune.sh                 # GFS-retention via zfs destroy
├── gfs.py
├── prune-all.sh
├── setup-zfs.sh                 # manuellt, engångs, EJ i %post
└── init-pool.sh                 # manuellt, engångs, EJ i %post

/var/lib/nspawn-vault/state/
└── vault_<host>_<name>.json     # {"result":"success","ts":"...","snap":"..."}

/vault/                          # ZFS-pool (namn konfigurerbart via NSPAWN_VAULT_POOL)
└── source0/
    └── testapp1/                # dataset, snapshots: @20260630-210156
```

### Systemd-timers på valvet

- `nspawn-vault-pull@<host>.timer` — templerad unit, en instans per källserver
- `nspawn-vault-check.timer` — kör check-stale.sh var 30:e min (dead-man's switch)
- `nspawn-vault-prune.timer` — kör prune-all.sh dagligen 04:00 (GFS-retention)

---

## Vad det fristående webb-UI:t ska visa

### Huvudvy — källserver-översikt

Tabell med en rad per konfigurerad källserver:

| Server | Containers | Senaste pull | Status | ZFS-pool |
|--------|-----------|-------------|--------|----------|
| source0.example.com | 5 | 2026-06-30 21:01 | OK | vault/source0 |

### Detaljvy per server — containers

Expanderbar rad med tabell per container:

| Container | Senaste pull | Snapshot | Storlek | Nästa pull |
|-----------|-------------|---------|---------|-----------|
| testapp1  | 21:01 OK    | @20260630-210156 | 1.5 GB | 21:31 |

### Snapshot-historik

Modal med lista över alla ZFS-snapshots för en container (`zfs list -t snapshot`).
Knapp för att starta restore-flöde.

### Konfiguration

- Lägg till/ta bort källservrar
- Konfigurera pull-intervall per server
- GFS-retentionsnivåer
- Notifieringskanaler (Pushover/Slack/SMTP) för dead-man's switch

---

## Kund-sidan i cockpit-nspawn (toggle)

I `MachineActions.jsx` eller `BackupDialog.jsx`: kryssruta/toggle
*"Aktivera pull-backup från valvet"* som:

1. Installerar `/usr/local/lib/nspawn-pull/dispatch.sh`
2. Installerar `/usr/local/lib/nspawn-pull/snapshot-db.sh`
3. Lägger till forced-command-raden i `/root/.ssh/authorized_keys`
4. Visar valvets publika nyckel som användaren klistrar in på valvet

---

## Teknikval

- **ZFS**: native snapshots, immutabla från källan, effektiv incremental med `zfs send`
- **rrsync -ro**: read-only rsync på kund-sidan, minimal attackyta
- **ed25519**: en nyckel per valv (inte per kund), aldrig på kund-servern
- **systemd-timers**: ingen cron, journal-loggning, enkelt att övervaka
- **State-JSON**: enkel filbaserad status, lätt att läsa från det fristående webb-UI:t

---

## Återstående att bygga (manuell fas)

- [x] ZFS-pool + dataset
- [x] pull.sh + dispatcher + rrsync på kund-sidan
- [x] Testat pull av alla 5 containers från source0
- [x] pull-host.sh (pull alla containers för en host)
- [x] Systemd service + timer för automatiska pulls (var 30:e minut)
- [x] check-stale.sh (dead-man's switch, threshold 3h)
- [x] Systemd timer för check-stale (var 30:e minut)
- [x] gfs-prune.sh + gfs.py (GFS-retention på ZFS-snapshots)
- [x] Systemd timer för prune (dagligen 04:00)
- [x] notify.conf + Pushover-notifiering verifierad
- [x] DB-hantering per container

## DB-hantering i pull-varianten — KLART och TESTAT (2026-07-01)

### Problem
rsync av `/var/lib/machines/<name>/` medan DB kör → risk för inkonsistenta databasfiler.

### Lösning: per-container konfig på kund-sidan

Fil: `/etc/cockpit-nspawn/pull/<name>.conf` (chmod 600, katalog chmod 700)

```bash
# MariaDB/MySQL — dump medan containern kör (rekommenderat)
DB_TYPE=mariadb
DB_USER=root            # default: root
DB_PASSWORD=hemligt     # tomt om auth-plugin tillåter (t.ex. mysql_native_password utan lösen)

# PostgreSQL — dump medan containern kör
DB_TYPE=postgres
DB_USER=postgres        # default: postgres
DB_PASSWORD=hemligt

# Stäng container under backup (valfritt, fungerar oavsett DB_TYPE eller utan)
# Praktiskt för pgapp1 (PostgreSQL, ok att stänga kl 02)
STOP_DURING_BACKUP=true
```

### Beteende i snapshot-db.sh / restore-after-backup.sh beroende på konfig

| DB_TYPE | STOP_DURING_BACKUP | Åtgärd |
|---------|-------------------|--------|
| mariadb | false | mysqldump (--defaults-extra-file) inuti container → sql-fil följer med rsync |
| postgres | false | pg_dumpall (PGPASSWORD) inuti container → sql-fil följer med rsync |
| valfritt/inget | true | machinectl stop → rsync → machinectl start (via trap i pull.sh) |
| (ingen .conf) | — | rsync direkt, ingen DB-hantering (no-op, som tidigare) |

### Implementation

- `dispatch.sh` på kund-sidan whitelistar nu även `restore-after-backup <name>`
  (utöver `snapshot-db <name>` och rsync-kommandot), samma namnvalidering
  (`*/*|*..*|""|*" "*` avvisas).
- `snapshot-db.sh` läser `.conf`-filen om den finns; sourcear `DB_TYPE`,
  `DB_USER`, `DB_PASSWORD`, `STOP_DURING_BACKUP`. Vid `STOP_DURING_BACKUP=true`
  stoppas containern (pollar `machinectl show` tills den försvinner ur
  registret, max 30s) och funktionen returnerar utan dump. Annars körs
  mysqldump/pg_dumpall beroende på `DB_TYPE`.
- `restore-after-backup.sh` (ny) läser samma `.conf`-fil, tar bort
  dump-filen (`/var/tmp/cockpit-nspawn-db.sql`) ur den levande containern
  om `DB_TYPE` var satt — den ska inte ligga okrypterad i produktion längre
  än nödvändigt för att rsync ska hinna dra den — och startar containern
  igen om `STOP_DURING_BACKUP=true`. Dumpen finns kvar (versionerad) i
  ZFS-snapshotarna på valvet.
- `pull.sh` på valvet sätter en `trap ... EXIT` direkt efter `snapshot-db`-
  anropet som alltid anropar `restore-after-backup` vid skriptets slut —
  garanterar återstart även om rsync eller zfs-snapshot-steget faller.

### Testat och verifierat (source0 → valv 192.0.2.10)

- **dbapp1** (MariaDB 10.5, root utan lösenord, `mysql_native_password`):
  `DB_TYPE=mariadb`, `DB_USER=root`, `DB_PASSWORD=` (tom) → mysqldump gav
  41 MB sql-fil i `/var/tmp/cockpit-nspawn-db.sql`, följde med rsync,
  container förblev igång hela tiden.
- **pgapp1** (PostgreSQL 16): `STOP_DURING_BACKUP=true` → container
  stoppades, rsync kördes mot vilande filsystem, ZFS-snapshot togs,
  container startades om automatiskt via trap, PostgreSQL lyssnade på
  5432 igen efter ~3s.
- **testapp1** (ingen `.conf`-fil): no-op-vägen fungerar oförändrat,
  vanlig rsync utan DB-hantering.

---

## Fristående webb-UI (nspawn-vault-web) — BYGGT och verifierat (2026-07-02)

`/root/nspawn-vault/web/` på build-host (flyttat dit 2026-07-03, se `CLAUDE.md`),
9 faser genomförda och verifierade end-to-end (varje fas testad mot skarp
data på AlmaLinux 10-VM:n 198.51.100.20 innan nästa påbörjades):

```
nspawn-vault-web/
├── backend/
│   ├── main.py, auth_routes.py, auth_utils.py, database.py, models.py,
│   │   ldap_service.py          # auth-lager, poratat från the internal build tool (SQLite+WAL istället för Postgres)
│   └── vault_config.py, vault_state.py, vault_zfs.py,
│       vault_systemd.py, vault_routes.py   # nspawn-vault-specifik domänlogik
├── frontend/src/
│   ├── pages/{Login,Dashboard,HostDetail,Admin}.jsx
│   ├── components/{StaleAlertBanner,StatusBadge,Spinner}.jsx
│   ├── components/layout/{Layout,Sidebar}.jsx
│   ├── context/AuthContext.jsx, services/api.js
│   └── locales/{en,sv}/translation.json
├── Caddyfile, env.example, systemd/nspawn-vault-web.service
└── nspawn-vault-web.spec
```

**Auth**: kopierat nästan rakt av från the internal build tool (JWT, argon2, lokal-först-
sen-LDAP-fallback, admin-bootstrap via första-användaren-blir-admin), med två
avsiktliga förbättringar mot originalet: `bind_password` redigeras (`********`)
i `GET /api/admin/ldap` istället för att läcka i klartext, och LDAP-gruppens
adminmedlemskap omvalideras vid **varje** inloggning (inte bara vid första
auto-provisionering som i the internal build tool). SQLite+WAL istället för Postgres —
beslutat med användaren, ingen extra tjänst behövs bara för en handfull
adminkonton.

**Domänlogik** (`vault_state.py`) portar `check-stale.sh`s exakta logik
(THRESHOLD_MIN=180, samma state-JSON-sökvägskonstruktion som `pull.sh`).
**Fas 9-acceptanstest**: körde `check-stale.sh` och webb-API:et sida vid
sida mot både frisk och konstgjord stale/failed-data — exakt överensstämmelse
i alla lägen.

**StaleAlertBanner**: solid `bg-red-600`, `w-8 h-8`-ikon, medvetet eskalerad
långt förbi the internal build tool egen (bleka/små) röda varningsstil — se komponentkoden
för fullständigt Tailwind-recept.

**Buggar hittade och fixade under bygget** (värda att komma ihåg):
- `NextElapseUSecRealtime`-mönstret (från `BackupsOverview.jsx`) har en
  dold bugg: `date -d ""` (tom sträng, ingen schemalagd körning) misslyckas
  INTE utan tolkas tyst som "idag" — måste kolla tom sträng explicit innan
  `date -d` anropas, annars får man ett falskt men giltigt epoch-värde.
- the build tool's `AuthContext.login()` satte bara `{username}` på `user`-objektet,
  inte hela användarposten (inkl. `role`) — gjorde adminmeny osynlig direkt
  efter inloggning tills sidan laddades om. Fixat: `login()` anropar nu
  `fetchCurrentUser()` efter lyckad inloggning.
- **RPM-paketering av vendored venv**: `%global debug_package %{nil}`
  krävs (tomma debuginfo-paket kraschar bygget), och `venv/bin/uvicorn`
  m.fl. wrapper-skript har byggsökvägen inbränd i sin shebang — trasig så
  fort venv:n kopieras till buildroot. Löst med `python3 -m uvicorn` i
  systemd-enheten (kringgår wrapper-skripten helt) + `%__requires_exclude_from`/
  `%__provides_exclude_from` (annars läcker RPM:s beroendescanner in
  byggsökvägen som ett trasigt `Requires`).
- **Bygg måste ske på målets Python-version**: testat konkret — lokalt
  Fedora 44-bygge (Python 3.14) är inkompatibelt med AlmaLinux 10 (Python
  3.12) eftersom site-packages-sökvägen är versionsspecifik. Löst genom att
  installera `rpm-build`+`gcc`+`python3-devel` och bygga direkt på VM:n.

**Verifierat installerat och körande** på 198.51.100.20: `dnf install`
av den färdiga RPM:en, tjänsten startar rent, serverar både `/api/*` och
den byggda frontend:en (FastAPI:s egen static-fallback).

**Caddy verifierat 2026-07-02**: installerat från EPEL, `Caddyfile` med
`http://` (rent HTTP, ingen ACME/TLS — LAN-räcker, ingen WAN-access krävs)
reverse-proxyar `/api/*` till `127.0.0.1:8000` och serverar frontend på
`:80`. Bekräftat nåbart över LAN (`curl http://198.51.100.20/...` från
build-host) medan uvicorn fortfarande bara lyssnar på `127.0.0.1` — bara
Caddy exponerat mot nätverket. Föranlett av att kollegor behöver kunna
logga in och sköta valvet, inte bara utvecklaren själv.

## Rollbaserad redigering: GFS + notifieringar via Admin (2026-07-02)

Beslut: "user"-roll förblir ren läs-only (Dashboard/HostDetail, som redan
byggt), men "admin"-roll (lokal eller LDAP — samma `role`-fält, redan
byggt i fas 1/6) ska kunna redigera GFS-retention och notifieringar direkt
i UI:t istället för att handredigera filer på valvet.

**Backend**: `vault_config.write_gfs_conf()` och `write_notify_conf()` (nya),
`read_notify_conf_masked()` för admin-vyn (samma `********`-sentinel-mönster
som LDAP `bind_password` — hemligheter läcks aldrig i klartext till
frontend). Nya endpoints, alla `get_current_admin`-skyddade:
`PUT /api/admin/settings/gfs`, `GET/PUT /api/admin/settings/notify`.

**Säkerhetskritiskt fynd under bygget**: `check-stale.sh` gör `source
"$NOTIFY_CONF"` som root — om Pushover-token/Slack-URL skrivs oskyddat till
filen och innehåller skalmetatecken (`$()`, bakåtcitat, etc.) skulle det
kunna exekvera godtycklig kod nästa gång dead-man's-switchen kör. Löst med
`_shell_quote()` (enkelfnuttar + escape av inbäddade fnuttar) i
`write_notify_conf()`. **Testat konkret**: skrev in `$(touch /tmp/PWNED)`
som token via API, verifierade att filen på disk fick korrekt
enkelfnutt-citerat värde, körde `source` på riktigt — ingen fil skapades,
värdet kom ut som ren textsträng. Även bekräftat att `********`-sentinelen
bevarar riktiga hemligheter vid efterföljande PUT utan ändring.

**Frontend**: nya sektioner "GFS Retention" och "Notifications" i
`Admin.jsx` (samma kort-layout som LDAP-sektionen). `Dashboard.jsx`s
gamla "redigera på disk"-hint ersatt med rollmedveten text: admin ser
en länk till Admin-sidan, user ser "be en admin ändra detta".

## Källserver/containerlista via Admin (2026-07-02)

Byggt som uppföljning: full CRUD för källservrar och deras containerlistor
i Admin-sidan, admin-skyddat (`get_current_admin`).

**Backend** (`vault_config.py`): `create_host()`, `delete_host()`,
`write_containers()` — hostnamn blir en katalogsökväg och containernamn blir
rader i en fil + ZFS-dataset-segment, så båda valideras strikt mot path
traversal/skalfarliga tecken (`_HOSTNAME_RE`, `_CONTAINER_NAME_RE`) innan
något rör filsystemet. **Testat konkret**: `../../etc/evil` som hostnamn
och `../evil` som containernamn — båda nekade med 400.

`delete_host()` tar **bara bort pull-konfigurationen** (katalogen under
`/etc/nspawn-vault/`) — rör aldrig ZFS-datasets eller snapshots. Befintliga
backuper för en borttagen host finns kvar, bara framtida pullar stoppas.
Detta är explicit dokumenterat i UI:t (bekräftelsedialog vid borttagning).

**Timer-styrning** (`vault_systemd.py`): `enable_pull_timer()`/
`disable_pull_timer()` — `systemctl enable/disable --now
nspawn-vault-pull@<host>.timer`. Att skapa en host aktiverar INTE timern
automatiskt (SSH-trust på kundsidan måste sättas upp manuellt först, se
kund-toggle-avsnittet ovan) — admin flippar på den separat när klart, ett
tydligt UI-meddelande om detta visas i "Lägg till host"-formuläret.

**Testat end-to-end** (skapa → redigera containerlista → aktivera timer →
ta bort → verifiera att timer avaktiverades och produktionshosten
`source0.example.com` förblev orörd genom hela flödet), både direkt mot
backend och genom Caddy på port 80.

**Ej byggt (medvetet, se scope i v1)**: ZFS-snapshot-bläddring/restore-UI,
multi-valv-replikerings-UI.

Huvudfokus i UI:t (enligt beslutet ovan): väldigt tydlig, iögonfallande varning
när en källas pull-backuper slutat fungera — inte bara en diskret badge som i
Cockpit-mönstret, utan något som inte går att missa.
