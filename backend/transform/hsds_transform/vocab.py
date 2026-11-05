"""Vocabulary used to recognise source fields: key normalisation, table names and
field aliases. Exact HSDS names always match; aliases cover common source spellings.
Add aliases here (or pass a mapping file) when a new layout is not recognised.
"""
from __future__ import annotations

import re

_CAMEL1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL2 = re.compile(r"([a-z0-9])([A-Z])")


def norm(key) -> str:
    """'yearIncorporated' / 'Year Incorporated' / 'year-incorporated' -> 'year_incorporated'."""
    s = str(key).strip()
    s = _CAMEL2.sub(r"\1_\2", _CAMEL1.sub(r"\1_\2", s))
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()
    return s


# Source keys (normalised) whose nested value is a given HSDS table.
TABLE_KEYS: dict[str, str] = {}
for _table, _keys in {
    "organization": ["organization", "organizations", "org", "orgs", "agency", "agencies", "provider",
                     "providers", "parent_organization"],
    "service": ["service", "services", "opportunity", "opportunities", "resource", "resources"],
    "program": ["program", "programs"],
    "location": ["location", "locations", "site", "sites", "branch", "branches", "facility", "facilities"],
    "address": ["address", "addresses", "physical_address", "physical_addresses", "postal_address",
                "postal_addresses", "mailing_address", "mailing_addresses"],
    "phone": ["phone", "phones", "phone_numbers", "telephones", "telephone_numbers"],
    "contact": ["contact", "contacts", "staff"],
    "schedule": ["schedule", "schedules", "regular_schedule", "regular_schedules", "holiday_schedule",
                 "holiday_schedules", "hours", "opening_hours", "hours_of_operation"],
    "service_area": ["service_area", "service_areas", "areas_served", "coverage_areas"],
    "service_at_location": ["service_at_location", "service_at_locations", "services_at_location",
                            "services_at_locations"],
    "language": ["language", "languages", "languages_spoken"],
    "accessibility": ["accessibility", "accessibility_for_disabilities", "accessibilities"],
    "required_document": ["required_document", "required_documents", "documents", "document"],
    "cost_option": ["cost_option", "cost_options", "costs", "prices"],
    "funding": ["funding", "fundings", "funders"],
    "organization_identifier": ["organization_identifier", "organization_identifiers", "identifiers"],
    "url": ["additional_urls", "additional_websites", "urls", "links"],
    "attribute": ["attribute", "attributes"],
    "metadata": ["metadata"],
    "service_capacity": ["capacities", "capacity", "service_capacity"],
    "unit": ["unit_detail"],
    "taxonomy_term": ["taxonomy_term", "taxonomy_terms"],
}.items():
    for _k in _keys:
        TABLE_KEYS[_k] = _table

# Address type implied by the key the address was found under.
ADDRESS_TYPE_FROM_KEY = {
    "physical_address": "physical", "physical_addresses": "physical",
    "postal_address": "postal", "postal_addresses": "postal",
    "mailing_address": "postal", "mailing_addresses": "postal",
}

# Keys whose values are classification terms: become taxonomy_term + attribute rows.
TAXONOMY_KEYS = {"taxonomy", "taxonomies", "categories", "category", "tags", "tag", "keywords",
                 "areas", "subjects", "service_types", "service_type", "topics", "needs"}

# Dict wrappers whose contents describe the parent itself (flattened into it).
FLATTEN_KEYS = {"meta", "properties", "details", "info", "attributes_map", "fees", "eligibility",
                "geo", "coordinates", "location_point", "contact_info", "contact_information"}

