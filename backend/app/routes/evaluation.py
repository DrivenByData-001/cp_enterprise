from fastapi import APIRouter, Query

from .. import evaluation
from ..db import db_cursor

router = APIRouter(prefix="/api/eval", tags=["evaluation"])


@router.get("/report")
def get_report(split: str | None = Query(default=None, pattern="^(dev|test)$")):
    """Engineering tests and evaluation metrics are separate things (brief
    §39) — this is the latter. Every field is either a real measurement or
    `measured: False` with a reason; nothing here is a fabricated pass."""
    with db_cursor() as cur:
        return {
            "span_validity": evaluation.span_validity(cur),
            "concept_linking_f1": evaluation.concept_linking_f1(cur, split=split),
            "modifier_accuracy": evaluation.modifier_accuracy(cur, split=split),
            "proposals_per_document": evaluation.proposals_per_document(cur),
            "capability_agreement": evaluation.capability_agreement(cur, split=split),
        }
