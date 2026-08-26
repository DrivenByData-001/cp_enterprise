"""§8.3 span validation — no claim tables exist yet in Phase 1 (evidence_claim /
requirement_claim are Phase 2), so there's nothing here to call this against yet.
It ships now, with a pytest harness, so Phase 2's claim review queue can import
it unchanged, per §8.3's mandate that it be "built in Phase 2, day one"."""


def validate_span(
    document_body: str,
    span: str,
    offset_start: int | None = None,
    offset_end: int | None = None,
) -> bool:
    """True iff `span` occurs literally in `document_body`.

    If both offsets are given, requires an exact slice match at that position
    (catches offset drift, e.g. from a document edit) rather than accepting any
    occurrence anywhere in the text. Otherwise falls back to a plain substring
    search.
    """
    if not document_body or not span:
        return False
    if offset_start is not None and offset_end is not None:
        return document_body[offset_start:offset_end] == span
    return span in document_body
