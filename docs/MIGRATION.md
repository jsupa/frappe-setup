# 🚚 Migrácia Frappe stacku (OMV ➔ VPS) + zálohovanie cez restic

Návod na presun bežiaceho stacku (Frappe v16 + CRM + Helpdesk) z OpenMediaVault servera na VPS vrátane všetkých dát (DB, súbory, konfigurácia stránky) a zaradenie dát do existujúcej restic zálohy.

Všetky hodnoty v `<...>` sú zástupné symboly, doplň si vlastné:

| Placeholder | Význam |
| :--- | :--- |
| `<SOURCE>` | SSH cieľ zdrojového (OMV) servera, napr. `root@<IP>` |
| `<VPS>` | SSH cieľ VPS, napr. `<user>@<host>` |
| `<VPS_HOST>` | hostname / IP VPS pre HTTP test |
| `<STACK_DIR>` | adresár stacku na VPS, napr. `/home/<user>/frappe` |
| `<SITE>` | názov Frappe stránky (hodnota `SITE_NAME`) |
| `<PORT>` | port frontendu na VPS (napr. voľný `15xxx`) |

Príkazy sa púšťajú z lokálneho stroja, ktorý má SSH prístup na oba servery. Dáta idú cez neho (`ssh ... | ssh ...`), takže servery na seba nemusia vidieť (OMV môže byť za NAT).

---

## 🧠 Prečo bind mounty namiesto named volumes

Na VPS beží restic ako bežný používateľ a zálohuje iba jeho domovský adresár. Docker named volumes ležia v `/var/lib/docker/volumes` (iba root), takže by sa do zálohy nikdy nedostali. Preto `compose.vps.yaml` mapuje všetky volumes na adresáre v `FRAPPE_DATA_DIR`:

```text
<STACK_DIR>/data/
├── sites/        # site_config.json (vrátane encryption_key!), súbory, zálohy  → restic ✅
├── logs/         # logy                                                         → restic ✅
├── db/           # surové MariaDB dáta (uid 999, živé = nekonzistentné)          → restic ❌ (exclude)
└── redis-queue/  # fronta úloh, prechodné                                        → restic ❌ (exclude)
```

Databáza sa do zálohy dostáva ako konzistentný dump cez `bench backup --with-files` (skript `scripts/backup.sh`), ktorý sa uloží do `sites/<SITE>/private/backups/`, teda do zálohovaného adresára.

> ⚠️ `encryption_key` v `sites/<SITE>/site_config.json` je nutný na dešifrovanie hesiel uložených v DB (e-mailové účty, API kľúče). Bez neho je obnova DB neúplná. Je súčasťou `sites/`, preto `sites/` vždy zálohuj celý.

---

## 0. Predpoklady

- Na VPS: Docker + Compose v2, používateľ v skupine `docker`, voľný port `<PORT>`.
- Restic repozitár a skript, ktorý ho spúšťa (cron), už existujú.
- Na zdroji: zisti, ktorý image **naozaj beží**. Tag sa mohol medzičasom presunúť na novší build:
  ```bash
  ssh <SOURCE> 'docker inspect frappe-backend-1 --format "{{.Image}}"'
  ```
  Prenášaj práve toto ID, nie tag. Tak je kód aplikácií zaručene zhodný so schémou DB.

## 1. Kontrolná záloha na zdroji + referenčné počty

Pozor: `bench backup` v kontajneri `backend` zlyhá s `TLS/SSL error: SSL is required` (rovnaký problém ako pri `new-site`, viď README). Na zdroji to jednorazovo obídeš:

```bash
ssh <SOURCE> 'docker exec frappe-backend-1 bash -c "printf \"[client]\nssl=0\n\" > ~/.my.cnf"
              docker exec frappe-backend-1 bench --site <SITE> backup --with-files'
```

Na VPS to rieši natrvalo `docker/resources/my.cnf`, ktorý `compose.vps.yaml` mountuje do `backend`, workerov a schedulera.

Zapíš si počty záznamov na porovnanie po migrácii:

```bash
ssh <SOURCE> 'docker exec frappe-backend-1 bench --site <SITE> mariadb -e "
  select (select count(*) from \`tabCRM Lead\`) leads,
         (select count(*) from \`tabCRM Organization\`) orgs,
         (select count(*) from \`tabContact\`) contacts,
         (select count(*) from \`tabCRM Task\`) tasks,
         (select count(*) from \`tabHD Ticket\`) tickets,
         (select count(*) from \`tabUser\`) users,
         (select count(*) from \`tabFile\`) files;"'
```

## 2. Príprava VPS

```bash
ssh <VPS> 'git clone https://github.com/jsupa/frappe-setup.git <STACK_DIR>'
```

`.env` skopíruj zo zdroja priamo cez pipe, aby sa heslá nikde nevypisovali. Na OMV je to súbor vygenerovaný pluginom Compose, napr. `/compose/frappe/frappe.env`:

