#!/usr/bin/env python3
"""
Synchronize financial data (Výnosy/Revenue, Zisk/Profit, Majetok/Assets, Vlastné imanie/Equity,
DIČ, Právna forma, SK NACE, Dátum založenia, Register ÚZ URL) from the Slovak
Register účtovných závierok (RÚZ) REST API (https://www.registeruz.sk/cruz-public/api)
into Frappe CRM (CRM Organization & CRM Lead).

Also sets up:
1. Custom financial fields on CRM Organization & CRM Lead
2. CRM Fields Layout updates so financial fields are visible in Frappe CRM UI
3. Server Script API ('sync_registeruz') + CRM Form Scripts on CRM Lead & CRM Organization
   providing a 1-click "Sync RÚZ (Výnosy & Zisk)" button in the Frappe CRM web UI.
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import frappe
import requests
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.installer import update_site_config
from frappe.utils import now_datetime


BASE_URL = "https://www.registeruz.sk/cruz-public/api"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SK_NACE_MAP = {
    "56101": "Reštauračné činnosti a pohostinstvá",
    "56102": "Jedálne",
    "56109": "Ostatné jedálenské a pohostinské služby",
    "56210": "Cateringové a dodávateľské služby",
    "56290": "Ostatné stravovacie služby",
    "56300": "Pohostinské činnosti a bary",
    "10710": "Výroba pekárenských výrobkov a pečiva",
    "47110": "Maloobchod v nešpecializovaných predajniach",
    "47250": "Maloobchod s nápojmi",
    "68200": "Prenájom a prevádzkovanie nehnuteľností",
    "70220": "Poradenské služby v oblasti podnikania",
    "82990": "Ostatné pomocné obchodné činnosti",
}

LEGAL_FORM_MAP = {
    "112": "Spoločnosť s ručením obmedzeným (s.r.o.)",
    "101": "Živnostník / Fyzická osoba",
    "121": "Akciová spoločnosť (a.s.)",
    "111": "Verejná obchodná spoločnosť (v.o.s.)",
    "117": "Jednoduchá spoločnosť na akcie (j.s.a.)",
    "118": "Komanditná spoločnosť (k.s.)",
}


def setup_financial_custom_fields():
    print("[1/4] Setting up RÚZ Financial Custom Fields on CRM Organization & CRM Lead...")
    fin_fields_org = [
        {
            "fieldname": "custom_dic",
            "label": "DIČ",
            "fieldtype": "Data",
            "insert_after": "custom_ico",
            "in_list_view": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_legal_form",
            "label": "Právna forma",
            "fieldtype": "Data",
            "insert_after": "custom_dic",
        },
        {
            "fieldname": "custom_sk_nace",
            "label": "SK NACE (Odvetvie)",
            "fieldtype": "Data",
            "insert_after": "custom_legal_form",
        },
        {
            "fieldname": "custom_established_on",
            "label": "Dátum založenia",
            "fieldtype": "Data",
            "insert_after": "custom_sk_nace",
        },
        {
            "fieldname": "custom_revenue_year",
            "label": "Rok závierky",
            "fieldtype": "Int",
            "insert_after": "annual_revenue",
            "in_list_view": 1,
        },
        {
            "fieldname": "custom_profit",
            "label": "Čistý zisk / VH (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_revenue_year",
            "in_list_view": 1,
        },
        {
            "fieldname": "custom_assets",
            "label": "Majetok / Aktíva (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_profit",
        },
        {
            "fieldname": "custom_equity",
            "label": "Vlastné imanie (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_assets",
        },
        {
            "fieldname": "custom_register_uz_url",
            "label": "Register ÚZ URL",
            "fieldtype": "Data",
            "insert_after": "custom_equity",
        },
        {
            "fieldname": "custom_ruz_sync_status",
            "label": "RÚZ Sync Status",
            "fieldtype": "Select",
            "options": "\npending\nsynced\nnot_found\nerror",
            "default": "pending",
            "insert_after": "custom_register_uz_url",
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_ruz_synced_at",
            "label": "Posledný sync s RÚZ",
            "fieldtype": "Datetime",
            "insert_after": "custom_ruz_sync_status",
        },
    ]

    fin_fields_lead = [
        {
            "fieldname": "custom_dic",
            "label": "DIČ",
            "fieldtype": "Data",
            "insert_after": "custom_ico",
        },
        {
            "fieldname": "custom_legal_form",
            "label": "Právna forma",
            "fieldtype": "Data",
            "insert_after": "custom_company_name",
        },
        {
            "fieldname": "custom_sk_nace",
            "label": "SK NACE (Odvetvie)",
            "fieldtype": "Data",
            "insert_after": "custom_legal_form",
        },
        {
            "fieldname": "custom_established_on",
            "label": "Dátum založenia",
            "fieldtype": "Data",
            "insert_after": "custom_sk_nace",
        },
        {
            "fieldname": "custom_revenue_year",
            "label": "Rok závierky",
            "fieldtype": "Int",
            "insert_after": "annual_revenue",
        },
        {
            "fieldname": "custom_profit",
            "label": "Čistý zisk / VH (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_revenue_year",
            "in_list_view": 1,
        },
        {
            "fieldname": "custom_assets",
            "label": "Majetok / Aktíva (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_profit",
        },
        {
            "fieldname": "custom_equity",
            "label": "Vlastné imanie (€)",
            "fieldtype": "Currency",
            "options": "currency",
            "insert_after": "custom_assets",
        },
        {
            "fieldname": "custom_register_uz_url",
            "label": "Register ÚZ URL",
            "fieldtype": "Data",
            "insert_after": "custom_equity",
        },
        {
            "fieldname": "custom_ruz_sync_status",
            "label": "RÚZ Sync Status",
            "fieldtype": "Select",
            "options": "\npending\nsynced\nnot_found\nerror",
            "default": "pending",
            "insert_after": "custom_register_uz_url",
            "in_standard_filter": 1,
        },
        {
            "fieldname": "custom_ruz_synced_at",
            "label": "Posledný sync s RÚZ",
            "fieldtype": "Datetime",
            "insert_after": "custom_ruz_sync_status",
        },
        {
            "fieldname": "custom_additional_contacts",
            "label": "Ďalšie kontakty (zo zlúčených leadov)",
            "fieldtype": "Small Text",
            "insert_after": "phone",
        },
    ]

    create_custom_fields(
        {
            "CRM Organization": fin_fields_org,
            "CRM Lead": fin_fields_lead,
        },
        update=True,
    )
    frappe.db.commit()
    print("   -> Financial custom fields created/updated.")


def setup_financial_field_layouts():
    print("[2/4] Updating CRM Fields Layout with Financial (Výnosy, Zisk, RÚZ) sections...")

    lead_side_panel = [
        {
            "label": "Restaurant & Menu",
            "name": "restaurant_menu_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_rest_1",
                    "fields": [
                        "custom_has_daily_menu",
                        "custom_daily_menu_url",
                        "website",
                        "custom_tier",
                        "custom_rating",
                        "custom_reviews_count",
                        "lead_temperature",
                        "lead_score",
                        "custom_verification_reason",
                        "custom_buying_signals",
                        "custom_recommended_pitch",
                    ],
                }
            ],
        },
        {
            "label": "Financie & Register ÚZ (Výnosy, Zisk)",
            "name": "financials_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_fin_1",
                    "fields": [
                        "annual_revenue",
                        "custom_profit",
                        "custom_revenue_year",
                        "custom_assets",
                        "custom_equity",
                        "custom_dic",
                        "custom_legal_form",
                        "custom_sk_nace",
                        "custom_established_on",
                        "custom_register_uz_url",
                        "custom_ruz_sync_status",
                        "custom_ruz_synced_at",
                    ],
                }
            ],
        },
        {
            "label": "Owner & Company",
            "name": "details_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_kl92",
                    "fields": [
                        "organization",
                        "custom_organization_link",
                        "custom_contact_link",
                        "custom_ico",
                        "custom_company_name",
                        "custom_city",
                        "custom_address",
                        "territory",
                        "industry",
                        "lead_owner",
                    ],
                }
            ],
        },
        {
            "label": "Contact Info",
            "name": "person_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_XmW2",
                    "fields": [
                        "first_name",
                        "email",
                        "mobile_no",
                        "phone",
                        "custom_additional_contacts",
                    ],
                }
            ],
        },
        {
            "label": "Portals & Delivery",
            "name": "portals_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_portals_1",
                    "fields": [
                        "source",
                        "custom_sources",
                        "custom_sme_url",
                        "custom_bistro_url",
                        "custom_bolt_url",
                        "custom_menucka_url",
                        "custom_gps",
                        "custom_delivery_available",
                        "custom_takeaway_available",
                    ],
                }
            ],
        },
    ]

    lead_data_fields = [
        {
            "label": "Restaurant & Daily Menu",
            "name": "restaurant_section",
            "opened": True,
            "columns": [
                {
                    "name": "col_r1",
                    "fields": [
                        "custom_has_daily_menu",
                        "custom_daily_menu_url",
                        "website",
                        "custom_verification_reason",
                    ],
                },
                {
                    "name": "col_r2",
                    "fields": [
                        "custom_tier",
                        "custom_rating",
                        "custom_reviews_count",
                        "lead_temperature",
                        "lead_score",
                    ],
                },
                {
                    "name": "col_r3",
                    "fields": [
                        "custom_buying_signals",
                        "custom_recommended_pitch",
                    ],
                },
            ],
        },
        {
            "label": "Financie & Register ÚZ (Výnosy, Zisk, Majetok)",
            "name": "fin_data_section",
            "opened": True,
            "columns": [
                {
                    "name": "col_f1",
                    "fields": [
                        "annual_revenue",
                        "custom_profit",
                        "custom_revenue_year",
                    ],
                },
                {
                    "name": "col_f2",
                    "fields": [
                        "custom_assets",
                        "custom_equity",
                        "custom_register_uz_url",
                    ],
                },
                {
                    "name": "col_f3",
                    "fields": [
                        "custom_dic",
                        "custom_legal_form",
                        "custom_sk_nace",
                        "custom_established_on",
                        "custom_ruz_sync_status",
                    ],
                },
            ],
        },
        {
            "label": "Owner & Location",
            "name": "details_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_ZgLG",
                    "fields": [
                        "organization",
                        "custom_organization_link",
                        "custom_contact_link",
                        "custom_ico",
                        "custom_company_name",
                    ],
                },
                {
                    "name": "column_TbYq",
                    "fields": [
                        "custom_city",
                        "custom_address",
                        "territory",
                        "custom_gps",
                    ],
                },
                {
                    "name": "column_OKSX",
                    "fields": [
                        "first_name",
                        "email",
                        "mobile_no",
                        "phone",
                        "lead_owner",
                    ],
                },
            ],
        },
        {
            "label": "Portals & Delivery",
            "name": "portals_data_section",
            "opened": True,
            "columns": [
                {
                    "name": "col_p1",
                    "fields": [
                        "source",
                        "custom_sources",
                        "custom_delivery_available",
                        "custom_takeaway_available",
                    ],
                },
                {
                    "name": "col_p2",
                    "fields": [
                        "custom_sme_url",
                        "custom_bistro_url",
                    ],
                },
                {
                    "name": "col_p3",
                    "fields": [
                        "custom_bolt_url",
                        "custom_menucka_url",
                    ],
                },
            ],
        },
    ]

    org_side_panel = [
        {
            "label": "Organization & IČO Details",
            "name": "details_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_IJOV",
                    "fields": [
                        "organization_name",
                        "custom_ico",
                        "custom_dic",
                        "custom_legal_form",
                        "custom_sk_nace",
                        "custom_established_on",
                        "custom_establishments_count",
                        "custom_city",
                        "custom_address_text",
                        "custom_phone",
                        "custom_email",
                        "website",
                        "territory",
                        "industry",
                        "custom_restaurant_names",
                        "company_description",
                    ],
                }
            ],
        },
        {
            "label": "Financie & Register ÚZ (Výnosy, Zisk)",
            "name": "org_fin_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_org_fin",
                    "fields": [
                        "annual_revenue",
                        "custom_profit",
                        "custom_revenue_year",
                        "custom_assets",
                        "custom_equity",
                        "custom_register_uz_url",
                        "custom_ruz_sync_status",
                        "custom_ruz_synced_at",
                    ],
                }
            ],
        },
    ]

    layouts = {
        "CRM Lead-Side Panel": lead_side_panel,
        "CRM Lead-Data Fields": lead_data_fields,
        "CRM Organization-Side Panel": org_side_panel,
    }

    for name, layout_data in layouts.items():
        if frappe.db.exists("CRM Fields Layout", name):
            frappe.db.set_value(
                "CRM Fields Layout",
                name,
                "layout",
                json.dumps(layout_data, ensure_ascii=False),
            )
    frappe.db.commit()
    print("   -> Financial layouts updated.")


def setup_ruz_server_and_form_scripts():
    print("[3/4] Setting up Server Script API ('sync_registeruz') & CRM Form Scripts...")
    update_site_config(
        "server_script_enabled",
        1,
        site_config_path="/home/frappe/frappe-bench/sites/common_site_config.json",
    )
    frappe.conf.server_script_enabled = 1

    server_script_code = """
