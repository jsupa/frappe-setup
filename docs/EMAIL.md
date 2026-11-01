# ✉️ Odosielanie e-mailov (SMTP)

Nastavenie odchádzajúcej pošty pre Frappe, CRM a Helpdesk (odpovede na tikety, notifikácie, reset hesla). Hodnoty v `<...>` sú zástupné symboly.

| Placeholder | Význam |
| :--- | :--- |
| `<SITE>` | názov Frappe stránky (`SITE_NAME`) |
| `<SMTP_HOST>` / `<SMTP_PORT>` | SMTP server a port (napr. Gmail: `smtp.gmail.com`, `465`) |
| `<SMTP_USER>` | prihlasovacie meno (napr. Gmail adresa) |
| `<SMTP_PASS>` | heslo, pri Gmaile **App Password** (16 znakov, vyžaduje 2FA) |
| `<FROM>` | adresa odosielateľa, ktorú uvidia príjemcovia |
| `<DOMAIN>` | doména adresy `<FROM>` |

---

## 1. Vytvorenie Email Account

Skript `scripts/setup_email.py` vytvorí (alebo aktualizuje) Email Account s názvom `<FROM>` a nastaví ho ako **Default Outgoing**. Konfigurácia ide cez stdin, takže heslo nebude v argumentoch procesu ani v logoch. Heslo Frappe uloží šifrované cez `encryption_key` zo `site_config.json`.

```bash
docker cp scripts/setup_email.py frappe-backend-1:/tmp/setup_email.py

# `read -s` = heslo sa nevypíše ani neuloží do histórie
read -s -p "SMTP password: " SMTP_PASS; echo
printf '{"site":"%s","host":"%s","port":%s,"user":"%s","password":"%s","from":"%s"}' \
  "<SITE>" "<SMTP_HOST>" "<SMTP_PORT>" "<SMTP_USER>" "$SMTP_PASS" "<FROM>" \
  | docker exec -i -w /home/frappe/frappe-bench/sites frappe-backend-1 \
      /home/frappe/frappe-bench/env/bin/python /tmp/setup_email.py
unset SMTP_PASS
docker exec frappe-backend-1 rm -f /tmp/setup_email.py
```

Výstup `saved <FROM> default_outgoing 1` znamená, že sa Frappe úspešne prihlásil na SMTP (overuje to pri uložení). Zlé meno alebo heslo skončí chybou `SMTPAuthenticationError`.

Skript nastavuje:
- `use_ssl_for_outgoing=1`, `use_tls=0` (port **465** = implicitné SSL). Pre port **587** (STARTTLS) treba opačne: `use_tls=1`, `use_ssl_for_outgoing=0`.
- `login_id_is_different=1`: prihlasuje sa ako `<SMTP_USER>`, odosiela ako `<FROM>`.
- `always_use_account_email_id_as_sender=1`: všetka pošta ide z `<FROM>`, nie z adresy prihláseného používateľa.
- `enable_incoming=0`: iba odchádzajúca pošta (tikety z e-mailov cez IMAP sa nastavujú zvlášť).

Rovnaké nastavenie sa dá spraviť aj ručne v Desk: **/app/email-account/new**.

## 2. Test

```bash
docker exec frappe-backend-1 bench --site <SITE> execute frappe.sendmail \
  --kwargs '{"recipients": ["<TEST_RECIPIENT>"], "subject": "Frappe SMTP test", "message": "Test", "now": True}'

docker exec frappe-backend-1 bench --site <SITE> mariadb -e \
  "select name, status, error, creation from \`tabEmail Queue\` order by creation desc limit 3;"
```

`status = Sent` a prázdny `error` znamená, že SMTP server správu prijal. Pri `Error` je dôvod v stĺpci `error`. Bežná pošta z CRM/Helpdesku ide cez frontu (`Email Queue`), ktorú odosiela scheduler + worker, takže scheduler musí byť zapnutý (`bench --site <SITE> scheduler status`).

## 3. Odkazy v e-mailoch: `host_name` (nutné pre „Login with Email Link“)

Frappe skladá odkazy v e-mailoch (prihlásenie cez e-mail, reset hesla, odkazy na tikety a dokumenty) cez `get_url()`. Prihlasovací odkaz sa generuje s `allow_header_override=False`, takže **ignoruje `Host` hlavičku** požiadavky. Bez `host_name` použije názov stránky, napr. `http://<SITE>/api/method/frappe.www.login.login_via_key?key=…`, a ten mimo lokálnej siete neexistuje. E-mail sa odošle (`Sent`), ale odkaz nefunguje.

