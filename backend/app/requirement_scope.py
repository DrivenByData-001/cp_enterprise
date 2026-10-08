"""Keep clearly labelled posting metadata out of capability vocabulary review.

Use the term's own evidence, not a global blacklist of place names: a place
mentioned in a regulatory or market-knowledge requirement can be meaningful.
This also works on old proposals without changing shared vocabulary records.
"""

import re
import unicodedata


_METADATA_LINE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:job\s+|role\s+|work\s+)?"
    r"(?P<label>location|country|city|office location|salary|compensation|"
    r"pay range|salary range|employment type|contract type|working pattern|"
    r"remote type|job title|posting date|closing date|application deadline)"
    r"\s*:\s*(?P<value>.+?)\s*$", re.IGNORECASE,
)


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).strip(" .;:")


def is_posting_metadata(surface_form: str, evidence_span: str | None) -> bool:
    """Only exclude a term equal to a labelled value (or location component).

    Do not discard skills merely because their quoted passage also includes
    a metadata line. Unlabelled/ambiguous text stays eligible for review.
    """
    surface = _normalise(surface_form)
    if not surface or not evidence_span:
        return False
    for line in evidence_span.splitlines():
        match = _METADATA_LINE.fullmatch(line)
        if not match:
            continue
        value = match["value"]
        candidates = {_normalise(value), _normalise(line)}
        if match["label"].casefold() in {"location", "country", "city", "office location"}:
            candidates.update(_normalise(part) for part in re.split(r"[,|/]", value))
        if surface in candidates:
            return True
    return False


def pending_requirement_proposals(cur, role_ids: list[str]) -> list[dict]:
    """One shared scope for the application list and review-completeness counts."""
    if not role_ids:
        return []
    cur.execute(
        "SELECT cp.*, o.role_instance_id, o.evidence_span AS role_evidence_span "
        "FROM jobber.concept_proposal cp "
        "JOIN jobber.concept_proposal_occurrence o ON o.concept_proposal_id=cp.id "
        "WHERE o.role_instance_id=ANY(%s::uuid[]) AND cp.status='pending' "
        "ORDER BY cp.surface_form, cp.id", (role_ids,),
    )
    return [dict(row) for row in cur.fetchall()
            if not is_posting_metadata(row["surface_form"], row["role_evidence_span"])]
