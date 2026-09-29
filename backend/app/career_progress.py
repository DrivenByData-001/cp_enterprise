"""Phase 9: current Target progress derivation + checkpoint comparison
(docs/40).

This module computes nothing new about capability evidence. The *current*
snapshot is built entirely from `comparison_service.build_role_comparison`
— the same canonical comparison Target/Pathways/Comparison/Applications
already use (build §4) — reshaped into the small, bounded, checkpoint-
storable form migration 0031 persists. `evidenced`/`partial`/`user_asserted`/
`not_found` are preserved distinctly throughout; nothing here ever collapses
partial/asserted into "met" (build §4).

A checkpoint is a historical record of derived evidence state, never
evidence itself (build §2): recording one calls no Profile360 write, no
requirement-review write, no Target/Pathways mutation — `record_checkpoint`
below does exactly one INSERT, into `jobber.career_progress_checkpoint`.

Checkpoints are comparable only within the same (career_direction_id,
target_role_instance_id, state_schema_version) — see `latest_comparable_
checkpoint` (build §6). Changing Direction, changing Target, or deleting the
old Target (ON DELETE SET NULL, migration 0031) all naturally fall out of
that scope filter rather than needing special-cased detection.
"""

from . import capability_engine, comparison_service
from .db import to_json_param
from .target_cache import revisions as target_cache_revisions

STATE_SCHEMA_VERSION = 1

CHECKPOINT_TYPE_BASELINE = "baseline"
CHECKPOINT_TYPE_CHECKPOINT = "checkpoint"

CHECKPOINT_HISTORY_LIMIT = 5

# Ordinal strength of an evidence status (build §7) — used only to classify
# a diff as strengthened vs. weakened; never persisted or shown as a score.
_STATUS_RANK = {"not_found": 0, "user_asserted": 1, "partial": 2, "evidenced": 3}


class CareerProgressTargetError(ValueError):
    """No selected Career Direction, or the selected Direction has no linked
    Target — 409 at the route layer (recording/reading progress needs both,
    build §5)."""


# --- Current state (build §3/§4) --------------------------------------------


def _development_action_counts(cur, target_role_instance_id: str) -> dict:
    """Open/done counts only (build §4) — a development_action is the user's
    own planning record, never evidence, so nothing beyond its status is
    read here. Reuses no private pathways.py helper; this is a smaller,
    single-role query than pathways._development_actions' bulk form."""
    cur.execute(
        "SELECT status, COUNT(*) AS n FROM jobber.development_action WHERE role_instance_id = %s GROUP BY status",
        (target_role_instance_id,),
    )
    counts = {"open": 0, "done": 0}
    for row in cur.fetchall():
        if row["status"] in counts:
            counts[row["status"]] = row["n"]
    return counts


def current_source_revision(cur) -> dict:
    """A fingerprint explaining what current evidence state a checkpoint was
    derived from (build §3), reusing `target_cache`'s existing evidence
    revision (migration 0019 triggers + a profile360 content hash) rather
    than inventing a second global revision system. Only the *evidence*
    digest is used — its computation does not depend on the embedding
    vectors `target_cache.revisions` also accepts for its `path` digest
    (route/archetype grouping, irrelevant to a Target's evidence snapshot),
    so this never needs to compute or load an embedding."""
    evidence_revision, _path_revision = target_cache_revisions(cur, None, None)
    return {"evidence_revision": evidence_revision, "engine_version": capability_engine.ENGINE_VERSION}


def build_current_target_state(cur, direction: dict) -> dict:
    """The deterministic, checkpoint-storable snapshot of `direction`'s
    linked Target (build §3/§4). Raises CareerProgressTargetError if the
    direction has no linked Target — callers that already branch on
    direction/target state (career_cockpit.py) should not normally hit this;
    it exists for the checkpoint-recording path, which requires both."""
    target = direction.get("target")
    if target is None:
        raise CareerProgressTargetError("the selected Career Direction has no linked Target")
    target_id = target["id"]

    comparison = comparison_service.build_role_comparison(cur, target_id)
    mapping = comparison["target_mapping"] or {"complete": True}

    requirements = [
        {
            "concept_id": item["concept"]["id"],
            "canonical_name": item["concept"]["canonical_name"],
            "requirement_type": item["role_side"]["requirement_type"],
            "status": item["status"],
        }
        for item in comparison["items"]
    ]
    required_total = sum(1 for r in requirements if r["requirement_type"] == "required")

    return {
        "direction": {"id": direction["id"], "name": direction["name"]},
        "target": {"id": target_id, "title": target.get("title")},
        "review": {
            "target_review_complete": comparison["review_summary"]["complete"],
            "target_mapping_complete": mapping["complete"],
        },
        "counts": {
            "requirements_total": len(requirements),
            "required_total": required_total,
            "evidenced": comparison["counts"]["evidenced"],
            "partial": comparison["counts"]["partial"],
            "user_asserted": comparison["counts"]["user_asserted"],
            "not_found": comparison["counts"]["not_found"],
            "blocking_required": len(comparison["blocking_gaps"]),
            "unverified_required": len(comparison["unverified_required"]),
        },
        "requirements": requirements,
        "development_actions": _development_action_counts(cur, target_id),
    }


