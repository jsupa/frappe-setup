#!/usr/bin/env python3
"""
Migrate Slovakia Restaurant Dataset (unikatne_restauracie_komplet_full.csv) into Frappe CRM.
Creates:
1. Custom Fields on CRM Organization, Contact, and CRM Lead
2. CRM Fields Layout updates so custom fields appear in Frappe CRM UI
3. Custom CRM Lead Statuses: In Check -> In Contact -> Contacted -> Replied -> Deal / Ignored
4. CRM Organizations & Contacts for unique restaurant Owners (grouped by ICO / Company)
5. CRM Leads for all restaurants in the first status ("In Check")
"""

import csv
import json
import re
import sys
from urllib.parse import urlparse

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import now_datetime, validate_email_address, validate_phone_number


EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def clean_email(val: str) -> str:
    if not val:
        return ""
    # Take first email if multiple separated by comma/semicolon
    first = re.split(r"[,;\s]+", val.strip())[0].lower()
    if EMAIL_REGEX.match(first) and validate_email_address(first, throw=False):
        return first[:140]
    return ""


def clean_phone(val: str) -> str:
    if not val:
        return ""
    cleaned = re.sub(r"^['\"]+|['\"]+$", "", val.strip())
    # If multiple phones separated by comma/slash/semicolon, take first
    cleaned = re.split(r"[,;/|]+", cleaned)[0].strip()
    cleaned = re.sub(r"[^0-9+\s\-().]", "", cleaned).strip()
    # Handle concatenated Slovak phone numbers like +421917235584421915033072
    digits_plus = re.sub(r"[\s\-().]", "", cleaned)
    m_sk = re.match(r"^(\+421\d{9})", digits_plus)
    if m_sk:
        cleaned = m_sk.group(1)
    elif re.match(r"^(421\d{9})", digits_plus):
        cleaned = "+" + re.match(r"^(421\d{9})", digits_plus).group(1)
    elif re.match(r"^(0\d{9})", digits_plus):
        cleaned = "+421" + digits_plus[1:10]
    if len(cleaned) < 5:
        return ""
    if not validate_phone_number(cleaned, throw=False):
        return ""
    return cleaned[:140]


def clean_url(val: str) -> str:
    if not val:
        return ""
    trimmed = val.strip()
    if not trimmed:
        return ""
    if not re.match(r"^https?://", trimmed, re.I):
        trimmed = "https://" + trimmed
    try:
        parsed = urlparse(trimmed)
        if not parsed.netloc or "." not in parsed.netloc:
            return ""
        return trimmed[:140]
    except Exception:
        return ""


def derive_fallback_name(row: dict) -> str:
    url = (
        row.get("Bistro_URL")
        or row.get("Bolt_URL")
        or row.get("SME_URL")
        or row.get("Menucka_URL")
        or ""
    ).strip()
    if not url:
        return "Neznáma reštaurácia"
    parts = [p for p in url.split("/") if p]
    last_slug = parts[-1] if parts else ""
    cleaned = re.sub(r"^[0-9]+-", "", last_slug)
    cleaned = re.sub(r"-[0-9]+$", "", cleaned)
    cleaned = cleaned.replace("-", " ")
    return " ".join(w.capitalize() for w in cleaned.split() if w) or "Neznáma reštaurácia"


def calculate_tier(rating_count: int) -> str:
    if rating_count >= 500:
        return "Tier 1 (500+)"
    if rating_count >= 100:
        return "Tier 2 (100-499)"
    return "Tier 3 (<100)"


