"""Phase 2 evaluation debt + Phase 3 capability-agreement evaluation (brief
§24-26). Every metric here is computed from `jobber.gold_*`/
`jobber.capability_gold_judgment` rows that must be hand-labelled against
real captured documents/capabilities. A metric with nothing labelled yet
returns `measured: False` and `value: None` rather than a fabricated
number or a false zero — see `routes/evaluation.py` and the Phase 3
completion report for how this must be surfaced (brief: "do not falsely
report these gates as passed if a sufficient labelled set does not yet
exist").
"""

from .capability_engine import CapabilityNotFoundError, derive_capability_coverage
from .span_validation import validate_span


def span_validity(cur) -> dict:
    """Fraction of currently-stored stated/implied requirement_claim rows
    whose evidence_span is verifiably present in its source document.

    This re-checks an invariant `span_validation.validate_span` already
    enforces at write time (`extraction.py` never stores a claim whose
    proposed span failed validation) — so this is expected to read 1.0 by
    construction once any stated/implied claims exist. It is still worth
    computing: a value below 1.0 would mean that write-time guarantee was
    bypassed (e.g. a direct database write), which is itself the signal
    this metric exists to catch. It does not measure what fraction of the
    *model's raw proposals* had valid spans before filtering — that number
    lives only in each extraction_run's own notes/rejected count, not
    aggregated here (see docs/16 §9's "known limitations")."""
    cur.execute(
        """
        SELECT rc.evidence_span, d.content_text
        FROM jobber.requirement_claim rc
        JOIN jobber.document d ON d.id = rc.document_id
        WHERE rc.basis IN ('stated', 'implied') AND rc.evidence_span IS NOT NULL
        """
    )
    rows = cur.fetchall()
    if not rows:
        return {"measured": False, "value": None, "n": 0, "note": "no stated/implied requirement claims exist yet"}
    valid = sum(1 for r in rows if validate_span(r["content_text"], r["evidence_span"]))
    return {"measured": True, "value": round(valid / len(rows), 4), "n": len(rows)}


def proposals_per_document(cur) -> dict:
    """Same computation as GET /api/concepts/proposals/stats, exposed here
    too so a full evaluation report is one call."""
    cur.execute("SELECT COUNT(*) AS n FROM jobber.document WHERE kind = 'job_posting'")
    total_documents = cur.fetchone()["n"]
    if not total_documents:
        return {"measured": False, "value": None, "n": 0, "note": "no job_posting documents captured yet"}
    cur.execute("SELECT COUNT(DISTINCT surface_form) AS n FROM jobber.concept_proposal WHERE status = 'pending'")
    pending_groups = cur.fetchone()["n"]
    return {"measured": True, "value": round(pending_groups / total_documents, 4), "n": total_documents}


def concept_linking_f1(cur, split: str | None = None) -> dict:
    """Micro F1 over gold_claim.is_core rows: does the system's own
    (non-superseded) requirement_claim set for a gold document contain the
    same (document, concept) pair as the gold label? Zero-division is
    treated as 0.0, not undefined, so the metric is always a plain number
    once any gold exists."""
    query = "SELECT gc.document_id, gc.concept_id, gc.is_core FROM jobber.gold_claim gc"
    params: list = []
    if split:
        query = (
            "SELECT gc.document_id, gc.concept_id, gc.is_core FROM jobber.gold_claim gc "
            "JOIN jobber.gold_document gd ON gd.document_id = gc.document_id WHERE gd.split = %s"
        )
        params = [split]
    cur.execute(query, params)
    gold_rows = cur.fetchall()
    if not gold_rows:
        return {"measured": False, "value": None, "precision": None, "recall": None, "n": 0, "note": "no gold_claim rows labelled yet"}

    core_gold = {(str(r["document_id"]), str(r["concept_id"])) for r in gold_rows if r["is_core"]}
    document_ids = list({str(r["document_id"]) for r in gold_rows})

    cur.execute(
        "SELECT document_id, concept_id FROM jobber.requirement_claim "
        "WHERE document_id = ANY(%s::uuid[]) AND superseded_by IS NULL",
        (document_ids,),
    )
    system_pairs = {(str(r["document_id"]), str(r["concept_id"])) for r in cur.fetchall()}

    true_positive = len(core_gold & system_pairs)
    precision = (true_positive / len(system_pairs)) if system_pairs else 0.0
    recall = (true_positive / len(core_gold)) if core_gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {"measured": True, "value": round(f1, 4), "precision": round(precision, 4), "recall": round(recall, 4), "n": len(core_gold)}


def modifier_accuracy(cur, split: str | None = None) -> dict:
    """Not applicable in this build — see docs/16-phase3-capability-engine.md
    §9. This codebase's own extraction pipeline (extract_role_requirements)
    produces role-side requirement_claim rows, which carry no depth/autonomy
    modifiers at all (those describe how a *person* demonstrated something,
    not what a *role* requires). Person-side modifier extraction belongs to
    profile360, a separate tool this build does not own, control, or have
    the corpus to measure. Returning `measured: False` here rather than a
    fabricated number or silently omitting the metric."""
    return {
        "measured": False,
        "value": None,
        "n": 0,
        "note": (
            "not applicable in this build: requirement_claim (the only claim table this "
            "codebase's extraction pipeline writes) carries no depth/autonomy modifiers — "
            "those are person-side attributes, owned by profile360. See docs/16 §9."
        ),
    }


def capability_agreement(cur, split: str | None = None) -> dict:
    """The primary Phase 3 quality gate (brief §26): exact status agreement
    between the deterministic engine and hand-labelled expectations. Runs
    the real engine (never a stub) against each judgment's capability."""
    query = "SELECT capability_concept_id, expected_status FROM jobber.capability_gold_judgment"
    params: list = []
    if split:
        query += " WHERE split = %s"
        params = [split]
    cur.execute(query, params)
    judgments = cur.fetchall()
    if not judgments:
        return {"measured": False, "value": None, "n": 0, "note": "no capability_gold_judgment rows labelled yet"}

    matches = 0
    details = []
    for j in judgments:
        capability_id = str(j["capability_concept_id"])
        try:
            actual = derive_capability_coverage(cur, capability_id)["status"]
        except CapabilityNotFoundError:
            actual = None
        agree = actual == j["expected_status"]
        matches += int(agree)
        details.append({"capability_concept_id": capability_id, "expected": j["expected_status"], "actual": actual, "agree": agree})
    return {"measured": True, "value": round(matches / len(judgments), 4), "n": len(judgments), "details": details}
