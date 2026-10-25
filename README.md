# Frappe CRM & Helpdesk v16 (Local Setup / OpenMediaVault)

Kompletný, opakovateľný návod a Docker Compose konfigurácia na lokálne nasadenie **Frappe v16** spolu s **Frappe CRM**, **Frappe Helpdesk** a **Telephony** bez nutnosti zložitého Traefiku, SSL certifikátov alebo externých reverzných proxy.

---

## 📋 Prehľad a architektúra

- **Frappe Framework**: verzia 16 (`version-16`)
- **Aplikácie**:
  - `telephony` (branch `develop`)
  - `helpdesk` (branch `main`)
  - `crm` (branch `main`)
- **Databáza**: MariaDB 10.6 s `utf8mb4_unicode_ci`
- **Cache & Queues**: Redis 6.2 (Alpine)
- **Prístup**: Priamy HTTP port (predvolene `:8080`), bez nutnosti DNS alebo SSL
- **Cieľový názov lokálnej stránky**: `menucka.local`

---

## 🚀 Rýchly štart (Quick Start)

### 1. Klonovanie repozitára
```bash
git clone https://github.com/jsupa/frappe-setup.git
cd frappe-setup
```

### 2. Vytvorenie a úprava `.env`
Skopíruj `.env.example` do `.env`:
```bash
cp .env.example .env
```
Obsah `.env`:
```ini
DB_PASSWORD=YourSecureDbPassword123!
ADMIN_PASSWORD=YourSecureAdminPassword123!
SITE_NAME=menucka.local
FRAPPE_SITE_NAME_HEADER=menucka.local
HTTP_PORT=8080
```

### 3. Zostavenie Docker Image
Oficiálne Frappe obrazy neobsahujú CRM ani Helpdesk. Pripravili sme automatický build skript s potrebnými patchmi:
```bash
./docker/build.sh
```
*(Tento krok stiahne Frappe v16, naklonuje CRM, Helpdesk a Telephony a skompiluje frontendové assety cez Vite a esbuild).*

### 4. Spustenie stacku
```bash
docker compose up -d
```
Docker automaticky:
1. Spustí MariaDB a Redis.
2. Počká na ich dostupnosť a prepojí ich (`configurator`).
3. Vytvorí novú stránku `menucka.local`, nainštaluje databázu a aplikácie (`create-site`).
4. Naštartuje backend, scheduler, workery a frontend (Nginx).

### 5. Zapnutie schedulera
Po vytvorení stránky povoľ plánovač úloh na pozadí:
```bash
./scripts/enable-scheduler.sh menucka.local
```

---

## 🖥️ Integrácia do OpenMediaVault (OMV Compose)

Ak používaš plugin **openmediavault-compose**:

1. V OMV Web GUI choď do: **Services ➔ Compose ➔ Files**.
2. Klikni na **+ (Vytvoriť)**:
   - **Name**: `frappe`
   - **Description**: `Frappe CRM & Helpdesk v16`
   - **File**: Vlož obsah súboru `compose.yaml`
   - Zaškrtni **Environment** a vlož:
     ```ini
     DB_PASSWORD=YourSecureDbPassword123!
     ADMIN_PASSWORD=YourSecureAdminPassword123!
     SITE_NAME=menucka.local
     FRAPPE_SITE_NAME_HEADER=menucka.local
     HTTP_PORT=8080
     ```
3. Klikni **Save** a následne **Apply**.
4. Označ stack a klikni na **Up** (Spustiť).

---

## 🔗 Prístup k aplikáciám

Po spustení otvor prehliadač na IP adrese tvojho servera (napr. `192.168.1.183`):

| Aplikácia | URL cesta | Popis |
| :--- | :--- | :--- |
| **Prihlásenie / Home** | `http://<IP_SERVERA>:8080/` | Hlavná prihlasovacia obrazovka |
| **Frappe Helpdesk** | `http://<IP_SERVERA>:8080/helpdesk` | Zákaznícka podpora a tikety |
| **Frappe CRM** | `http://<IP_SERVERA>:8080/crm` | Správa zákazníkov a leadov |
| **Frappe Desk** | `http://<IP_SERVERA>:8080/desk` | Správa systému, používateľov a modulov |

**Predvolené prihlasovacie údaje:**
- **Používateľ**: `Administrator`
- **Heslo**: hodnota `ADMIN_PASSWORD` v `.env` (napr. `YourSecureAdminPassword123!`)

---

## 🧩 Štruktúra repozitára

```text
frappe-setup/
├── README.md                 # Táto dokumentácia
├── compose.yaml              # Docker Compose definícia pre všetky služby
├── .env.example              # Vzorový konfiguračný súbor
├── docker/
│   ├── Containerfile         # Viacfázový Dockerfile s patchom pre v16
│   ├── apps.json             # Manifest aplikácií (telephony, helpdesk, crm)
│   ├── build.sh              # Skript na zostavenie vlastného docker image
│   └── resources/
│       └── core/
│           ├── main-entrypoint.sh # Entrypoint prepájajúci assety na volume
│           └── start.sh           # Štartovací skript pre Gunicorn
└── scripts/
    ├── enable-scheduler.sh   # Pomocný skript na zapnutie schedulera
    └── console.sh            # Rýchly prístup do bash konzoly kontajnera
```

---

## 💡 Kľúčové technické riešenia a úskalia