# Prefixes in flat column names that point to another table ("org_name", "site_city").
PREFIX_TABLES: dict[str, str] = {
    "organization": "organization", "org": "organization", "agency": "organization",
    "provider": "organization", "parent_organization": "organization",
    "service": "service", "program": "service", "opportunity": "service",
    "location": "location", "loc": "location", "site": "location", "facility": "location",
    "address": "address", "addr": "address", "physical_address": "address", "mailing_address": "address",
    "postal_address": "address", "street": "address",
    "phone": "phone", "telephone": "phone", "tel": "phone",
    "contact": "contact",
    "schedule": "schedule",
    "language": "language",
    "accessibility": "accessibility",
    "funding": "funding",
    "taxonomy_term": "taxonomy_term",
}

# Field aliases per table: normalised source key -> HSDS column.
FIELD_ALIASES: dict[str, dict[str, str]] = {
    "organization": {
        "organization_name": "name", "org_name": "name", "agency_name": "name", "agency": "name",
        "provider_name": "name", "legal_name": "name", "title": "name",
        "alternate_names": "alternate_name", "aka": "alternate_name", "also_known_as": "alternate_name",
        "dba": "alternate_name",
        "url": "website", "web": "website", "web_site": "website", "homepage": "website",
        "website_url": "website", "site_url": "website",
        "email_address": "email", "e_mail": "email", "emails": "email",
        "ein": "tax_id", "tax_number": "tax_id",
        "founded": "year_incorporated", "year_founded": "year_incorporated",
        "incorporated": "year_incorporated",
        "logo_url": "logo", "summary": "description", "about": "description", "mission": "description",
        "overview": "description",
    },
    "service": {
        "service_name": "name", "program_name": "name", "title": "name", "service_title": "name",
        "opportunity_name": "name", "resource_name": "name",
        "alternate_names": "alternate_name", "aka": "alternate_name",
        "summary": "description", "overview": "description", "service_description": "description",
        "website": "url", "web": "url", "link": "url", "service_url": "url", "action_signup_url": "url",
        "email_address": "email", "e_mail": "email", "emails": "email",
        "how_to_apply": "application_process", "intake": "application_process",
        "intake_process": "application_process", "application": "application_process",
        "fee": "fees_description", "cost": "fees_description", "costs": "fees_description",
        "cost_note": "fees_description", "fee_description": "fees_description", "price": "fees_description",
        "fees_note": "fees_description",
        "eligibility": "eligibility_description", "who_is_eligible": "eligibility_description",
        "elig_description": "eligibility_description", "eligibility_requirements": "eligibility_description",
        "eligibility_criteria": "eligibility_description",
        "min_age": "minimum_age", "age_min": "minimum_age", "elig_age_or_over": "minimum_age",
        "minimum_age_years": "minimum_age", "age_from": "minimum_age",
        "max_age": "maximum_age", "age_max": "maximum_age", "elig_age_or_under": "maximum_age",
        "age_to": "maximum_age",
        "last_verified": "assured_date", "last_verified_at": "assured_date", "verified_date": "assured_date",
        "verified_at": "assured_date", "date_verified": "assured_date", "verified_on": "assured_date",
        "last_updated": "last_modified", "updated_at": "last_modified", "modified": "last_modified",
        "date_modified": "last_modified", "updated": "last_modified", "last_update": "last_modified",
        "modified_at": "last_modified",
        "wait": "wait_time", "waittime": "wait_time", "waiting_time": "wait_time",
        "interpretation": "interpretation_services", "interpreter_services": "interpretation_services",
        "accreditation": "accreditations", "license": "licenses",
        "temporary_message": "alert", "alerts": "alert", "notice": "alert",
        "service_status": "status", "state_status": "status",
        "assurer": "assurer_email",
    },
    "program": {"program_name": "name", "title": "name", "alternate_names": "alternate_name",
                "summary": "description"},
    "location": {
        "location_name": "name", "site_name": "name", "branch_name": "name", "facility_name": "name",
        "title": "name", "alternate_names": "alternate_name",
        "lat": "latitude", "y": "latitude", "lng": "longitude", "lon": "longitude", "long": "longitude",
        "x": "longitude", "type": "location_type", "kind": "location_type",
        "website": "url", "transit": "transportation", "directions": "transportation",
        "public_transportation": "transportation",
    },
    "address": {
        "address": "address_1", "address1": "address_1", "street": "address_1", "street_address": "address_1",
        "address_line_1": "address_1", "address_line1": "address_1", "line1": "address_1",
        "line_1": "address_1", "street_1": "address_1", "street1": "address_1", "addr": "address_1",
        "address_line": "address_1",
        "address2": "address_2", "address_line_2": "address_2", "address_line2": "address_2",
        "line2": "address_2", "line_2": "address_2", "unit": "address_2", "suite": "address_2",
        "apt": "address_2", "street_2": "address_2", "street2": "address_2",
        "town": "city", "locality": "city", "municipality": "city",
        "county": "region", "district": "region",
        "state": "state_province", "province": "state_province", "st": "state_province",
        "state_code": "state_province",
        "zip": "postal_code", "zip_code": "postal_code", "zipcode": "postal_code", "postcode": "postal_code",
        "postal": "postal_code", "zip_postal_code": "postal_code",
        "country_code": "country", "type": "address_type", "kind": "address_type",
        "attn": "attention", "care_of": "attention",
    },
    "phone": {
        "phone": "number", "phone_number": "number", "digits": "number", "telephone": "number",
        "tel": "number", "telephone_number": "number", "value": "number", "phone_no": "number",
        "ext": "extension", "extension_number": "extension", "phone_extension": "extension",
        "phone_type": "type", "kind": "type", "phone_description": "description",
    },
    "contact": {"contact_name": "name", "full_name": "name", "job_title": "title", "position": "title",
                "role": "title", "email_address": "email", "e_mail": "email", "dept": "department"},
    "schedule": {
        "opens": "opens_at", "open": "opens_at", "open_time": "opens_at", "opening_time": "opens_at",
        "start": "opens_at", "start_time": "opens_at", "from": "opens_at", "opens_at_time": "opens_at",
        "closes": "closes_at", "close": "closes_at", "close_time": "closes_at", "closing_time": "closes_at",
        "end": "closes_at", "end_time": "closes_at", "to": "closes_at",
        "day": "byday", "days": "byday", "weekday": "byday", "weekdays": "byday", "day_of_week": "byday",
        "start_date": "valid_from", "end_date": "valid_to", "frequency": "freq",
        "hours": "description", "hours_of_operation": "description", "opening_hours": "description",
        "schedule": "description", "note": "notes", "link": "schedule_link", "url": "schedule_link",
        "tz": "timezone",
    },
    "service_area": {"value": "name", "area": "name", "area_name": "name", "county": "name",
                     "geography": "name", "geo_json": "extent", "geojson": "extent"},
    "language": {"language": "name", "language_name": "name", "description": "name", "lang": "name",
                 "codes": "code", "language_code": "code", "iso_code": "code", "notes": "note"},
    "accessibility": {"notes": "details", "accessibility_details": "details", "ada": "description",
                      "ada_accessible": "description", "accessibility_description": "description"},
    "required_document": {"description": "document", "name": "document", "document_name": "document",
                          "url": "uri", "link": "uri"},
    "cost_option": {"price": "amount", "cost": "amount", "fee": "amount", "name": "option",
                    "description": "amount_description", "currency_code": "currency"},
    "funding": {"name": "source", "funder": "source", "funding_source": "source", "description": "source"},
    "organization_identifier": {"value": "identifier", "type": "identifier_type",
                                "scheme": "identifier_scheme"},
    "taxonomy_term": {"taxonomy_term": "name", "term": "name", "label": "name", "value": "name",
                      "taxonomy_code": "code", "term_code": "code", "category": "name", "tag": "name",
                      "uri": "term_uri", "url": "term_uri", "parent": "parent_id"},
    "taxonomy": {"title": "name"},
    "url": {"link": "url", "href": "url", "website": "url", "name": "label", "title": "label"},
    "attribute": {},
    "metadata": {},
    "service_at_location": {"summary": "description"},
    "service_capacity": {},
    "unit": {},
    "meta_table_description": {},
}

