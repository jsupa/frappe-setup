#!/usr/bin/env python3
"""
Vytvorí / aktualizuje odchádzajúci Email Account (SMTP) a nastaví ho ako predvolený.

Konfigurácia ide cez stdin ako JSON, aby heslo nebolo v argv, `ps` ani v shell histórii:
  {"site": "...", "host": "...", "port": 465, "user": "...", "password": "...", "from": "..."}

Port 465 = implicitné SSL. Pri save() Frappe overí SMTP prihlásenie, zlé údaje skončia chybou.
Návod: docs/EMAIL.md
"""
import json, sys
import frappe

cfg = json.load(sys.stdin)
frappe.init(site=cfg["site"], sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()

name = cfg["from"]
doc = frappe.get_doc("Email Account", name) if frappe.db.exists("Email Account", name) else frappe.new_doc("Email Account")
doc.update({
    "email_account_name": name,
    "email_id": cfg["from"],
    "login_id_is_different": 1,
    "login_id": cfg["user"],
    "password": cfg["password"],
    "awaiting_password": 0,
    "enable_incoming": 0,
    "enable_outgoing": 1,
    "default_outgoing": 1,
    "smtp_server": cfg["host"],
    "smtp_port": cfg["port"],
    "use_ssl_for_outgoing": 1,  # 465 = implicit SSL
    "use_tls": 0,
    "always_use_account_email_id_as_sender": 1,
})
doc.save()  # validate() otvorí SMTP spojenie a prihlási sa
frappe.db.commit()
print("saved", doc.name, "default_outgoing", doc.default_outgoing)