Pri inštalácii Frappe CRM a Helpdesk vo verzii 16 s Dockerom sme vyriešili niekoľko špecifických problémov:

### 1. Kompatibilita Frappe v16 a CRM frontendu
- **Problém**: Vo Frappe v16 bol komponent `DataImport.vue` premenovaný na `DataImportWizard.vue`. CRM frontend (Vite) pri kompilácii hľadal starý export `DataImport` a zostavenie assetov skončilo chybou:
  `[MISSING_EXPORT] "DataImport" is not exported by "../../frappe/ui/src/components/DataImport/index.ts"`.
- **Riešenie**: V `docker/Containerfile` sa po `bench init` automaticky aplikuje patch do `apps/frappe/ui/src/components/DataImport/index.ts`:
  ```bash
  echo 'export { default as DataImport } from "./DataImportWizard.vue";' >> apps/frappe/ui/src/components/DataImport/index.ts
  ```

### 2. MariaDB Client SSL mismatch
- **Problém**: MariaDB klient v novších distribúciách Debianu predvolene vyžaduje SSL (`ssl=1`). Pri vytváraní stránky (`bench new-site`) volá Frappe interný príkaz `mariadb`, ktorý skončil chybou:
  `ERROR 2026 (HY000): TLS/SSL error: SSL is required, but the server does not support it`.
- **Riešenie**: Pred spustením `bench new-site` kontajner vytvorí konfiguráciu `~/.my.cnf`:
  ```ini
  [client]
  ssl=0
  ```
  Vďaka tomu sa klient pripojí k MariaDB bez SSL po bezpečnej internej Docker sieti.

### 3. Smerovanie požiadaviek podľa IP (Bez nutnosti DNS)
- **Problém**: Frappe je štandardne multi-tenant systém a stránku vyberá podľa HTTP hlavičky `Host`. Ak používateľ pristupuje cez `http://192.168.1.183:8080`, Frappe štandardne hľadá stránku s názvom IP adresy.
- **Riešenie**: Premenná `FRAPPE_SITE_NAME_HEADER=menucka.local` zabezpečí, že Nginx automaticky pridáva hlavičku `X-Frappe-Site-Name: menucka.local` na každý prichádzajúci request, takže stránka funguje okamžite cez IP aj lokálnu doménu.

---

## 🛠️ Užitočné príkazy (Bench Cheatsheet)

### Vstup do konzoly kontajnera:
```bash
./scripts/console.sh
```

### Záloha databázy a súborov:
```bash
docker exec frappe-backend-1 bench --site menucka.local backup --with-files
```
*(Zálohy sa ukladajú do volume `sites/menucka.local/private/backups/`).*

### Obnova databázy zo zálohy:
```bash
docker exec frappe-backend-1 bench --site menucka.local restore /cesta/k/zaloha.sql.gz
```

### Migrácia databázy po aktualizácii aplikácií:
```bash
docker exec frappe-backend-1 bench --site menucka.local migrate
```

### Prečistenie vyrovnávacej pamäte:
```bash
docker exec frappe-backend-1 bench --site menucka.local clear-cache
```

---

## 🍽️ Migrácia databázy reštaurácií (`restaurant-list` -> Frappe CRM)

Skript `scripts/migrate_restaurants.py` zabezpečuje kompletnú migráciu slovenských reštaurácií (`unikatne_restauracie_komplet_full.csv`) do Frappe CRM:
1. Vytvorí vlastné polia (`Custom Field`) na **CRM Organization**, **Contact** a **CRM Lead** (IČO, denné menu, URL denného menu, Tier, Rating, počet recenzií, GPS, portály SME/Bistro/Bolt/Menucka, Buying Signals, Recommended Pitch).
2. Aktualizuje rozloženie polí vo Frappe CRM UI (`CRM Fields Layout`).
3. Nastaví predajnú pipeline (`CRM Lead Status`): `In Check` -> `In Contact` -> `Contacted` -> `Replied` -> `Deal` / `Ignored`.
4. Zoskupí majiteľov podľa `IČO` / obchodného mena a vytvorí pre nich záznamy **CRM Organization** + **Contact**.
5. Naimportuje všetky reštaurácie ako **CRM Lead** v počiatočnom stave `In Check` a prepojí ich s organizáciou a kontaktom majiteľa.
6. Vytvorí pre každý **CRM Lead** 4 nadväzujúce obchodné úlohy (**CRM Task**):
   - `1. Research (Prieskum & overenie)` (`Todo`)
   - `2. Initial Contact (Prvé oslovenie)` (`Backlog`)
   - `3. Follow-up (Pripomenutie / reakcia)` (`Backlog`)
   - `4. Deal Closing (Uzavretie dohody)` (`Backlog`)

### Spustenie migrácie:
```bash
docker cp unikatne_restauracie_komplet_full.csv frappe-backend-1:/tmp/restaurants.csv
docker cp scripts/migrate_restaurants.py frappe-backend-1:/tmp/migrate_restaurants.py
docker exec -w /home/frappe/frappe-bench/sites frappe-backend-1 \
  /home/frappe/frappe-bench/env/bin/python /tmp/migrate_restaurants.py menucka.local /tmp/restaurants.csv
```
*(Pre samostatné pregenerovanie iba úloh bez reimportu leadov stačí pridať parameter `--tasks-only`)*

---

## 📄 Licencia
MIT License. Vytvorené pre lokálne nasadenie Frappe v16, CRM a Helpdesku.
