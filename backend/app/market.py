"""Controlled market dimension helpers (Phase 4 prompt §5).

`jobber.market` is a small first-class controlled dimension, not a global
geography ontology. The only normalisation this module performs is a short,
explicit alias table for genuinely unambiguous abbreviations (prompt §7:
"Normalise obvious existing country aliases only through a documented
mapping (e.g. UK -> United Kingdom). Do not guess genuinely ambiguous
multi-country rows."). A country string that isn't a known alias and isn't
on the ambiguous blocklist is assumed to already be a clean, specific
country name (e.g. "Germany", "Ireland") and is used to get-or-create its
own market row directly — no invented taxonomy beyond that.
"""

import re

# Obvious abbreviations only — every value on the right is itself a market
# `label`/`country` this module will use unchanged.
_COUNTRY_ALIASES = {
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "u.k": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "gb": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "northern ireland": "United Kingdom",
    "us": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
}

# Values that name no single country and must never be auto-assigned a
# market — retained as unassigned/unusable for aggregation rather than
# guessed (prompt §7).
_AMBIGUOUS = {
    "",
    "remote",
    "europe",
    "eu",
    "emea",
    "apac",
    "global",
    "worldwide",
    "multiple",
    "various",
    "n/a",
    "na",
    "unknown",
    "international",
}


def normalize_country(raw_country: str | None) -> str | None:
    """Returns a clean, specific country name, or None if `raw_country` is
    missing/ambiguous and cannot be safely resolved. Never guesses across a
    genuinely multi-country label."""
    if not raw_country:
        return None
    key = re.sub(r"\s+", " ", raw_country.strip().lower())
    if key in _AMBIGUOUS:
        return None
    if key in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[key]
    # Already a specific-looking name (not on the alias/ambiguous lists) —
    # use it as-is, title-cased for a consistent label.
    return raw_country.strip()


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return slug or "market"


def get_or_create_market_by_country(cur, raw_country: str | None) -> str | None:
    """Deterministic, idempotent get-or-create of a per-country market row,
    keyed by a slug of the normalised country name. Returns None (never
    guesses) when the country cannot be safely normalised — the caller must
    then leave the compensation observation's market_id NULL."""
    country = normalize_country(raw_country)
    if not country:
        return None
    code = f"country-{_slugify(country)}"
    cur.execute("SELECT id FROM jobber.market WHERE code = %s", (code,))
    row = cur.fetchone()
    if row:
        return str(row["id"])
    cur.execute(
        "INSERT INTO jobber.market (code, label, country, geography, status) "
        "VALUES (%s, %s, %s, %s, 'active') "
        "ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code RETURNING id",
        (code, country, country, country),
    )
    return str(cur.fetchone()["id"])