# Scalar keys that describe a *child* table even when they appear on the parent
# (e.g. a "languages" column on a service row). Value: (table, column, split_pattern).
SCALAR_CHILD: dict[str, tuple[str, str, str | None]] = {
    "language": ("language", "name", r"[,;/]| and "),
    "languages": ("language", "name", r"[,;/]| and "),
    "languages_spoken": ("language", "name", r"[,;/]| and "),
    "language_codes": ("language", "code", r"[,;/ ]"),
    "service_area": ("service_area", "name", r";|\n"),
    "service_areas": ("service_area", "name", r";|\n"),
    "areas_served": ("service_area", "name", r";|\n"),
    "counties_served": ("service_area", "name", r"[;,]|\n"),
    "service_county": ("service_area", "name", r"[;,]|\n"),
    "coverage_area": ("service_area", "name", r";|\n"),
    "elig_county_resident": ("service_area", "name", r";|\n"),
    "elig_city_resident": ("service_area", "name", r";|\n"),
    "required_documents": ("required_document", "document", r";|\n"),
    "documents_required": ("required_document", "document", r";|\n"),
    "elig_documentation": ("required_document", "document", r";|\n"),
    "documents": ("required_document", "document", r";|\n"),
    "hours": ("schedule", "description", None),
    "hours_of_operation": ("schedule", "description", None),
    "opening_hours": ("schedule", "description", None),
    "schedule": ("schedule", "description", None),
    "schedule_notes": ("schedule", "notes", None),
    "phone": ("phone", "number", None),
    "phones": ("phone", "number", r"[;\n]"),
    "phone_number": ("phone", "number", None),
    "telephone": ("phone", "number", None),
    "tel": ("phone", "number", None),
    "main_phone": ("phone", "number", None),
    "fax": ("phone", "number", None),
    "fax_number": ("phone", "number", None),
    "toll_free": ("phone", "number", None),
    "hotline": ("phone", "number", None),
    "phone_extension": ("phone", "extension", None),
    "address": ("address", "address_1", None),
    "unit": ("address", "address_2", None),
    "suite": ("address", "address_2", None),
    "full_address": ("address", "address_1", None),
    "accessibility": ("accessibility", "description", None),
    "ada_accessible": ("accessibility", "description", None),
    "ada_accessible_notes": ("accessibility", "details", None),
    "wheelchair_accessible": ("accessibility", "description", None),
    "funding": ("funding", "source", r";|\n"),
    "funding_source": ("funding", "source", r";|\n"),
    "funding_sources": ("funding", "source", r";|\n"),
    "contact_name": ("contact", "name", None),
    "contact_person": ("contact", "name", None),
    "contact_title": ("contact", "title", None),
    "contact_email": ("contact", "email", None),
    "additional_urls": ("url", "url", r"[;\s]+"),
    "additional_websites": ("url", "url", r"[;\s]+"),
    "cost_options": ("cost_option", "amount_description", r";|\n"),
}