raw_ico = (frappe.form_dict.get("ico") or "").strip()
org_name = frappe.form_dict.get("organization")
lead_name = frappe.form_dict.get("lead")

if not raw_ico and org_name:
    raw_ico = (frappe.db.get_value("CRM Organization", org_name, "custom_ico") or "").strip()
if not raw_ico and lead_name:
    raw_ico = (frappe.db.get_value("CRM Lead", lead_name, "custom_ico") or "").strip()

if not raw_ico:
    frappe.throw("Pre tento záznam nie je vyplnené IČO.")

ico = raw_ico.zfill(8)
base_url = "https://www.registeruz.sk/cruz-public/api"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Connection": "close",
}

def ruz_get(url):
    for attempt in [1, 2, 3]:
        try:
            return frappe.make_get_request(url, headers=headers)
        except Exception:
            if attempt == 3:
                return None
    return None

sk_nace_map = {
    "56101": "Reštauračné činnosti a pohostinstvá",
    "56102": "Jedálne",
    "56109": "Ostatné jedálenské a pohostinské služby",
    "56210": "Cateringové a dodávateľské služby",
    "56290": "Ostatné stravovacie služby",
    "56300": "Pohostinské činnosti a bary",
    "10710": "Výroba pekárenských výrobkov a pečiva",
    "47110": "Maloobchod v nešpecializovaných predajniach",
    "47250": "Maloobchod s nápojmi",
    "68200": "Prenájom a prevádzkovanie nehnuteľností",
    "70220": "Poradenské služby v oblasti podnikania",
    "82990": "Ostatné pomocné obchodné činnosti",
}