# --- Checkpoints (build §5/§6) ----------------------------------------------


def _serialize_checkpoint(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "career_direction_id": str(row["career_direction_id"]),
        "target_role_instance_id": str(row["target_role_instance_id"]) if row["target_role_instance_id"] else None,
        "checkpoint_type": row["checkpoint_type"],
        "label": row["label"],
        "state_schema_version": row["state_schema_version"],
        "state": row["state"],
        "source_revision": row["source_revision"],
        "created_at": row["created_at"],
    }


def latest_comparable_checkpoint(cur, career_direction_id: str, target_role_instance_id: str) -> dict | None:
    """The most recent checkpoint for the *exact same* (direction, Target,
    schema version) — build §6's comparable-checkpoint rule. A Direction
    change, a Target change, or an old Target's deletion (ON DELETE SET
    NULL) all fall outside this scope automatically, so this returns None
    for each of them rather than needing separate detection."""
    cur.execute(
        "SELECT * FROM jobber.career_progress_checkpoint "
        "WHERE career_direction_id = %s AND target_role_instance_id = %s AND state_schema_version = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (career_direction_id, target_role_instance_id, STATE_SCHEMA_VERSION),
    )
    row = cur.fetchone()
    return _serialize_checkpoint(row) if row else None


def checkpoint_history(cur, career_direction_id: str, *, limit: int = CHECKPOINT_HISTORY_LIMIT) -> list[dict]:
    """Bounded history across every Target this Direction has ever recorded
    a checkpoint against (build §31) — read from each row's own stored
    `state` rather than joining role_instance, so a since-renamed or since-
    deleted Target still shows the title it had at checkpoint time."""
    cur.execute(
        "SELECT id, checkpoint_type, label, created_at, target_role_instance_id, state "
        "FROM jobber.career_progress_checkpoint WHERE career_direction_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (career_direction_id, limit),
    )
    history = []
    for row in cur.fetchall():
        state = row["state"] or {}
        history.append(
            {
                "id": str(row["id"]),
                "checkpoint_type": row["checkpoint_type"],
                "label": row["label"],
                "created_at": row["created_at"],
                "target_role_instance_id": (
                    str(row["target_role_instance_id"]) if row["target_role_instance_id"] else None
                ),
                "target_title": (state.get("target") or {}).get("title"),
                "counts": state.get("counts", {}),
            }
        )
    return history


def record_checkpoint(cur, direction: dict, *, label: str | None) -> dict:
    """POST /api/cockpit/progress/checkpoints (build §5). Always derives
    state server-side from `direction`/the database — the caller may only
    supply an optional label, never counts or statuses (build §5's "does not
    trust client-supplied counts/statuses"). The first checkpoint recorded
    for a (direction, Target) pair is a `baseline`; every one after that is a
    `checkpoint` — decided here, not by the caller."""
    current_state = build_current_target_state(cur, direction)
    target_id = current_state["target"]["id"]
    previous = latest_comparable_checkpoint(cur, direction["id"], target_id)
    checkpoint_type = CHECKPOINT_TYPE_BASELINE if previous is None else CHECKPOINT_TYPE_CHECKPOINT

    cur.execute(
        "INSERT INTO jobber.career_progress_checkpoint "
        "(career_direction_id, target_role_instance_id, checkpoint_type, label, state_schema_version, state, source_revision) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (
            direction["id"],
            target_id,
            checkpoint_type,
            (label or "").strip() or None,
            STATE_SCHEMA_VERSION,
            to_json_param(current_state),
            to_json_param(current_source_revision(cur)),
        ),
    )
    return _serialize_checkpoint(cur.fetchone())


# --- Diff (build §7/§8) ------------------------------------------------------


def _transition_entry(requirement: dict, previous_status: str, current_status: str) -> dict:
    return {
        "concept_id": requirement["concept_id"],
        "canonical_name": requirement["canonical_name"],
        "requirement_type": requirement["requirement_type"],
        "previous_status": previous_status,
        "current_status": current_status,
    }


def _requirement_summary(concept_id: str, source_by_concept: dict) -> dict:
    row = source_by_concept[concept_id]
    return {"concept_id": concept_id, "canonical_name": row["canonical_name"], "requirement_type": row["requirement_type"]}