```bash
ssh <SOURCE> 'grep -E "^(DB_PASSWORD|ADMIN_PASSWORD|SITE_NAME|FRAPPE_SITE_NAME_HEADER)=" /compose/frappe/frappe.env' \
  | ssh <VPS> 'umask 077; cat > <STACK_DIR>/.env
               printf "HTTP_PORT=<PORT>\nFRAPPE_DATA_DIR=<STACK_DIR>/data\n" >> <STACK_DIR>/.env'
```

> `DB_PASSWORD` musí ostať rovnaký: root heslo je uložené priamo v prenesených MariaDB dátach.

Vytvor dátové adresáre so správnymi vlastníkmi (frappe=1000, mariadb=999, redis=999) a over konfiguráciu:

```bash
ssh <VPS> 'cd <STACK_DIR> && ./scripts/init-data-dir.sh <STACK_DIR>/data \
           && docker compose -f compose.yaml -f compose.vps.yaml config > /dev/null && echo OK'
```

## 3. Prenos image

Image sa nebuilduje nanovo. `build.sh` ťahá vetvy `main`/`develop`, takže by vznikla iná verzia kódu než tá, ktorej zodpovedá DB.

```bash
IMAGE_ID=$(ssh <SOURCE> 'docker inspect frappe-backend-1 --format "{{.Image}}"')
ssh <SOURCE> "docker save $IMAGE_ID | gzip -1" | ssh <VPS> 'gunzip | docker load'
# ➔ "Loaded image ID: sha256:<LOADED_ID>"
ssh <VPS> 'docker tag <LOADED_ID> frappe-crm-helpdesk:v16'
```

(~3.6 GB nekomprimovane, trvá podľa uploadu.)

> `<LOADED_ID>` sa môže líšiť od `$IMAGE_ID`. Ak cieľový Docker používa containerd image store (`docker info` ➔ `io.containerd.snapshotter.v1`), ID je digest manifestu, nie configu. Taguj podľa ID z výstupu `docker load` a zhodu over porovnaním vrstiev:
> ```bash
> F='{{.Created}} {{range .RootFS.Layers}}{{.}} {{end}}'
> diff <(ssh <SOURCE> "docker image inspect $IMAGE_ID --format '$F'") \
>      <(ssh <VPS> "docker image inspect <LOADED_ID> --format '$F'") && echo IDENTICAL
> ```

## 4. Cutover: zastavenie zdroja a prenos dát

Od tohto kroku je výpadok. Stack na zdroji iba **zastav** (`stop`, nie `down`), aby ostal pripravený na rollback:

```bash
ssh <SOURCE> 'cd /compose/frappe && docker compose stop'
```

Každý volume sa prenesie tarom so zachovaním číselných vlastníkov a rozbalí sa cez dočasný kontajner (zápis do `db/` vyžaduje uid 999, bez sudo):

```bash
for pair in sites:sites logs:logs db-data:db redis-queue-data:redis-queue; do
  src=${pair%%:*}; dst=${pair##*:}
  ssh <SOURCE> "tar -C /var/lib/docker/volumes/frappe_${src}/_data --numeric-owner -cpf - ." \
    | ssh <VPS> "docker run --rm -i -v <STACK_DIR>/data/${dst}:/dst alpine:3.20 tar -C /dst --numeric-owner -xpf -"
done
```

Over počet súborov a súčet bajtov na oboch stranách. `du` sa môže líšiť o násobky 4096 (veľkosť adresárov na inom FS), preto rátaj iba súbory:

```bash
# zdroj (pre každý volume)
find /var/lib/docker/volumes/frappe_<VOL>/_data -type f -printf '%s\n' | awk '{n++; s+=$1} END {print n"/"s}'
# VPS (cez kontajner, db/ patrí uid 999)
docker run --rm -v <STACK_DIR>/data:/d alpine:3.20 sh -c \
  'find /d/<DIR> -type f -exec stat -c %s {} + | awk "{n++; s+=\$1} END {print n\"/\"s}"'
```

## 5. Spustenie na VPS

```bash
ssh <VPS> 'cd <STACK_DIR> && docker compose -f compose.yaml -f compose.vps.yaml up -d'
```

`configurator` prepíše `common_site_config.json` (rovnaké hodnoty), `create-site` zistí, že stránka existuje, a preskočí sa. Potom over:

```bash
ssh <VPS> 'cd <STACK_DIR> && docker compose -f compose.yaml -f compose.vps.yaml ps'
ssh <VPS> 'docker exec frappe-backend-1 bench --site <SITE> migrate'        # pri rovnakom image no-op, ale neuškodí
ssh <VPS> 'docker exec frappe-backend-1 bench --site <SITE> scheduler status'
curl -s -o /dev/null -w "%{http_code}\n" http://<VPS_HOST>:<PORT>/api/method/ping   # 200
curl -s -o /dev/null -w "%{http_code}\n" http://<VPS_HOST>:<PORT>/crm               # 200
curl -s -o /dev/null -w "%{http_code}\n" http://<VPS_HOST>:<PORT>/helpdesk          # 200
```