legal_form_map = {
    "112": "Spoločnosť s ručením obmedzeným (s.r.o.)",
    "101": "Živnostník / Fyzická osoba",
    "121": "Akciová spoločnosť (a.s.)",
    "111": "Verejná obchodná spoločnosť (v.o.s.)",
    "117": "Jednoduchá spoločnosť na akcie (j.s.a.)",
    "118": "Komanditná spoločnosť (k.s.)",
}

def parse_num(val):
    if val is None or val == "":
        return None
    clean = str(val).replace(" ", "").replace(",", ".")
    try:
        return float(clean)
    except Exception:
        return None

search_data = ruz_get(f"{base_url}/uctovne-jednotky?zmenene-od=2000-01-01&ico={ico}")
now_ts = frappe.utils.now_datetime()

if not search_data or not search_data.get("id"):
    updates = {
        "custom_ruz_sync_status": "not_found",
        "custom_ruz_synced_at": now_ts,
    }
    frappe.response["message"] = {"status": "not_found", "ico": ico}
else:
    uj_id = search_data["id"][0]
    uj_data = ruz_get(f"{base_url}/uctovna-jednotka?id={uj_id}") or {}
    public_url = f"https://www.registeruz.sk/cruz-public/domain/accountingentity/show/{uj_id}"

    sk_nace_code = str(uj_data.get("skNace") or "").strip()
    legal_form_code = str(uj_data.get("pravnaForma") or "").strip()

    updates = {
        "custom_dic": (uj_data.get("dic") or "")[:140],
        "custom_legal_form": (legal_form_map.get(legal_form_code, f"Forma {legal_form_code}") if legal_form_code else "")[:140],
        "custom_sk_nace": (sk_nace_map.get(sk_nace_code, f"SK NACE {sk_nace_code}") if sk_nace_code else "")[:140],
        "custom_established_on": str(uj_data.get("datumZalozenia") or "")[:140],
        "custom_register_uz_url": public_url[:140],
        "custom_ruz_sync_status": "synced",
        "custom_ruz_synced_at": now_ts,
    }

    zavierky = sorted(uj_data.get("idUctovnychZavierok") or [], reverse=True)
    for z_id in zavierky[:3]:
        z_data = ruz_get(f"{base_url}/uctovna-zavierka?id={z_id}") or {}
        st_date = str(z_data.get("datumZostaveniaK") or z_data.get("obdobieDo") or z_data.get("datumPodania") or "")
        year = int(st_date[:4]) if len(st_date) >= 4 and st_date[:4].isdigit() else None
        vykazy = z_data.get("idUctovnychVykazov") or []
        found_table = False

        for v_id in vykazy:
            v_data = ruz_get(f"{base_url}/uctovny-vykaz?id={v_id}") or {}
            obsah = v_data.get("obsah") or {}
            tabulky = obsah.get("tabulky") or []
            for t in tabulky:
                t_name = str((t.get("nazov") or {}).get("sk") or "").lower()
                data = t.get("data") or []
                if "zisk" in t_name or "výkaz" in t_name or "výnos" in t_name:
                    found_table = True
                    rev = None
                    prof = None
                    if len(data) >= 100:
                        op_rev = parse_num(data[2])
                        fin_rev = parse_num(data[56])
                        net_turn = parse_num(data[0])
                        if op_rev is not None:
                            rev = op_rev + (fin_rev if (fin_rev and fin_rev > 0) else 0)
                        elif net_turn is not None:
                            rev = net_turn
                        prof = parse_num(data[120]) if len(data) > 120 else None
                    elif len(data) >= 75:
                        op_rev = parse_num(data[0])
                        fin_rev = parse_num(data[38])
                        if op_rev is not None:
                            rev = op_rev + (fin_rev if (fin_rev and fin_rev > 0) else 0)
                        prof = parse_num(data[74]) if len(data) > 74 else None
                    if rev is not None:
                        updates["annual_revenue"] = rev
                        if year:
                            updates["custom_revenue_year"] = year
                    if prof is not None:
                        updates["custom_profit"] = prof
                if "aktív" in t_name and len(data) > 0:
                    found_table = True
                    assets = parse_num(data[0])
                    if assets is not None:
                        updates["custom_assets"] = assets
                if "pasív" in t_name and len(data) > 2:
                    found_table = True
                    eq = parse_num(data[2])
                    if eq is not None:
                        updates["custom_equity"] = eq
        if found_table:
            break

    orgs = frappe.get_all("CRM Organization", filters={"custom_ico": ["in", [raw_ico, ico]]}, pluck="name")
    for o_name in orgs:
        frappe.db.set_value("CRM Organization", o_name, updates)

    leads = frappe.get_all("CRM Lead", filters={"custom_ico": ["in", [raw_ico, ico]]}, pluck="name")
    for l_name in leads:
        frappe.db.set_value("CRM Lead", l_name, updates)

    frappe.db.commit()
    frappe.response["message"] = {
        "status": "synced",
        "ico": ico,
        "updates": updates,
        "updated_organizations": len(orgs),
        "updated_leads": len(leads),
    }
