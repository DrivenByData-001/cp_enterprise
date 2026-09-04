"""DEMO/ILLUSTRATION ONLY — NOT the real production gold set, NOT real
curated catalogue data. Never run this against the real Supabase project or
any database whose capability catalogue is meant to be trusted — it inserts
5 hand-authored, clearly-fictional capabilities indistinguishable in the
schema from real curator work (there is no "demo" flag on
`jobber.concept`/`capability_detail`). Run it only against a disposable/local
database, per the usage note below.

This build environment has no credential for the real Supabase project and
no captured production corpus to hand-label (the same constraint every
phase since Phase 0 has recorded — see docs/14-phase2-postgres-architecture.md
§2). Rather than report only zero-labelled metrics, this script seeds a
small, honestly-labelled illustrative set: capability catalogue rows,
curator-authored evidence, and `capability_gold_judgment` rows assigned by
reading the evidence *before* running the engine — so the capability-
agreement number this produces is a real measurement of the deterministic
engine against independently-authored expectations, not a number copied
from the engine's own output.

**This does not establish, and must never be cited as establishing, the
brief's actual Phase 3 analytical gates** (capability agreement ≥0.80 on
~20 real hand-labelled judgments; concept-linking F1 ≥0.75 on real dev-split
gold). Those require a human curation exercise against the real corpus —
see docs/16-phase3-capability-engine.md §0.1/§13.

Usage (against a disposable/dev database only — never production):

    export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/cp_eval_demo
    createdb cp_eval_demo   # if it doesn't exist yet
    psql "$DATABASE_URL" -f backend/scripts/local_baseline.sql
    python backend/scripts/seed_phase3_eval_sample.py

Prints the resulting evaluation report as JSON. Not run by the test suite,
not run on backend startup, not idempotent (each run inserts new rows) — a
one-shot illustrative demo, not a fixture or a migration.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import capability_engine as engine  # noqa: E402
from app import db, evaluation  # noqa: E402
from app.db import db_cursor, run_migrations  # noqa: E402


def _concept(cur, name, type_code="tool"):
    cur.execute(
        "INSERT INTO jobber.concept (type_code, canonical_name, status, origin, created_at) "
        "VALUES (%s, %s, 'active', 'curator', now()) RETURNING id",
        (type_code, name),
    )
    return str(cur.fetchone()["id"])


def _capability(cur, name, **detail):
    cap_id = _concept(cur, name, type_code="capability")
    cur.execute(
        "INSERT INTO jobber.capability_detail "
        "(concept_id, demonstration_standard, min_depth, min_autonomy, requires_all_core, min_core_required) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (
            cap_id,
            detail.get("demonstration_standard", f"Demonstration standard for {name}."),
            detail.get("min_depth", "owned"),
            detail.get("min_autonomy"),
            detail.get("requires_all_core", True),
            detail.get("min_core_required"),
        ),
    )
    return cap_id


def _edge(cur, atom_id, cap_id, necessity):
    cur.execute(
        "INSERT INTO jobber.concept_edge (from_concept_id, to_concept_id, relation, necessity, origin, status) "
        "VALUES (%s, %s, 'component_of', %s, 'curator', 'accepted')",
        (atom_id, cap_id, necessity),
    )


def _episode(cur, start, end, autonomy=None):
    cur.execute(
        "INSERT INTO profile360.episodes (start_date, end_date, autonomy, status, title, organisation) "
        "VALUES (%s, %s, %s, 'active', 'Senior Actuarial Analyst', 'Illustrative Insurer') RETURNING id",
        (start, end, autonomy),
    )
    return str(cur.fetchone()["id"])


def _claim(cur, text, episode_id, depth):
    cur.execute(
        "INSERT INTO profile360.claims (claim_text, episode_id, depth) VALUES (%s, %s, %s) RETURNING id",
        (text, episode_id, depth),
    )
    return str(cur.fetchone()["id"])


def _map(cur, claim_id, concept_id):
    cur.execute(
        "INSERT INTO jobber.profile360_claim_mapping (profile360_claim_id, jobber_concept_id, mapping_basis, review_status) "
        "VALUES (%s, %s, 'curator_asserted', 'accepted')",
        (claim_id, concept_id),
    )


def seed(cur):
    python_id = _concept(cur, "Python", "tool")
    reserving_id = _concept(cur, "Reserving", "function")

    episode = _episode(cur, date(2019, 3, 1), date(2021, 6, 30), autonomy="independent")
    _map(cur, _claim(cur, "Used Python to build the reserving model.", episode, "owned"), python_id)
    _map(cur, _claim(cur, "Ran the quarterly reserving cycle.", episode, "owned"), reserving_id)

    cap1 = _capability(cur, "Own a production actuarial model", min_depth="owned")
    _edge(cur, python_id, cap1, "core")
    _edge(cur, reserving_id, cap1, "core")
    _map(cur, _claim(cur, "Rebuilt and owned the annuity reserving model in Python end-to-end.", episode, "owned"), cap1)

    cap2 = _capability(cur, "Lead a reserving process", min_depth="owned", min_autonomy="directed_others")
    _edge(cur, reserving_id, cap2, "core")

    cap3 = _capability(cur, "Influence executive stakeholders")

    cap4 = _capability(cur, "Manage an actuarial team", min_autonomy="directed_others")
    cur.execute(
        "INSERT INTO jobber.person_capability_assertion (jobber_concept_id, asserted, note) VALUES (%s, TRUE, %s)",
        (cap4, "Managed two graduate actuaries for a year — not yet reviewed as a formal claim."),
    )

    cap5 = _capability(cur, "Sign off technical provisions under Solvency II", min_depth="owned")
    solvency_id = _concept(cur, "Solvency II", "regulation")
    _edge(cur, solvency_id, cap5, "contextual")
    _map(cur, _claim(cur, "Reviewed technical provisions under Solvency II.", episode, "applied"), cap5)

    # Gold judgments — the curator's own expectation, reasoned from the
    # evidence above, recorded before the engine runs. Not copied from the
    # engine's output.
    judgments = [
        (cap1, "evidenced", "direct claim states the whole capability at depth owned, meeting the threshold"),
        (cap2, "partial", "reserving is fully evidenced compositionally, but nothing states the person *led* it — no autonomy evidence at all"),
        (cap3, "not_found", "no components curated, no direct evidence, no assertion"),
        (cap4, "user_asserted", "only a local assertion, no accepted evidence"),
        (cap5, "partial", "direct claim exists but at depth applied, below the required owned threshold"),
    ]
    for cap_id, expected, notes in judgments:
        cur.execute(
            "INSERT INTO jobber.capability_gold_judgment (capability_concept_id, expected_status, notes) VALUES (%s, %s, %s)",
            (cap_id, expected, notes),
        )

    # One illustrative gold document + claims, for span validity / concept-linking F1.
    document_id, _ = db.create_document(
        cur,
        kind="job_posting",
        content_text="We need someone who can build models in Python and lead the reserving cycle end to end.",
        provenance_quality="original",
        title="Illustrative Senior Actuarial Analyst posting",
    )
    role_id = db.upsert_role_instance(
        cur, None,
        {"instance_type": "observed_posting", "title": "Illustrative posting", "document_id": document_id},
        skills=[],
    )
    cur.execute("INSERT INTO jobber.gold_document (document_id, split, stratum) VALUES (%s, 'dev', 'actuarial_core')", (document_id,))
    cur.execute(
        "INSERT INTO jobber.gold_claim (document_id, concept_id, relation, evidence_span, is_core) VALUES (%s, %s, 'requires', %s, TRUE)",
        (document_id, python_id, "build models in Python"),
    )
    cur.execute(
        "INSERT INTO jobber.gold_claim (document_id, concept_id, relation, evidence_span, is_core) VALUES (%s, %s, 'requires', %s, TRUE)",
        (document_id, reserving_id, "lead the reserving cycle"),
    )
    # A curator-entered requirement_claim for only the first gold concept —
    # deliberately leaving the second undiscovered, so concept_linking_f1
    # reports a genuine (not trivially perfect) recall.
    cur.execute(
        "INSERT INTO jobber.requirement_claim (role_instance_id, concept_id, requirement_type, basis, document_id, evidence_span) "
        "VALUES (%s, %s, 'required', 'stated', %s, %s)",
        (role_id, python_id, document_id, "build models in Python"),
    )


def main():
    run_migrations()
    # Close the connection pool explicitly before exit (db.reset_pool's
    # docstring) instead of leaving it to psycopg_pool's __del__ at
    # interpreter shutdown, which logs a spurious thread-join warning.
    try:
        with db_cursor() as cur:
            seed(cur)
            rebuild = engine.rebuild_phase3_derivations(cur)
            report = {
                "rebuild": rebuild,
                "span_validity": evaluation.span_validity(cur),
                "concept_linking_f1": evaluation.concept_linking_f1(cur),
                "modifier_accuracy": evaluation.modifier_accuracy(cur),
                "proposals_per_document": evaluation.proposals_per_document(cur),
                "capability_agreement": evaluation.capability_agreement(cur),
            }
    finally:
        db.reset_pool()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