# Phone type keys: a column called "fax" means a phone of type fax.
PHONE_TYPE_FROM_KEY = {"fax": "fax", "fax_number": "fax", "toll_free": "voice", "hotline": "voice",
                       "main_phone": "voice", "tty": "textphone", "text": "text", "sms": "text"}

# Keys that unambiguously belong to one satellite table when seen on another table.
# Built from the aliases above for these tables, minus generic words.
IMPLIED_TABLES = ("address", "location", "phone", "schedule", "organization")
GENERIC_KEYS = {"id", "name", "title", "description", "summary", "type", "kind", "email", "url",
                "website", "web", "link", "value", "notes", "note", "status", "alternate_name",
                "alternate_names", "aka", "language", "uri", "label", "code", "unit", "start", "end",
                "from", "to", "open", "close", "day", "days", "schedule", "hours", "x", "y", "long",
                "address", "phone", "tel", "telephone", "metadata", "attributes", "logo"}

# Day names -> RFC 5545 BYDAY codes.
DAYS = {
    "monday": "MO", "mon": "MO", "mo": "MO", "m": "MO",
    "tuesday": "TU", "tue": "TU", "tues": "TU", "tu": "TU",
    "wednesday": "WE", "wed": "WE", "we": "WE",
    "thursday": "TH", "thu": "TH", "thur": "TH", "thurs": "TH", "th": "TH",
    "friday": "FR", "fri": "FR", "fr": "FR",
    "saturday": "SA", "sat": "SA", "sa": "SA",
    "sunday": "SU", "sun": "SU", "su": "SU",
}
DAY_ORDER = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]