"""

    ss_name = "Sync RegisterUZ Financials"
    if frappe.db.exists("Server Script", ss_name):
        frappe.db.set_value(
            "Server Script",
            ss_name,
            {
                "script_type": "API",
                "api_method": "sync_registeruz",
                "allow_guest": 0,
                "disabled": 0,
                "script": server_script_code,
            },
        )
    else:
        frappe.get_doc(
            {
                "doctype": "Server Script",
                "name": ss_name,
                "script_type": "API",
                "api_method": "sync_registeruz",
                "allow_guest": 0,
                "disabled": 0,
                "script": server_script_code,
            }
        ).insert(ignore_permissions=True)

    # Update CRM Form Script on CRM Lead to have both "Sync RÚZ (Výnosy & Zisk)" and "Merge Lead (Zlúčiť)"
    lead_form_script = """class CRMLead {
  onLoad() {
    const existing = Array.isArray(this.actions) ? this.actions : []
    this.actions = [
      ...existing.filter(
        (a) =>
          a.label !== 'Merge Lead (Zlúčiť)' &&
          a.label !== 'Sync RÚZ (Výnosy & Zisk)',
      ),
      {
        label: 'Sync RÚZ (Výnosy & Zisk)',
        icon: 'refresh-cw',
        onClick: () => this.syncRegisterUz(),
      },
      {
        label: 'Merge Lead (Zlúčiť)',
        icon: 'git-merge',
        onClick: () => this.openMergeDialog(),
      },
    ]
  }

  async syncRegisterUz() {
    const ico = (this.doc.custom_ico || '').trim()
    if (!ico) {
      this.toast.error('Tento Lead nemá vyplnené IČO. Doplňte IČO a skúste znova.')
      return
    }
    try {
      this.toast.info(`Sťahujem účtovnú závierku z Register ÚZ pre IČO ${ico}...`)
      const res = await this.call('sync_registeruz', {
        ico,
        lead: this.doc.name,
        organization: this.doc.custom_organization_link || this.doc.organization,
      })
      if (res && res.status === 'synced') {
        const rev = res.updates?.annual_revenue ?? 0
        const prof = res.updates?.custom_profit ?? 0
        this.toast.success(
          `RÚZ Sync hotový (IČO ${ico}): Výnosy ${rev.toLocaleString('sk-SK')} € | Zisk ${prof.toLocaleString('sk-SK')} €`,
        )
        window.location.reload()
      } else {
        this.toast.warning(`Subjekt s IČO ${ico} sa nenašiel v Register ÚZ.`)
      }
    } catch (err) {
      console.error(err)
      this.toast.error(err?.message || 'Chyba pri synchronizácii s Register ÚZ.')
    }
  }

  async openMergeDialog() {
    const sourceName = this.doc.name
    const sourceTitle = this.doc.lead_name || this.doc.first_name || sourceName

    const result = await this.formDialog({
      title: `Zlúčiť Lead "${sourceTitle}" (${sourceName}) do iného Leadu`,
      size: 'lg',
      fields: [
        {
          fieldname: 'target_lead',
          label: 'Cieľový Lead (Target Lead, ktorý ostane zachovaný)',
          fieldtype: 'Link',
          options: 'CRM Lead',
          reqd: 1,
        },
        {
          fieldname: 'copy_missing_fields',
          label: 'Doplniť chýbajúce polia (IČO, financie, web, menu URL, portály, kontakty) do cieľového Leadu',
          fieldtype: 'Check',
          default: 1,
        },
        {
          fieldname: 'remove_duplicate_tasks',
          label: 'Odstrániť duplicitné nezačaté úlohy (Todo/Backlog) z tohto Leadu pred zlúčením',
          fieldtype: 'Check',
          default: 1,
        },
      ],
      defaults: {
        copy_missing_fields: 1,
        remove_duplicate_tasks: 1,
      },
      submitLabel: 'Zlúčiť Leady (Merge)',
      cancelLabel: 'Zrušiť',
    })

    if (!result || !result.target_lead) return
    const targetName = result.target_lead

    if (targetName === sourceName) {
      this.toast.error('Nemôžete zlúčiť Lead sám so sebou.')
      return
    }

    try {
      if (result.copy_missing_fields) {
        const targetDoc = await this.call('frappe.client.get', {
          doctype: 'CRM Lead',
          name: targetName,
        })

        const copyFields = [
          'email',
          'mobile_no',
          'phone',
          'website',
          'territory',
          'annual_revenue',
          'custom_ico',
          'custom_dic',
          'custom_company_name',
          'custom_legal_form',
          'custom_sk_nace',
          'custom_established_on',
          'custom_revenue_year',
          'custom_profit',
          'custom_assets',
          'custom_equity',
          'custom_register_uz_url',
          'custom_organization_link',
          'custom_contact_link',
          'custom_city',
          'custom_address',
          'custom_daily_menu_url',
          'custom_verification_reason',
          'custom_sme_url',
          'custom_bistro_url',
          'custom_bolt_url',
          'custom_menucka_url',
          'custom_gps',
          'custom_buying_signals',
          'custom_recommended_pitch',
        ]

        const updates = {}
        for (const f of copyFields) {
          if (!targetDoc[f] && this.doc[f]) {
            updates[f] = this.doc[f]
          }
        }

        if (
          targetDoc.custom_has_daily_menu !== 'ÁNO' &&
          this.doc.custom_has_daily_menu === 'ÁNO'
        ) {
          updates.custom_has_daily_menu = 'ÁNO'
        }

        if (this.doc.custom_sources) {
          const s1 = (targetDoc.custom_sources || '')
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
          const s2 = (this.doc.custom_sources || '')
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
          const mergedSources = Array.from(new Set([...s1, ...s2])).join(', ')
          if (mergedSources && mergedSources !== targetDoc.custom_sources) {
            updates.custom_sources = mergedSources.slice(0, 140)
          }
        }

        if (Object.keys(updates).length > 0) {
          await this.call('frappe.client.set_value', {
            doctype: 'CRM Lead',
            name: targetName,
            fieldname: updates,
          })
        }
      }

      if (result.remove_duplicate_tasks) {
        const tasks = await this.call('frappe.client.get_list', {
          doctype: 'CRM Task',
          filters: {
            reference_doctype: 'CRM Lead',
            reference_docname: sourceName,
            status: ['in', ['Todo', 'Backlog']],
          },
          fields: ['name'],
          limit_page_length: 50,
        })
        if (tasks && tasks.length) {
          for (const t of tasks) {
            await this.call('frappe.client.delete', {
              doctype: 'CRM Task',
              name: t.name,
            })
          }
        }
      }

      await this.call('frappe.model.rename_doc.update_document_title', {
        doctype: 'CRM Lead',
        docname: sourceName,
        name: targetName,
        merge: 1,
        enqueue: 0,
      })

      this.toast.success(`Lead ${sourceName} bol úspešne zlúčený do ${targetName}.`)
      this.router.push({ name: 'Lead', params: { leadId: targetName } })
    } catch (err) {
      console.error(err)
      this.toast.error(err?.message || 'Nepodarilo sa zlúčiť Leady.')
    }
  }
}
"""

    if frappe.db.exists("CRM Form Script", "Merge CRM Lead Script"):
        frappe.db.set_value(
            "CRM Form Script",
            "Merge CRM Lead Script",
            {"script": lead_form_script, "enabled": 1, "dt": "CRM Lead", "view": "Form"},
        )
    else:
        frappe.get_doc(
            {
                "doctype": "CRM Form Script",
                "name": "Merge CRM Lead Script",
                "dt": "CRM Lead",
                "view": "Form",
                "enabled": 1,
                "is_standard": 0,
                "script": lead_form_script,
            }
        ).insert(ignore_permissions=True)

    org_form_script = """class CRMOrganization {
  onLoad() {
    const existing = Array.isArray(this.actions) ? this.actions : []
    this.actions = [
      ...existing.filter((a) => a.label !== 'Sync RÚZ (Výnosy & Zisk)'),
      {
        label: 'Sync RÚZ (Výnosy & Zisk)',
        icon: 'refresh-cw',
        onClick: () => this.syncRegisterUz(),
      },
    ]
  }

  async syncRegisterUz() {
    const ico = (this.doc.custom_ico || '').trim()
    if (!ico) {
      this.toast.error('Táto organizácia nemá vyplnené IČO.')
      return
    }
    try {
      this.toast.info(`Sťahujem účtovnú závierku z Register ÚZ pre IČO ${ico}...`)
      const res = await this.call('sync_registeruz', {
        ico,
        organization: this.doc.name,
      })
      if (res && res.status === 'synced') {
        const rev = res.updates?.annual_revenue ?? 0
        const prof = res.updates?.custom_profit ?? 0
        this.toast.success(
          `RÚZ Sync hotový (IČO ${ico}): Výnosy ${rev.toLocaleString('sk-SK')} € | Zisk ${prof.toLocaleString('sk-SK')} €`,
        )
        window.location.reload()
      } else {
        this.toast.warning(`Subjekt s IČO ${ico} sa nenašiel v Register ÚZ.`)
      }
    } catch (err) {
      console.error(err)
      this.toast.error(err?.message || 'Chyba pri synchronizácii s Register ÚZ.')
    }
  }
}
"""

    if frappe.db.exists("CRM Form Script", "Sync RÚZ Organization Script"):
        frappe.db.set_value(
            "CRM Form Script",
            "Sync RÚZ Organization Script",
            {
                "script": org_form_script,
                "enabled": 1,
                "dt": "CRM Organization",
                "view": "Form",
            },
        )
    else:
        frappe.get_doc(
            {
                "doctype": "CRM Form Script",
                "name": "Sync RÚZ Organization Script",
                "dt": "CRM Organization",
                "view": "Form",
                "enabled": 1,
                "is_standard": 0,
                "script": org_form_script,
            }
        ).insert(ignore_permissions=True)

    frappe.db.commit()
    print("   -> Server Script 'sync_registeruz' and Form Scripts configured.")


def parse_num(val):
    if val is None or val == "":
        return None
    clean = str(val).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(clean)
    except Exception:
        return None


def ruz_http_get(session: requests.Session, url: str, headers: dict):
    for attempt in range(3):
        try:
            r = session.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:
            if attempt < 2:
                time.sleep(0.3 * (attempt + 1))
    return None


def fetch_ico_financials(raw_ico: str, session: requests.Session) -> tuple[str, dict]:
    ico = raw_ico.strip().zfill(8)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Connection": "close",
    }
    try:
        time.sleep(0.08)
        search_data = ruz_http_get(
            session,
            f"{BASE_URL}/uctovne-jednotky?zmenene-od=2000-01-01&ico={ico}",
            headers,
        )
        if search_data is None:
            return raw_ico, {"status": "error"}
        ids = search_data.get("id") or []
        if not ids:
            return raw_ico, {"status": "not_found"}

        uj_id = ids[0]
        uj_data = ruz_http_get(
            session, f"{BASE_URL}/uctovna-jednotka?id={uj_id}", headers
        )
        if not uj_data:
            return raw_ico, {"status": "not_found"}

        public_url = (
            f"https://www.registeruz.sk/cruz-public/domain/accountingentity/show/{uj_id}"
        )
        sk_nace_code = str(uj_data.get("skNace") or "").strip()
        legal_form_code = str(uj_data.get("pravnaForma") or "").strip()

        res = {
            "status": "synced",
            "dic": str(uj_data.get("dic") or "")[:140],
            "legal_form": (
                LEGAL_FORM_MAP.get(legal_form_code, f"Forma {legal_form_code}")
                if legal_form_code
                else ""
            )[:140],
            "sk_nace": (
                SK_NACE_MAP.get(sk_nace_code, f"SK NACE {sk_nace_code}")
                if sk_nace_code
                else ""
            )[:140],
            "established_on": str(uj_data.get("datumZalozenia") or "")[:140],
            "register_uz_url": public_url[:140],
            "revenue": 0.0,
            "revenue_year": None,
            "profit": None,
            "assets": None,
            "equity": None,
        }

        zavierky = sorted(uj_data.get("idUctovnychZavierok") or [], reverse=True)
        for z_id in zavierky[:3]:
            z_data = ruz_http_get(
                session, f"{BASE_URL}/uctovna-zavierka?id={z_id}", headers
            )
            if not z_data:
                continue
            st_date = str(
                z_data.get("datumZostaveniaK")
                or z_data.get("obdobieDo")
                or z_data.get("datumPodania")
                or ""
            )
            year = (
                int(st_date[:4])
                if len(st_date) >= 4 and st_date[:4].isdigit()
                else None
            )
            vykazy = z_data.get("idUctovnychVykazov") or []
            found_table = False

            for v_id in vykazy:
                v_data = ruz_http_get(
                    session, f"{BASE_URL}/uctovny-vykaz?id={v_id}", headers
                )
                if not v_data:
                    continue
                tabulky = (v_data.get("obsah") or {}).get("tabulky") or []
                for t in tabulky:
                    t_name = str((t.get("nazov") or {}).get("sk") or "").lower()
                    data = t.get("data") or []

                    if "zisk" in t_name or "výkaz" in t_name or "výnos" in t_name:
                        found_table = True
                        parsed_rev = None
                        parsed_prof = None
                        if len(data) >= 100:
                            op_rev = parse_num(data[2])
                            fin_rev = parse_num(data[56])
                            net_turn = parse_num(data[0])
                            if op_rev is not None:
                                parsed_rev = op_rev + (
                                    fin_rev if (fin_rev and fin_rev > 0) else 0.0
                                )
                            elif net_turn is not None:
                                parsed_rev = net_turn
                            else:
                                s = (
                                    (parse_num(data[4]) or 0.0)
                                    + (parse_num(data[6]) or 0.0)
                                    + (parse_num(data[8]) or 0.0)
                                )
                                if s > 0:
                                    parsed_rev = s
                            if len(data) > 120:
                                parsed_prof = parse_num(data[120])
                        elif len(data) >= 75:
                            op_rev = parse_num(data[0])
                            fin_rev = parse_num(data[38])
                            if op_rev is not None:
                                parsed_rev = op_rev + (
                                    fin_rev if (fin_rev and fin_rev > 0) else 0.0
                                )
                            else:
                                s = (parse_num(data[2]) or 0.0) + (
                                    parse_num(data[4]) or 0.0
                                )
                                if s > 0:
                                    parsed_rev = s
                            if len(data) > 74:
                                parsed_prof = parse_num(data[74])

                        if parsed_prof is None and len(data) >= 2:
                            for i in range(len(data) - 2, -1, -2):
                                p = parse_num(data[i])
                                if p is not None:
                                    parsed_prof = p
                                    break

                        if parsed_rev is not None:
                            res["revenue"] = parsed_rev
                            res["revenue_year"] = year
                        if parsed_prof is not None:
                            res["profit"] = parsed_prof
                            if not res["revenue_year"]:
                                res["revenue_year"] = year

                    if "aktív" in t_name and len(data) > 0:
                        found_table = True
                        a = parse_num(data[0])
                        if a is not None:
                            res["assets"] = a

                    if "pasív" in t_name and len(data) > 2:
                        found_table = True
                        eq = parse_num(data[2])
                        if eq is not None:
                            res["equity"] = eq

            if found_table:
                break

        return raw_ico, res
    except Exception:
        return raw_ico, {"status": "error"}


def bulk_sync_all_icos(max_workers: int = 12):
    print(f"[4/4] Bulk syncing all unique IČOs with Register ÚZ (workers={max_workers})...")
    org_icos = frappe.db.sql(
        "SELECT DISTINCT custom_ico FROM `tabCRM Organization` WHERE custom_ico IS NOT NULL AND custom_ico != '' AND COALESCE(custom_ruz_sync_status, '') != 'synced'",
        pluck=True,
    )
    lead_icos = frappe.db.sql(
        "SELECT DISTINCT custom_ico FROM `tabCRM Lead` WHERE custom_ico IS NOT NULL AND custom_ico != '' AND COALESCE(custom_ruz_sync_status, '') != 'synced'",
        pluck=True,
    )
    unique_icos = sorted(set(org_icos) | set(lead_icos))
    print(f"   -> Found {len(unique_icos)} unique unsynced IČOs to fetch from Register ÚZ.")
    if not unique_icos:
        return

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=max_workers * 2, pool_maxsize=max_workers * 2
    )
    session.mount("https://", adapter)

    now_str = str(now_datetime())
    synced_count = 0
    not_found_count = 0
    error_count = 0
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_ico_financials, ico, session): ico
            for ico in unique_icos
        }
        for future in as_completed(futures):
            done += 1
            raw_ico, data = future.result()
            status = data.get("status", "error")

            if status == "synced":
                synced_count += 1
                params = {
                    "rev": data.get("revenue") or 0.0,
                    "rev_year": data.get("revenue_year") or 0,
                    "profit": data.get("profit") or 0.0,
                    "assets": data.get("assets") or 0.0,
                    "equity": data.get("equity") or 0.0,
                    "dic": data.get("dic") or "",
                    "legal_form": data.get("legal_form") or "",
                    "sk_nace": data.get("sk_nace") or "",
                    "est_on": data.get("established_on") or "",
                    "ruz_url": data.get("register_uz_url") or "",
                    "status": "synced",
                    "now": now_str,
                    "ico": raw_ico,
                }
                frappe.db.sql(
                    """
                    UPDATE `tabCRM Organization`
                    SET annual_revenue = %(rev)s,
                        custom_revenue_year = %(rev_year)s,
                        custom_profit = %(profit)s,
                        custom_assets = %(assets)s,
                        custom_equity = %(equity)s,
                        custom_dic = %(dic)s,
                        custom_legal_form = %(legal_form)s,
                        custom_sk_nace = %(sk_nace)s,
                        custom_established_on = %(est_on)s,
                        custom_register_uz_url = %(ruz_url)s,
                        custom_ruz_sync_status = %(status)s,
                        custom_ruz_synced_at = %(now)s
                    WHERE custom_ico = %(ico)s
                    """,
                    params,
                )
                frappe.db.sql(
                    """
                    UPDATE `tabCRM Lead`
                    SET annual_revenue = %(rev)s,
                        custom_revenue_year = %(rev_year)s,
                        custom_profit = %(profit)s,
                        custom_assets = %(assets)s,
                        custom_equity = %(equity)s,
                        custom_dic = %(dic)s,
                        custom_legal_form = %(legal_form)s,
                        custom_sk_nace = %(sk_nace)s,
                        custom_established_on = %(est_on)s,
                        custom_register_uz_url = %(ruz_url)s,
                        custom_ruz_sync_status = %(status)s,
                        custom_ruz_synced_at = %(now)s
                    WHERE custom_ico = %(ico)s
                    """,
                    params,
                )
            else:
                if status == "not_found":
                    not_found_count += 1
                else:
                    error_count += 1
                frappe.db.sql(
                    """
                    UPDATE `tabCRM Organization`
                    SET custom_ruz_sync_status = %(status)s,
                        custom_ruz_synced_at = %(now)s
                    WHERE custom_ico = %(ico)s
                    """,
                    {"status": status, "now": now_str, "ico": raw_ico},
                )
                frappe.db.sql(
                    """
                    UPDATE `tabCRM Lead`
                    SET custom_ruz_sync_status = %(status)s,
                        custom_ruz_synced_at = %(now)s
                    WHERE custom_ico = %(ico)s
                    """,
                    {"status": status, "now": now_str, "ico": raw_ico},
                )

            if done % 200 == 0 or done == len(unique_icos):
                frappe.db.commit()
                print(
                    f"   -> Processed {done}/{len(unique_icos)} IČOs "
                    f"(synced={synced_count}, not_found={not_found_count}, error={error_count})..."
                )

    frappe.db.commit()
    frappe.clear_cache()
    print("=== Register ÚZ Sync Complete! ===")
    print(f"Total Synced IČOs:    {synced_count}")
    print(f"Total Not Found IČOs: {not_found_count}")
    print(f"Total Error IČOs:     {error_count}")


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else "menucka.local"
    setup_only = "--setup-only" in sys.argv

    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()

    setup_financial_custom_fields()
    setup_financial_field_layouts()
    setup_ruz_server_and_form_scripts()

    if not setup_only:
        bulk_sync_all_icos(max_workers=2)


if __name__ == "__main__":
    main()