def setup_custom_fields():
    print("[1/6] Setting up Custom Fields on CRM Organization, Contact, and CRM Lead...")
    custom_fields = {
        "CRM Organization": [
            {
                "fieldname": "custom_ico",
                "label": "IČO",
                "fieldtype": "Data",
                "insert_after": "organization_name",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_establishments_count",
                "label": "Establishments Count",
                "fieldtype": "Int",
                "insert_after": "custom_ico",
                "in_list_view": 1,
            },
            {
                "fieldname": "custom_city",
                "label": "City / Mesto",
                "fieldtype": "Data",
                "insert_after": "territory",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_address_text",
                "label": "Address / Adresa",
                "fieldtype": "Data",
                "insert_after": "custom_city",
            },
            {
                "fieldname": "custom_phone",
                "label": "Phone",
                "fieldtype": "Data",
                "options": "Phone",
                "insert_after": "custom_address_text",
            },
            {
                "fieldname": "custom_email",
                "label": "Email",
                "fieldtype": "Data",
                "options": "Email",
                "insert_after": "custom_phone",
            },
            {
                "fieldname": "custom_restaurant_names",
                "label": "Restaurants / Prevádzky",
                "fieldtype": "Small Text",
                "insert_after": "company_description",
            },
        ],
        "Contact": [
            {
                "fieldname": "custom_ico",
                "label": "IČO",
                "fieldtype": "Data",
                "insert_after": "company_name",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_organization",
                "label": "CRM Organization",
                "fieldtype": "Link",
                "options": "CRM Organization",
                "insert_after": "custom_ico",
            },
            {
                "fieldname": "custom_establishments_count",
                "label": "Establishments Count",
                "fieldtype": "Int",
                "insert_after": "custom_organization",
            },
            {
                "fieldname": "custom_city",
                "label": "City / Mesto",
                "fieldtype": "Data",
                "insert_after": "address",
                "in_list_view": 1,
            },
            {
                "fieldname": "custom_address_text",
                "label": "Address / Adresa",
                "fieldtype": "Data",
                "insert_after": "custom_city",
            },
            {
                "fieldname": "custom_website",
                "label": "Website",
                "fieldtype": "Data",
                "insert_after": "custom_address_text",
            },
        ],
        "CRM Lead": [
            {
                "fieldname": "custom_ico",
                "label": "IČO",
                "fieldtype": "Data",
                "insert_after": "organization",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_company_name",
                "label": "Legal Company / Právnická osoba",
                "fieldtype": "Data",
                "insert_after": "custom_ico",
            },
            {
                "fieldname": "custom_organization_link",
                "label": "Owner Organization",
                "fieldtype": "Link",
                "options": "CRM Organization",
                "insert_after": "custom_company_name",
            },
            {
                "fieldname": "custom_contact_link",
                "label": "Owner Contact",
                "fieldtype": "Link",
                "options": "Contact",
                "insert_after": "custom_organization_link",
            },
            {
                "fieldname": "custom_city",
                "label": "City / Mesto",
                "fieldtype": "Data",
                "insert_after": "territory",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_address",
                "label": "Address / Adresa",
                "fieldtype": "Data",
                "insert_after": "custom_city",
            },
            {
                "fieldname": "custom_has_daily_menu",
                "label": "Has Daily Menu",
                "fieldtype": "Select",
                "options": "\nÁNO\nNIE\nNEAKTÍVNE / NENÁJDENÉ",
                "insert_after": "website",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_daily_menu_url",
                "label": "Daily Menu URL",
                "fieldtype": "Data",
                "insert_after": "custom_has_daily_menu",
            },
            {
                "fieldname": "custom_verification_reason",
                "label": "Verification Reason / Dôvod overenia",
                "fieldtype": "Small Text",
                "insert_after": "custom_daily_menu_url",
            },
            {
                "fieldname": "custom_tier",
                "label": "Tier",
                "fieldtype": "Select",
                "options": "\nTier 1 (500+)\nTier 2 (100-499)\nTier 3 (<100)",
                "insert_after": "lead_temperature",
                "in_list_view": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": "custom_rating",
                "label": "Rating",
                "fieldtype": "Float",
                "insert_after": "custom_tier",
                "in_list_view": 1,
            },
            {
                "fieldname": "custom_reviews_count",
                "label": "Reviews Count",
                "fieldtype": "Int",
                "insert_after": "custom_rating",
            },
            {
                "fieldname": "custom_sources",
                "label": "Sources / Zdroje",
                "fieldtype": "Data",
                "insert_after": "source",
            },
            {
                "fieldname": "custom_sme_url",
                "label": "SME Reštaurácie URL",
                "fieldtype": "Data",
                "insert_after": "custom_sources",
            },
            {
                "fieldname": "custom_bistro_url",
                "label": "Bistro.sk URL",
                "fieldtype": "Data",
                "insert_after": "custom_sme_url",
            },
            {
                "fieldname": "custom_bolt_url",
                "label": "Bolt Food URL",
                "fieldtype": "Data",
                "insert_after": "custom_bistro_url",
            },
            {
                "fieldname": "custom_menucka_url",
                "label": "Menúčka.sk URL",
                "fieldtype": "Data",
                "insert_after": "custom_bolt_url",
            },
            {
                "fieldname": "custom_gps",
                "label": "GPS Coordinates",
                "fieldtype": "Data",
                "insert_after": "custom_menucka_url",
            },
            {
                "fieldname": "custom_delivery_available",
                "label": "Delivery Available",
                "fieldtype": "Check",
                "insert_after": "custom_gps",
            },
            {
                "fieldname": "custom_takeaway_available",
                "label": "Takeaway Available",
                "fieldtype": "Check",
                "insert_after": "custom_delivery_available",
            },
            {
                "fieldname": "custom_buying_signals",
                "label": "Buying Signals",
                "fieldtype": "Small Text",
                "insert_after": "company_description",
            },
            {
                "fieldname": "custom_recommended_pitch",
                "label": "Recommended Pitch",
                "fieldtype": "Small Text",
                "insert_after": "custom_buying_signals",
            },
        ],
    }
    create_custom_fields(custom_fields, update=True)
    frappe.db.commit()
    print("   -> Custom fields created/updated.")