# Foreign-key spellings: normalised key -> referenced HSDS table.
FK_KEYS: dict[str, str] = {}
for _t, _aliases in {
    "organization": ["organization", "org", "agency", "provider"],
    "service": ["service", "opportunity"],
    "location": ["location", "site"],
    "program": ["program"],
    "contact": ["contact"],
    "phone": ["phone"],
    "service_at_location": ["service_at_location"],
    "taxonomy_term": ["taxonomy_term"],
    "taxonomy": ["taxonomy"],
    "unit": ["unit"],
}.items():
    for _a in _aliases:
        FK_KEYS[f"{_a}_id"] = _t
        FK_KEYS[f"id_{_a}"] = _t
        FK_KEYS[f"{_a}_uuid"] = _t
FK_KEYS["parent_organization_id"] = "organization"
FK_KEYS["id_parent_organization"] = "organization"
FK_KEYS["parent_id"] = "taxonomy_term"

# Lists of foreign ids: normalised key -> referenced table.
FK_LIST_KEYS = {"location_ids": "location", "service_ids": "service", "organization_ids": "organization",
                "program_ids": "program"}

MISSING_TOKENS = {"", "na", "n/a", "n.a.", "none", "null", "nil", "nan", "-", "--", "undefined", "unknown"}

COUNTRY_CODES = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "us": "US", "america": "US", "canada": "CA", "mexico": "MX", "united kingdom": "GB", "uk": "GB",
    "great britain": "GB", "england": "GB",
}

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI",
    "wyoming": "WY", "puerto rico": "PR",
}

PHONE_TYPES = {
    "voice": "voice", "main": "voice", "phone": "voice", "office": "voice", "landline": "voice",
    "work": "voice", "tel": "voice", "telephone": "voice", "toll_free": "voice", "toll free": "voice",
    "hotline": "voice", "business": "voice",
    "fax": "fax", "facsimile": "fax",
    "cell": "cell", "mobile": "cell", "cellular": "cell",
    "text": "text", "sms": "text",
    "video": "video", "pager": "pager",
    "textphone": "textphone", "tty": "textphone", "tdd": "textphone", "ttd": "textphone",
}

SERVICE_STATUS = {
    "active": "active", "open": "active", "operating": "active", "available": "active", "true": "active",
    "inactive": "inactive", "closed": "inactive", "unavailable": "inactive", "false": "inactive",
    "suspended": "inactive",
    "defunct": "defunct", "permanently closed": "defunct", "permanently_closed": "defunct",
    "discontinued": "defunct", "deleted": "defunct",
    "temporarily closed": "temporarily closed", "temporarily_closed": "temporarily closed",
    "temporary closed": "temporarily closed", "paused": "temporarily closed",
}

LOCATION_TYPES = {
    "physical": "physical", "street": "physical", "office": "physical", "site": "physical",
    "in_person": "physical", "in person": "physical", "building": "physical",
    "postal": "postal", "mailing": "postal", "mail": "postal", "po box": "postal",
    "virtual": "virtual", "online": "virtual", "remote": "virtual", "web": "virtual", "phone": "virtual",
}