Nastav verejnú URL, cez ktorú sa na systém reálne pristupuje (vrátane `https://`), a reštartuj procesy. Gunicorn a workery majú `site_config.json` načítaný v pamäti, takže samotný `clear-cache` nestačí:

```bash
docker exec frappe-backend-1 bench --site <SITE> set-config host_name <PUBLIC_URL>
docker compose -f compose.yaml -f compose.vps.yaml restart backend queue-long queue-short scheduler websocket
```

Overenie (vypíše vygenerovaný odkaz s maskovaným kľúčom):

```bash
docker exec -i frappe-backend-1 bench --site <SITE> console <<'PY'
from frappe.utils import get_url
print(get_url("/api/method/frappe.www.login.login_via_key?key=***", allow_header_override=False))
PY
```

Ďalej pre „Login with Email Link“ platí:
- Musí byť zapnuté v **System Settings ➔ Login ➔ Login with email link** (`login_with_email_link=1`, platnosť `login_with_email_link_expiry` v minútach).
- Odkaz príde iba **existujúcemu a aktívnemu používateľovi** (`User`). Pri neznámej adrese Frappe zámerne nič neodošle ani nezobrazí chybu (ochrana pred zisťovaním účtov).
- Odkaz je jednorazový, druhé otvorenie vráti 403.

## 4. Gmail ako SMTP s vlastnou doménou v `From`

Gmail prijme SMTP správu s ľubovoľnou `From` hlavičkou, ale **potichu ju prepíše** na `<SMTP_USER>`, ak `<FROM>` nie je overený alias. Že je `status = Sent`, ešte neznamená, že príjemca vidí `<FROM>`. Over to v Gmaile v priečinku **Odoslané**.

Aby príjemcovia videli `<FROM>`:

1. **Alias v Gmaile**: Settings ➔ Accounts ➔ *Send mail as* ➔ pridaj `<FROM>`. Na `<FROM>` príde overovací kód (pri doméne s Cloudflare Email Routing ho dostaneš na adresu, kam routing preposiela).
2. **SPF pre `<DOMAIN>`**: povoľ Google servery, inak príde pošta so `softfail` a často skončí v spame. Napr. pri Cloudflare Email Routing:
   ```text
   v=spf1 include:_spf.mx.cloudflare.net include:_spf.google.com ~all
   ```
   Aktuálny stav: `dig +short TXT <DOMAIN>`.
3. **DMARC** (odporúčané): TXT záznam `_dmarc.<DOMAIN>`, napr. `v=DMARC1; p=none; rua=mailto:<REPORT_ADDRESS>`. Začni s `p=none` a sprísni, až keď SPF prechádza.

> DKIM podpis pre vlastnú doménu cez osobný Gmail nastaviť nejde (to vie iba Google Workspace). Ak záleží na doručiteľnosti, zváž transakčného poskytovateľa (Brevo, Mailgun, SES, …) s DKIM pre doménu. Stačí potom zmeniť `<SMTP_HOST>/<SMTP_PORT>/<SMTP_USER>/<SMTP_PASS>` a znovu spustiť skript.

## 5. Zmena hesla / rotácia App Password

Vygeneruj nové App Password, spusti krok 1 znova s novým heslom (skript existujúci účet aktualizuje) a potom staré heslo v Google účte zruš.

## ⚠️ Úskalia

| Problém | Príčina | Riešenie |
| :--- | :--- | :--- |
| `SMTPAuthenticationError` (535) | bežné heslo namiesto App Password, alebo vypnuté 2FA | zapni 2FA, vygeneruj App Password |
| Login link / reset hesla vedie na `http://<SITE>/…` | chýba `host_name`, alebo procesy bežia so starým configom | krok 3 vrátane reštartu |
| Login link vôbec nepríde | adresa nepatrí aktívnemu `User`, alebo je funkcia vypnutá | krok 3, System Settings |
| `Sent`, ale príjemca vidí Gmail adresu | `<FROM>` nie je *Send mail as* alias | krok 4.1 |
| Pošta padá do spamu | SPF domény nepovoľuje Google, chýba DMARC/DKIM | krok 4.2 a 4.3 |
| Maily ostávajú `Not Sent` | nebeží scheduler alebo worker | `bench --site <SITE> enable-scheduler`, skontroluj `queue-*` kontajnery |
| Timeout pri spojení | poskytovateľ VPS blokuje odchádzajúce SMTP porty | over `nc -vz <SMTP_HOST> <SMTP_PORT>` z VPS, prípadne iný port (465/587) |