def setup_field_layouts():
    print("[2/6] Updating CRM Fields Layout for Lead, Organization, and Contact...")

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
                        "custom_establishments_count",
                        "custom_city",
                        "custom_address_text",
                        "custom_phone",
                        "custom_email",
                        "website",
                        "territory",
                        "industry",
                        "annual_revenue",
                        "custom_restaurant_names",
                        "company_description",
                    ],
                }
            ],
        }
    ]

    contact_side_panel = [
        {
            "label": "Contact & Company Details",
            "name": "details_section",
            "opened": True,
            "columns": [
                {
                    "name": "column_eIWl",
                    "fields": [
                        "first_name",
                        "last_name",
                        "company_name",
                        "custom_organization",
                        "custom_ico",
                        "custom_establishments_count",
                        "email_id",
                        "mobile_no",
                        "phone",
                        "custom_city",
                        "custom_address_text",
                        "custom_website",
                    ],
                }
            ],
        }
    ]

    layouts = {
        "CRM Lead-Side Panel": lead_side_panel,
        "CRM Lead-Data Fields": lead_data_fields,
        "CRM Organization-Side Panel": org_side_panel,
        "Contact-Side Panel": contact_side_panel,
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
    print("   -> CRM Fields Layout updated.")


def setup_lead_statuses():
    print("[3/6] Setting up custom CRM Lead Statuses...")
    desired_statuses = [
        {"lead_status": "In Check", "type": "Open", "color": "gray", "position": 1},
        {"lead_status": "In Contact", "type": "Ongoing", "color": "blue", "position": 2},
        {"lead_status": "Contacted", "type": "Ongoing", "color": "yellow", "position": 3},
        {"lead_status": "Replied", "type": "Ongoing", "color": "purple", "position": 4},
        {"lead_status": "Deal", "type": "Won", "color": "green", "position": 5},
        {"lead_status": "Ignored", "type": "Lost", "color": "orange", "position": 6},
    ]

    for s in desired_statuses:
        if frappe.db.exists("CRM Lead Status", s["lead_status"]):
            frappe.db.set_value(
                "CRM Lead Status",
                s["lead_status"],
                {
                    "type": s["type"],
                    "color": s["color"],
                    "position": s["position"],
                },
            )
        else:
            doc = frappe.get_doc({"doctype": "CRM Lead Status", **s})
            doc.insert(ignore_permissions=True)

    # Point any existing leads (if any) to 'In Check' before removing default unused statuses
    frappe.db.sql("UPDATE `tabCRM Lead` SET status = 'In Check' WHERE status NOT IN ('In Check', 'In Contact', 'Contacted', 'Replied', 'Deal', 'Ignored')")

    desired_names = {s["lead_status"] for s in desired_statuses}
    existing = frappe.get_all("CRM Lead Status", pluck="name")
    for name in existing:
        if name not in desired_names:
            frappe.delete_doc("CRM Lead Status", name, ignore_permissions=True, force=True)

    frappe.db.commit()
    print("   -> CRM Lead Statuses configured:", [s["lead_status"] for s in desired_statuses])


def ensure_master_data(cities: set, sources: set):
    print("[4/6] Ensuring CRM Industry, Sources, and Territories...")
    if not frappe.db.exists("CRM Industry", "Restaurants & Hospitality"):
        frappe.get_doc(
            {"doctype": "CRM Industry", "industry": "Restaurants & Hospitality"}
        ).insert(ignore_permissions=True)

    for src in sources:
        if src and not frappe.db.exists("CRM Lead Source", src):
            frappe.get_doc({"doctype": "CRM Lead Source", "source_name": src}).insert(
                ignore_permissions=True
            )

    for city in cities:
        if city and not frappe.db.exists("CRM Territory", city):
            frappe.get_doc({"doctype": "CRM Territory", "territory_name": city}).insert(
                ignore_permissions=True
            )

    frappe.db.commit()
    print(f"   -> Created/verified {len(cities)} Territories and {len(sources)} Sources.")


def migrate_data(csv_path: str):
    # Disable auto web enrichment background tasks during bulk import
    import crm.domain_enrichment.tasks as enrichment_tasks

    enrichment_tasks.auto_enrich_on_create = lambda *args, **kwargs: None
    frappe.flags.in_import = True
    frappe.flags.ignore_emails = True

    with open(csv_path, encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    print(f"Loaded {len(raw_rows)} rows from {csv_path}.")

    # Clean previous imported leads/orgs/contacts if re-running so it's idempotent
    print("Cleaning any previous CRM Lead / CRM Organization / Contact records for fresh import...")
    frappe.db.sql("DELETE FROM `tabCRM Lead`")
    frappe.db.sql("DELETE FROM `tabCRM Status Change Log`")
    frappe.db.sql("DELETE FROM `tabCRM Organization`")
    frappe.db.sql("DELETE FROM `tabContact` WHERE user IS NULL OR user = ''")
    frappe.db.sql("DELETE FROM `tabContact Email` WHERE parent NOT IN (SELECT name FROM `tabContact`)")
    frappe.db.sql("DELETE FROM `tabContact Phone` WHERE parent NOT IN (SELECT name FROM `tabContact`)")
    frappe.db.sql("DELETE FROM `tabDynamic Link` WHERE parenttype = 'Contact' AND parent NOT IN (SELECT name FROM `tabContact`)")
    frappe.db.commit()

    # Pass 1: Collect owners, cities, sources
    owners_map = {}
    cities_set = set()
    sources_set = {"Scraper", "Multi-Source"}
    processed_rows = []

    for r in raw_rows:
        name = (r.get("Nazov") or "").strip() or derive_fallback_name(r)
        city = (r.get("Mesto") or "").strip()[:140]
        address = (r.get("Adresa") or "").strip()[:140]
        ico = re.sub(r"['\"]", "", (r.get("ICO") or "").strip())[:140]
        company = re.sub(r"['\"]", "", (r.get("Company") or "").strip())[:140]
        phone = clean_phone(r.get("Telefon") or "")
        email = clean_email(r.get("Email") or "")
        website = clean_url(r.get("Webova_stranka") or "")
        daily_menu_url = (r.get("URL_denneho_menu") or "").strip()[:140]
        has_menu_raw = (r.get("Ma_100p_denne_menu") or "").strip()
        if has_menu_raw == "ÁNO":
            has_daily_menu = "ÁNO"
        elif has_menu_raw == "NIE":
            has_daily_menu = "NIE"
        else:
            has_daily_menu = "NEAKTÍVNE / NENÁJDENÉ"

        raw_sources = [s.strip() for s in (r.get("Zdroj") or "").split(",") if s.strip()]
        primary_source = (
            "Multi-Source"
            if len(raw_sources) > 1
            else (raw_sources[0] if raw_sources else "Scraper")
        )
        sources_set.add(primary_source)
        if city:
            cities_set.add(city)

        try:
            rating = float(r.get("Rating_Exact") or r.get("Rating") or 0)
        except ValueError:
            rating = 0.0

        try:
            reviews_count = int(float(r.get("Rating_Count") or 0))
        except ValueError:
            reviews_count = 0

        tier = calculate_tier(reviews_count)

        # Owner grouping key (ICO prioritized, otherwise normalized legal Company name)
        owner_key = ico if len(ico) >= 6 else (company.lower() if company else "")

        if owner_key:
            if owner_key not in owners_map:
                owners_map[owner_key] = {
                    "key": owner_key,
                    "ico": ico,
                    "company": company or f"Firma IČO {ico}",
                    "city": city,
                    "address": address,
                    "phone": phone,
                    "email": email,
                    "website": website or clean_url(daily_menu_url),
                    "venues": [name],
                }
            else:
                o = owners_map[owner_key]
                o["venues"].append(name)
                if not o["company"] and company:
                    o["company"] = company
                if not o["phone"] and phone:
                    o["phone"] = phone
                if not o["email"] and email:
                    o["email"] = email
                if not o["website"] and website:
                    o["website"] = website
                if not o["city"] and city:
                    o["city"] = city
                if not o["address"] and address:
                    o["address"] = address

        processed_rows.append(
            {
                "name": name[:140],
                "city": city,
                "address": address,
                "ico": ico,
                "company": company,
                "owner_key": owner_key,
                "phone": phone,
                "email": email,
                "website": website or clean_url(daily_menu_url),
                "daily_menu_url": daily_menu_url,
                "has_daily_menu": has_daily_menu,
                "verification_reason": (r.get("Dovod_overenia") or "").strip(),
                "sources_str": ", ".join(raw_sources)[:140],
                "primary_source": primary_source,
                "rating": rating,
                "reviews_count": reviews_count,
                "tier": tier,
                "lat": (r.get("Latitude") or "").strip(),
                "lng": (r.get("Longitude") or "").strip(),
                "delivery": 1 if (r.get("Delivery_Available") or "").lower() == "true" else 0,
                "takeaway": 1 if (r.get("Takeaway_Available") or "").lower() == "true" else 0,
                "sme_url": (r.get("SME_URL") or "").strip()[:140],
                "bistro_url": (r.get("Bistro_URL") or "").strip()[:140],
                "bolt_url": (r.get("Bolt_URL") or "").strip()[:140],
                "menucka_url": (r.get("Menucka_URL") or "").strip()[:140],
            }
        )

    ensure_master_data(cities_set, sources_set)

    # Pass 2: Insert CRM Organizations and Contacts for Owners
    print(f"[5/6] Creating {len(owners_map)} CRM Organizations & Owner Contacts...")
    owner_key_to_org_name = {}
    owner_key_to_contact_name = {}
    used_org_names = set()

    now = now_datetime()
    idx = 0
    for owner_key, o in owners_map.items():
        idx += 1
        base_org_name = o["company"].strip()[:60] or f"Firma IČO {o['ico']}"
        org_name = base_org_name
        if org_name.lower() in used_org_names:
            org_name = f"{base_org_name[:45]} ({o['ico'][:12] or idx})"
        used_org_names.add(org_name.lower())

        venues_count = len(o["venues"])
        venues_summary = ", ".join(o["venues"])[:1000]

        org_doc = frappe.get_doc(
            {
                "doctype": "CRM Organization",
                "organization_name": org_name,
                "custom_ico": o["ico"],
                "custom_establishments_count": venues_count,
                "custom_city": o["city"],
                "custom_address_text": o["address"],
                "custom_phone": o["phone"],
                "custom_email": o["email"],
                "custom_restaurant_names": venues_summary,
                "website": o["website"],
                "territory": o["city"] if o["city"] in cities_set else None,
                "industry": "Restaurants & Hospitality",
                "currency": "EUR",
                "exchange_rate": 1.0,
                "company_description": f"Firma: {o['company']} | IČO: {o['ico'] or 'N/A'} | Počet prevádzok: {venues_count} ({venues_summary[:200]})",
            }
        )
        org_doc.insert(ignore_permissions=True, ignore_links=True)
        owner_key_to_org_name[owner_key] = org_doc.name

        # Create corresponding Contact linked to CRM Organization
        # Keep first_name + last_name + org_name under 140 chars for Contact.autoname()
        contact_first_name = base_org_name[:45]
        contact_last_name = f"(IČO {o['ico'][:12]})" if o["ico"] else ""
        contact_full_name = f"{contact_first_name} {contact_last_name}".strip()[:65]

        contact_doc = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": contact_first_name,
                "last_name": contact_last_name,
                "full_name": contact_full_name,
                "company_name": org_doc.name,
                "custom_organization": org_doc.name,
                "custom_ico": o["ico"],
                "custom_establishments_count": venues_count,
                "custom_city": o["city"],
                "custom_address_text": o["address"],
                "custom_website": o["website"],
                "status": "Open",
            }
        )
        if o["email"]:
            contact_doc.append("email_ids", {"email_id": o["email"], "is_primary": 1})
        if o["phone"]:
            contact_doc.append(
                "phone_nos",
                {"phone": o["phone"], "is_primary_phone": 1, "is_primary_mobile_no": 1},
            )
        contact_doc.append(
            "links",
            {"link_doctype": "CRM Organization", "link_name": org_doc.name},
        )
        try:
            contact_doc.insert(ignore_permissions=True, ignore_links=True)
            owner_key_to_contact_name[owner_key] = contact_doc.name
        except Exception:
            # Fallback if duplicate contact name
            contact_doc.first_name = f"{contact_first_name[:35]} #{idx}"
            contact_doc.insert(ignore_permissions=True, ignore_links=True)
            owner_key_to_contact_name[owner_key] = contact_doc.name

        if idx % 500 == 0:
            frappe.db.commit()
            print(f"   -> Created {idx}/{len(owners_map)} organizations & contacts...")

    frappe.db.commit()
    print(f"   -> Finished creating {len(owners_map)} CRM Organizations & Contacts.")

    # Pass 3: Insert all Restaurants as CRM Leads in status 'In Check'
    print(f"[6/6] Creating {len(processed_rows)} CRM Leads in status 'In Check'...")
    lead_idx = 0
    for r in processed_rows:
        lead_idx += 1
        owner_key = r["owner_key"]
        org_name = owner_key_to_org_name.get(owner_key)
        contact_name = owner_key_to_contact_name.get(owner_key)
        venues_count = len(owners_map[owner_key]["venues"]) if owner_key in owners_map else 1

        # Build Buying Signals & Recommended Pitch (matching seed.ts)
        signals = []
        pitch = ""
        if r["bolt_url"] or r["bistro_url"]:
            signals.append("Platí provízie ~25-30% agregátorom (Bistro/Bolt)")
            pitch = "Prevádzka je na Bistro/Bolt. Kľúčový pitch: Vlastný objednávkový systém a propagácia denného menu bez 30% provízií."
        if r["has_daily_menu"] == "ÁNO":
            signals.append("100% overené denné obedové menu")
            if not pitch:
                pitch = "Aktívny obedový koncept. Kľúčový pitch: Automatické publikovanie denného menu za 14 € / mesiac."
        if venues_count > 1:
            signals.append(f"Gastro skupina ({venues_count} podnikov)")
            pitch = (pitch + f" Majiteľ prevádzkuje sieť {venues_count} podnikov pod jedným IČO.").strip()

        if r["has_daily_menu"] == "ÁNO" and r["tier"] == "Tier 1 (500+)":
            temp = "Hot"
        elif r["has_daily_menu"] == "ÁNO":
            temp = "Warm"
        else:
            temp = "Cold"

        gps = f"{r['lat']}, {r['lng']}" if (r["lat"] and r["lng"]) else ""

        lead_doc = frappe.get_doc(
            {
                "doctype": "CRM Lead",
                "naming_series": "CRM-LEAD-.YYYY.-",
                "first_name": r["name"],
                "lead_name": r["name"],
                "organization": org_name or r["company"] or r["name"],
                "status": "In Check",
                "email": r["email"] or None,
                "mobile_no": r["phone"] or None,
                "phone": r["phone"] or None,
                "website": r["website"] or None,
                "territory": r["city"] if r["city"] in cities_set else None,
                "industry": "Restaurants & Hospitality",
                "source": r["primary_source"],
                "lead_temperature": temp,
                "lead_score": r["reviews_count"],
                "company_description": pitch or r["verification_reason"],
                "custom_ico": r["ico"],
                "custom_company_name": r["company"],
                "custom_organization_link": org_name,
                "custom_contact_link": contact_name,
                "custom_city": r["city"],
                "custom_address": r["address"],
                "custom_has_daily_menu": r["has_daily_menu"],
                "custom_daily_menu_url": r["daily_menu_url"],
                "custom_verification_reason": r["verification_reason"],
                "custom_tier": r["tier"],
                "custom_rating": r["rating"],
                "custom_reviews_count": r["reviews_count"],
                "custom_sources": r["sources_str"],
                "custom_sme_url": r["sme_url"],
                "custom_bistro_url": r["bistro_url"],
                "custom_bolt_url": r["bolt_url"],
                "custom_menucka_url": r["menucka_url"],
                "custom_gps": gps,
                "custom_delivery_available": r["delivery"],
                "custom_takeaway_available": r["takeaway"],
                "custom_buying_signals": "; ".join(signals),
                "custom_recommended_pitch": pitch,
            }
        )
        lead_doc.flags.ignore_mandatory = True
        lead_doc.flags.ignore_email_validation = True
        lead_doc.flags.ignore_permissions = True
        lead_doc.flags.ignore_links = True
        lead_doc.insert(ignore_permissions=True, ignore_links=True, ignore_mandatory=True)

        if lead_idx % 500 == 0:
            frappe.db.commit()
            print(f"   -> Created {lead_idx}/{len(processed_rows)} leads...")

    frappe.db.commit()
    create_lead_tasks()
    frappe.clear_cache()
    print("=== Migration Complete! ===")
    print(f"Total CRM Organizations: {frappe.db.count('CRM Organization')}")
    print(f"Total Contacts:          {frappe.db.count('Contact')}")
    print(f"Total CRM Leads:         {frappe.db.count('CRM Lead')}")
    print(f"Total CRM Tasks:         {frappe.db.count('CRM Task')}")


