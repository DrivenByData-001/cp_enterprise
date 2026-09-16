"""Whether the derived economics tables still reflect the evidence they were
built from.

Cache invalidation and derivation freshness are separate concepts, and
conflating them was a real defect: migration 0023's `economics` counter makes
a Pathways cache entry miss when compensation changes, but a miss only
*recomposes* Pathways from `d_archetype_comp` / `d_archetype_demand` /
`d_gap_value`. Those tables are only ever written by an explicit
`POST /api/economics/rebuild`. Without this module, accepting a salary
produced a freshly-computed answer over stale derived numbers and presented
it as current.

The three counters that matter, all from `jobber.target_analysis_revision`:

- `economics` — compensation observations, markets, the planning assumption,
  and the derived economics tables themselves. Changes `d_archetype_comp`
  and therefore every market benchmark.
- `path` — role instances (including archetype assignment), requirement
  claims, role skill observations. Changes `d_archetype_demand` and which
  roles a capability unlocks, so it changes `d_gap_value`.
- `evidence` — the concept/capability model and profile360 mappings. Gap
  value is a counterfactual over the *current* structural model, so a
  capability or mapping change makes it stale too.

A rebuild records all three; a read compares them. Nothing here rebuilds
anything — a rebuild walks every role's fit and is far too expensive for a
request path, so the honest response to staleness is to say so and suppress
the affected figures, not to silently serve them.
"""

REBUILD_HINT = "Rebuild economics (Economics → Rebuild) to bring the derived figures up to date."


def _live_revisions(cur) -> dict:
    cur.execute("SELECT evidence, path, economics FROM jobber.target_analysis_revision WHERE singleton")
    row = cur.fetchone()
    if not row:
        return {"evidence": 0, "path": 0, "economics": 0}
    return {"evidence": row["evidence"], "path": row["path"], "economics": row["economics"]}


def record_rebuild(cur, engine_version: str) -> dict:
    """Called at the end of an explicit economics rebuild, in the same
    transaction, so the recorded counters are exactly the state the rebuild
    saw. Writing this row deliberately does not itself bump any counter (see
    migration 0024) — otherwise a rebuild would immediately invalidate
    itself."""
    live = _live_revisions(cur)
    cur.execute(
        """
        INSERT INTO jobber.economics_rebuild_state
            (singleton, evidence_revision, path_revision, economics_revision, engine_version, rebuilt_at)
        VALUES (true, %s, %s, %s, %s, now())
        ON CONFLICT (singleton) DO UPDATE SET
            evidence_revision = EXCLUDED.evidence_revision,
            path_revision = EXCLUDED.path_revision,
            economics_revision = EXCLUDED.economics_revision,
            engine_version = EXCLUDED.engine_version,
            rebuilt_at = now()
        RETURNING evidence_revision, path_revision, economics_revision, engine_version, rebuilt_at
        """,
        (live["evidence"], live["path"], live["economics"], engine_version),
    )
    return dict(cur.fetchone())


# What each counter's derived output is, in words a user can act on.
_STALE_REASONS = {
    "economics": "compensation evidence or market definitions have changed",
    "path": "roles, archetype assignments or requirement evidence have changed",
    "evidence": "the capability model or your profile evidence mappings have changed",
}


def economics_freshness(cur) -> dict:
    """The freshness verdict every consumer of a derived economics figure
    should check before presenting it.

    `state` is one of:

    - `never_rebuilt` — the derived tables have never been built. Any row in
      them is either absent or from before this check existed.
    - `stale` — source evidence changed after the last rebuild. `stale_inputs`
      names which, in the user's terms.
    - `fresh` — the derived tables reflect the current source state.
    """
    live = _live_revisions(cur)
    cur.execute(
        "SELECT evidence_revision, path_revision, economics_revision, engine_version, rebuilt_at "
        "FROM jobber.economics_rebuild_state WHERE singleton"
    )
    row = cur.fetchone()

    if not row or row["rebuilt_at"] is None:
        return {
            "state": "never_rebuilt",
            "fresh": False,
            "stale_inputs": [],
            "reason": "The derived economics tables have never been rebuilt, so no market benchmark can be "
                      f"presented as current. {REBUILD_HINT}",
            "last_rebuilt_at": None,
            "engine_version": None,
            "live_revisions": live,
            "rebuilt_revisions": None,
        }

    rebuilt = {
        "evidence": row["evidence_revision"],
        "path": row["path_revision"],
        "economics": row["economics_revision"],
    }
    stale_inputs = [name for name, value in rebuilt.items() if value != live[name]]

    if not stale_inputs:
        return {
            "state": "fresh",
            "fresh": True,
            "stale_inputs": [],
            "reason": None,
            "last_rebuilt_at": row["rebuilt_at"],
            "engine_version": row["engine_version"],
            "live_revisions": live,
            "rebuilt_revisions": rebuilt,
        }

    changes = "; ".join(_STALE_REASONS[name] for name in stale_inputs)
    return {
        "state": "stale",
        "fresh": False,
        "stale_inputs": stale_inputs,
        "reason": f"Derived economics were last rebuilt on {row['rebuilt_at'].date().isoformat()}, and since then "
                  f"{changes}. Figures derived from them are withheld rather than shown as current. {REBUILD_HINT}",
        "last_rebuilt_at": row["rebuilt_at"],
        "engine_version": row["engine_version"],
        "live_revisions": live,
        "rebuilt_revisions": rebuilt,
    }