Spusti dotaz na počty z kroku 1 aj na VPS. Čísla musia sedieť.

## 6. Zaradenie do restic zálohy

1. **Excludes**: surové DB a redis dáta vynechaj. Patria uid 999, takže restic bežiaci ako bežný používateľ ich nemusí vedieť prečítať (exit code 3 = neúplný snapshot, cron skript to vyhodnotí ako chybu), a živá kópia DB aj tak nie je konzistentná. Do exclude súboru restic pridaj:
   ```text
   <STACK_DIR>/data/db
   <STACK_DIR>/data/redis-queue
   ```
2. **Dump pred restic**: v skripte, ktorý spúšťa `restic backup`, zavolaj pred ním:
   ```bash
   <STACK_DIR>/scripts/backup.sh <SITE> || { echo "CHYBA: Frappe backup zlyhal!"; exit 1; }
   ```
   Skript spraví `bench backup --with-files` do `sites/<SITE>/private/backups/` a lokálne zmaže zálohy staršie ako 3 dni (históriu drží restic).
3. **Over**, že posledný snapshot obsahuje dump:
   ```bash
   restic ls --recursive latest <STACK_DIR>/data | grep -E 'private/backups/.*database.sql.gz$|<SITE>/site_config.json$'
   restic ls --recursive latest <STACK_DIR>/data | grep -c '/data/db'      # musí byť 0
   ```
   Pozor: `restic ls <snapshot> <cesta>` bez `--recursive` vypíše iba priamy obsah adresára a vyzerá to, akoby dump v snapshote chýbal.
4. **Over čitateľnosť dumpu** priamo zo snapshotu:
   ```bash
   restic dump latest <STACK_DIR>/data/sites/<SITE>/private/backups/<TS>-database.sql.gz \
     | gunzip | grep -c '^INSERT INTO `tabCRM Lead`'   # > 0
   ```

## 7. Obnova z restic

```bash
restic restore latest --target /tmp/frappe-restore --include <STACK_DIR>/data/sites
R=/tmp/frappe-restore<STACK_DIR>/data/sites/<SITE>/private/backups
ls -t $R/*database.sql.gz | head -1        # vyber najnovší dump (<TS> = jeho časová pečiatka)
docker cp $R/<TS>-database.sql.gz          frappe-backend-1:/tmp/
docker cp $R/<TS>-files.tar                frappe-backend-1:/tmp/
docker cp $R/<TS>-private-files.tar        frappe-backend-1:/tmp/
docker exec frappe-backend-1 bench --site <SITE> restore /tmp/<TS>-database.sql.gz \
  --with-public-files /tmp/<TS>-files.tar --with-private-files /tmp/<TS>-private-files.tar \
  --db-root-password "$DB_PASSWORD"
docker exec frappe-backend-1 bench --site <SITE> migrate
```

Pri obnove na čistý server obnov celý `sites/` (kvôli `encryption_key` v `site_config.json`), potom `up -d` a až nakoniec `bench restore`.

## 8. Rollback a upratanie zdroja

- **Rollback** (kým zdroj nie je zmazaný): `docker compose stop` na VPS, `docker compose start` na zdroji. Dáta na zdroji sú v stave pred cutoverom.
- **Upratanie** až po pár dňoch bezproblémovej prevádzky: zmaž stack v OMV GUI (Services ➔ Compose ➔ Files), potom `docker volume rm frappe_sites frappe_logs frappe_db-data frappe_redis-queue-data`.

---

## ⚠️ Úskalia, na ktoré sme narazili

| Problém | Príčina | Riešenie |
| :--- | :--- | :--- |
| Bežiace kontajnery majú image bez tagu | `frappe-crm-helpdesk:v16` bol prebuildnutý, kontajnery bežia zo starého ID | prenášať ID z `docker inspect frappe-backend-1` |
| `bench backup` ➔ `TLS/SSL error: SSL is required` | `~/.my.cnf` so `ssl=0` vzniká iba v kontajneri `create-site` | `docker/resources/my.cnf` mountovaný cez `compose.vps.yaml` |
| restic nevidí named volumes | `/var/lib/docker` patrí rootovi, restic beží ako bežný používateľ | bind mounty do `FRAPPE_DATA_DIR` |
| restic exit code 3 na `data/db` | súbory MariaDB patria uid 999 | exclude `data/db` a `data/redis-queue`, DB ide cez dump |
| `docker load` vráti iné image ID | cieľ používa containerd image store | tag podľa ID z `docker load`, zhoda cez porovnanie vrstiev |
| `bench migrate` ➔ `unknown command FT.CREATE` | Helpdesk search index chce RediSearch, `redis:6.2-alpine` ho nemá | nesúvisí s migráciou, migrate prebehne; pre fulltext v Helpdesku treba `redis-stack` |