def create_lead_tasks():
    from frappe.database.sequence import set_next_val
    from frappe.utils import today

    print("[7/7] Creating 4 Sales Playbook Tasks (Research, Initial Contact, Follow-up, Deal Closing) for every CRM Lead...")
    frappe.db.sql("DELETE FROM `tabCRM Task` WHERE reference_doctype = 'CRM Lead'")
    frappe.db.commit()

    leads = frappe.get_all(
        "CRM Lead",
        fields=[
            "name",
            "lead_name",
            "organization",
            "email",
            "mobile_no",
            "website",
            "lead_temperature",
            "custom_ico",
            "custom_company_name",
            "custom_city",
            "custom_has_daily_menu",
            "custom_daily_menu_url",
            "custom_tier",
            "custom_sources",
            "custom_buying_signals",
            "custom_recommended_pitch",
        ],
        order_by="name asc",
    )

    max_existing = frappe.db.sql("SELECT COALESCE(MAX(name), 0) FROM `tabCRM Task`")[0][0] or 0
    next_id = int(max_existing)
    now_str = str(now_datetime())
    today_str = str(today())

    fields = [
        "name",
        "creation",
        "modified",
        "modified_by",
        "owner",
        "docstatus",
        "idx",
        "title",
        "priority",
        "start_date",
        "reference_doctype",
        "reference_docname",
        "status",
        "description",
    ]

    batch = []
    for lead in leads:
        tier = lead.get("custom_tier") or ""
        temp = lead.get("lead_temperature") or ""
        if tier == "Tier 1 (500+)" or temp == "Hot":
            priority = "High"
        elif tier == "Tier 2 (100-499)" or temp == "Warm":
            priority = "Medium"
        else:
            priority = "Low"

        r_name = (lead.get("lead_name") or lead.get("name"))[:85]
        ico_info = f"{lead.get('custom_company_name') or lead.get('organization') or 'N/A'} (IČO: {lead.get('custom_ico') or 'N/A'})"
        menu_info = f"Stav denného menu: {lead.get('custom_has_daily_menu') or 'N/A'} | URL: {lead.get('custom_daily_menu_url') or lead.get('website') or 'N/A'}"
        contact_info = f"Tel: {lead.get('mobile_no') or 'N/A'} | Email: {lead.get('email') or 'N/A'} | Mesto: {lead.get('custom_city') or 'N/A'}"
        pitch_info = lead.get("custom_recommended_pitch") or "Štandardná ponuka automatického publikovania denného menu a vlastného objednávkového systému."
        signals_info = lead.get("custom_buying_signals") or "Overiť potenciál prevádzky."

        tasks_spec = [
            (
                f"1. Research (Prieskum & overenie): {r_name}"[:140],
                "Todo",
                (
                    f"<p><strong>1. Prieskum a kvalifikácia prevádzky ({r_name})</strong></p>"
                    f"<ul>"
                    f"<li>Skontrolovať web a denné menu — {menu_info}</li>"
                    f"<li>Overiť majiteľa / konateľa na Finstat / ORSR — {ico_info}</li>"
                    f"<li>Skontrolovať kontaktné údaje — {contact_info}</li>"
                    f"<li>Zdroje / portály: {lead.get('custom_sources') or 'N/A'}</li>"
                    f"</ul>"
                ),
            ),
            (
                f"2. Initial Contact (Prvé oslovenie): {r_name}"[:140],
                "Backlog",
                (
                    f"<p><strong>2. Prvé oslovenie majiteľa / prevádzky ({r_name})</strong></p>"
                    f"<ul>"
                    f"<li>Kontaktovať telefonicky alebo e-mailom ({contact_info})</li>"
                    f"<li><strong>Odporúčaný pitch:</strong> {pitch_info}</li>"
                    f"<li><strong>Nákupné signály:</strong> {signals_info}</li>"
                    f"<li>Po oslovení posunúť stav leadu do <em>In Contact</em> / <em>Contacted</em></li>"
                    f"</ul>"
                ),
            ),
            (
                f"3. Follow-up (Pripomenutie / reakcia): {r_name}"[:140],
                "Backlog",
                (
                    f"<p><strong>3. Follow-up a vyhodnotenie reakcie ({r_name})</strong></p>"
                    f"<ul>"
                    f"<li>Ak bez odpovede do 3 pracovných dní, urobiť telefonický/e-mailový follow-up</li>"
                    f"<li>Zaslať ukážku riešenia / kalkuláciu úspory provízií oproti agregátorom</li>"
                    f"<li>Pri odpovedi posunúť stav leadu do <em>Replied</em></li>"
                    f"</ul>"
                ),
            ),
            (
                f"4. Deal Closing (Uzavretie dohody): {r_name}"[:140],
                "Backlog",
                (
                    f"<p><strong>4. Uzavretie dohody a onboarding ({r_name})</strong></p>"
                    f"<ul>"
                    f"<li>Potvrdiť podmienky spolupráce a fakturačné údaje ({ico_info})</li>"
                    f"<li>Nastaviť automatickú synchronizáciu denného menu ({lead.get('custom_daily_menu_url') or 'N/A'})</li>"
                    f"<li>Prepnúť stav leadu na <em>Deal</em> (alebo <em>Ignored</em> pri nezáujme)</li>"
                    f"</ul>"
                ),
            ),
        ]

        for idx_num, (title, status, desc) in enumerate(tasks_spec, start=1):
            next_id += 1
            batch.append(
                (
                    next_id,
                    now_str,
                    now_str,
                    "Administrator",
                    "Administrator",
                    0,
                    idx_num,
                    title,
                    priority,
                    today_str,
                    "CRM Lead",
                    lead["name"],
                    status,
                    desc,
                )
            )

        if len(batch) >= 2000:
            frappe.db.bulk_insert("CRM Task", fields=fields, values=batch)
            frappe.db.commit()
            batch.clear()

    if batch:
        frappe.db.bulk_insert("CRM Task", fields=fields, values=batch)
        frappe.db.commit()

    set_next_val("CRM Task", next_id + 1, is_val_used=False)
    frappe.db.commit()
    print(f"   -> Created {len(leads) * 4} CRM Tasks across {len(leads)} CRM Leads.")


def main():
    site = sys.argv[1] if len(sys.argv) > 1 else "menucka.local"
    csv_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/restaurants.csv"
    tasks_only = "--tasks-only" in sys.argv

    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()

    if tasks_only:
        create_lead_tasks()
        frappe.clear_cache()
        print(f"Total CRM Tasks: {frappe.db.count('CRM Task')}")
        return

    setup_custom_fields()
    setup_field_layouts()
    setup_lead_statuses()
    migrate_data(csv_path)


if __name__ == "__main__":
    main()