def diff_checkpoint(previous_state: dict, current_state: dict) -> dict:
    """Compare requirement state by concept_id (build §7). Every group below
    is disjoint and explicit — a requirement whose *type* changed is reported
    only under `target_definition_changed`, never also scored as an evidence
    transition, since a redefinition and an evidence change are different
    facts (build §7's "Do not call these evidence progress"). No score or
    percentage is computed anywhere in this function."""
    prev_by_concept = {r["concept_id"]: r for r in previous_state.get("requirements", [])}
    curr_by_concept = {r["concept_id"]: r for r in current_state.get("requirements", [])}
    prev_ids, curr_ids = set(prev_by_concept), set(curr_by_concept)
    common_ids = prev_ids & curr_ids

    strengthened, weakened, assertion_added, type_changed = [], [], [], []
    unchanged_count = 0

    for concept_id in common_ids:
        prev_r, curr_r = prev_by_concept[concept_id], curr_by_concept[concept_id]
        if prev_r["requirement_type"] != curr_r["requirement_type"]:
            type_changed.append(
                {
                    "concept_id": concept_id,
                    "canonical_name": curr_r["canonical_name"],
                    "previous_requirement_type": prev_r["requirement_type"],
                    "current_requirement_type": curr_r["requirement_type"],
                }
            )
            continue

        previous_status, current_status = prev_r["status"], curr_r["status"]
        if previous_status == current_status:
            unchanged_count += 1
        elif previous_status == "not_found" and current_status == "user_asserted":
            # An assertion is never accepted evidence (build §7/§34) — kept
            # structurally apart from evidence_strengthened even though its
            # ordinal rank moved up, so it can never be described as one.
            assertion_added.append(_transition_entry(curr_r, previous_status, current_status))
        elif _STATUS_RANK[current_status] > _STATUS_RANK[previous_status]:
            strengthened.append(_transition_entry(curr_r, previous_status, current_status))
        else:
            # Every other rank-decreasing move, including user_asserted ->
            # not_found and evidenced -> user_asserted — regressions are
            # never hidden (build §7).
            weakened.append(_transition_entry(curr_r, previous_status, current_status))

    return {
        "evidence_strengthened": strengthened,
        "assertion_added": assertion_added,
        "evidence_weakened": weakened,
        "target_definition_changed": {
            "requirements_added": [_requirement_summary(cid, curr_by_concept) for cid in sorted(curr_ids - prev_ids)],
            "requirements_removed": [_requirement_summary(cid, prev_by_concept) for cid in sorted(prev_ids - curr_ids)],
            "requirement_type_changed": type_changed,
        },
        "unchanged_count": unchanged_count,
    }


# --- Read composition (build §5) --------------------------------------------


def read_progress(cur, direction: dict | None) -> dict:
    """GET /api/cockpit/progress (build §5) — read-only, never writes a
    checkpoint. Returns an honest `state` for every stage: no Direction, a
    Direction with no Target, a Target with no comparable checkpoint yet, or
    a full current-vs-checkpoint comparison.

    `comparison_state` ("normal" | "limited" | None) gates the diff
    specifically, not the current structural counts: while the *current*
    Target's requirement review or mapping is incomplete, an evidence-status
    diff against an earlier checkpoint would be comparing a still-moving
    target, so `comparison_state` is "limited" and `diff` is withheld —
    `current` (and its own `review` flags) are still returned in full, so
    the structural counts and the "needs attention" signal remain visible.
    This is computed fresh from the *current* state on every call, never
    persisted, so the normal diff resumes automatically the next time
    review/mapping are complete — no separate "resume" step exists."""
    if direction is None:
        return {"state": "no_direction", "direction": None, "current": None, "checkpoint": None, "comparison_state": None, "diff": None, "history": []}
    if direction.get("target") is None:
        return {
            "state": "no_target",
            "direction": {"id": direction["id"], "name": direction["name"]},
            "current": None,
            "checkpoint": None,
            "comparison_state": None,
            "diff": None,
            "history": [],
        }

    current = build_current_target_state(cur, direction)
    history = checkpoint_history(cur, direction["id"])
    latest = latest_comparable_checkpoint(cur, direction["id"], current["target"]["id"])
    if latest is None:
        return {
            "state": "no_checkpoint", "direction": current["direction"], "current": current,
            "checkpoint": None, "comparison_state": None, "diff": None, "history": history,
        }

    review_complete = current["review"]["target_review_complete"] and current["review"]["target_mapping_complete"]
    return {
        "state": "available",
        "direction": current["direction"],
        "current": current,
        "checkpoint": latest,
        "comparison_state": "normal" if review_complete else "limited",
        "diff": diff_checkpoint(latest["state"], current) if review_complete else None,
        "history": history,
    }
