import { WorkflowLink } from '../WorkflowContextBanner'
import { Link } from 'react-router-dom'
import type {
  AlignmentEconomics,
  DirectConstraint,
  DirectConstraintStatus,
  OpportunityAlignment,
  OpportunityToTarget,
  RelationshipState,
  YouToOpportunity,
} from '../../lib/api'
import { CompensationFigure, PersonalComparisonPanel } from '../economics/Compensation'
import { ArchetypeEvidenceInline } from '../market/ArchetypeEvidencePanel'

// Phase 7 (docs/38): `You -> Opportunity -> Target`. This one file is the
// only place that renders an OpportunityAlignment response — Role Detail,
// the Application workspace, and Pathways' opportunity overlay each compose
// these same pieces differently, but none of them re-derives what a
// relationship state or a constraint status means. Nothing here computes
// anything: every value is already decided server-side (build §25 — no
// AI/scoring on the frontend either).

const RELATIONSHIP_COLOR: Record<RelationshipState, string> = {
  same_destination_family: 'var(--series-1)',
  potential_step: 'var(--good)',
  no_identified_target_progress: 'var(--text-muted)',
  not_more_reachable_than_target: 'var(--warning)',
  relationship_unclear: 'var(--text-muted)',
  target_already_evidenced: 'var(--good)',
}

export function RelationshipBadge({ relationship }: { relationship: NonNullable<OpportunityAlignment['relationship']> }) {
  return (
    <span
      title={relationship.reason}
      style={{
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
        padding: '2px 7px',
        borderRadius: 4,
        color: RELATIONSHIP_COLOR[relationship.state],
        border: `1px solid ${RELATIONSHIP_COLOR[relationship.state]}`,
        whiteSpace: 'nowrap',
      }}
    >
      {relationship.label}
    </span>
  )
}

function targetGapsSummaryLine(alignment: OpportunityAlignment): string | null {
  const o2t = alignment.opportunity_to_target
  if (!o2t) return null
  const total = o2t.target_gaps_involved.length + o2t.target_gaps_not_touched.length
  if (total === 0) return null
  return `Involves ${o2t.target_gaps_involved.length} of your ${total} outstanding Target requirement${total === 1 ? '' : 's'}.`
}

/** The compact Decision Summary tile — read by Role Detail (replacing the
 * pre-Phase-7 CareerDirectionTile, which only ever named the direction) and
 * by the Application workspace's compact summary. Every one of the four
 * backend states (build §3) is a normal, honest state here, never an error:
 * no selected direction, a direction with no linked Target, a Target whose
 * evidence is not yet reviewable, or a full relationship. A fetch failure is
 * the only thing that renders as an error, and it never blocks the rest of
 * the page around it (build §26). */
export function AlignmentDecisionTile({
  alignment,
  error,
  onRetry,
}: {
  alignment: OpportunityAlignment | null
  error: string | null
  onRetry?: () => void
}) {
  return (
    <section className="card" aria-labelledby="ds-alignment-h">
      <h3 id="ds-alignment-h" style={{ marginTop: 0, fontSize: 14 }}>
        Career direction
      </h3>
      {error ? (
        <div role="alert" style={{ fontSize: 13 }}>
          <p style={{ margin: 0, color: 'var(--critical)' }}>Career alignment couldn't be loaded.</p>
          {onRetry && (
            <button type="button" onClick={onRetry} style={{ marginTop: 6 }}>
              Retry
            </button>
          )}
        </div>
      ) : !alignment ? (
        <p className="muted">Loading…</p>
      ) : alignment.state === 'no_selected_direction' ? (
        <>
          <p className="secondary" style={{ fontSize: 13, margin: 0 }}>
            {alignment.message}
          </p>
          <p style={{ marginTop: 10, marginBottom: 0 }}>
            <Link to="/future">Explore my future</Link>
          </p>
        </>
      ) : (
        <>
          {alignment.direction && (
            <p style={{ fontSize: 13, margin: 0 }}>
              <strong>Current direction:</strong> {alignment.direction.name}
            </p>
          )}
          {alignment.state === 'direction_without_target' && (
            <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
              No concrete Target linked — direction constraints can be checked, but structural route analysis needs a
              Target.
            </p>
          )}
          {alignment.state === 'insufficient_target_evidence' && (
            <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
              {alignment.target && <>Target: {alignment.target.title}. </>}
              The Target still has unresolved requirement review or mapping — a structural comparison isn't available
              yet.
            </p>
          )}
          {alignment.state === 'target_available' && alignment.relationship && (
            <div style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span className="secondary" style={{ fontSize: 12 }}>
                  Relationship:
                </span>
                <RelationshipBadge relationship={alignment.relationship} />
              </div>
              {targetGapsSummaryLine(alignment) && (
                <p className="secondary" style={{ fontSize: 12, margin: '6px 0 0' }}>
                  {targetGapsSummaryLine(alignment)}
                </p>
              )}
            </div>
          )}
          <p style={{ marginTop: 10, marginBottom: 0 }}>
            {alignment.direction ? (
              <Link to={`/future/directions/${alignment.direction.id}`}>Open direction</Link>
            ) : (
              <Link to="/future">Explore my future</Link>
            )}
          </p>
        </>
      )}
    </section>
  )
}

const STATUS_LABEL: Record<string, string> = {
  evidenced: 'evidenced',
  partial: 'partially evidenced',
  user_asserted: 'user asserted',
  not_found: 'not found',
}
const STATUS_COLOR: Record<string, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

function YouToOpportunityPanel({ data, roleId }: { data: YouToOpportunity; roleId: string }) {
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
        {(Object.keys(STATUS_LABEL) as (keyof typeof STATUS_LABEL)[]).map((s) => (
          <span key={s}>
            <strong style={{ color: STATUS_COLOR[s] }}>{data.counts[s as keyof typeof data.counts]}</strong>{' '}
            <span className="muted">{STATUS_LABEL[s]}</span>
          </span>
        ))}
      </div>
      {data.blocking_gaps.length > 0 && (
        <p style={{ color: 'var(--critical)', margin: '8px 0 0' }}>
          Required gaps: {data.blocking_gaps.map((g) => g.canonical_name).join(', ')}
        </p>
      )}
      {data.unverified_required.length > 0 && (
        <p style={{ color: 'var(--warning)', margin: '4px 0 0' }}>
          Unverified required: {data.unverified_required.map((g) => g.canonical_name).join(', ')}
        </p>
      )}
      {data.legacy_requirement_count > 0 && (
        <p className="muted" style={{ margin: '6px 0 0', fontSize: 12 }}>
          Includes {data.legacy_requirement_count} legacy (unreviewed) requirement
          {data.legacy_requirement_count === 1 ? '' : 's'} — shown as context, never treated as reviewed.
        </p>
      )}
      {!data.review_summary.complete && (
        <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
          This opportunity's requirement review is incomplete:{' '}
          {data.review_blockers.map((b) => `${b.count} ${b.label}`).join('; ')}.
        </p>
      )}
      <p style={{ marginTop: 10, marginBottom: 0 }}>
        <WorkflowLink to={`/comparison/${roleId}`}>Review evidence in detail</WorkflowLink>
      </p>
    </div>
  )
}

function ConceptList({ items, emptyLabel }: { items: { canonical_name: string | null }[]; emptyLabel: string }) {
  if (items.length === 0) {
    return (
      <p className="muted" style={{ fontSize: 13, margin: '4px 0 0' }}>
        {emptyLabel}
      </p>
    )
  }
  return (
    <ul className="secondary" style={{ fontSize: 13, margin: '4px 0 0', paddingLeft: 18 }}>
      {items.map((item, i) => (
        <li key={i}>{item.canonical_name}</li>
      ))}
    </ul>
  )
}

/** Section 10's three groups, verbatim: what this opportunity involves,
 * what it leaves untouched, and what it additionally demands. Always
 * "involves"/"touches", never "teaches"/"will give you" (build §7/§10) — a
 * job requirement is exposure, not guaranteed capability acquisition. */
function OpportunityToTargetPanel({ data }: { data: OpportunityToTarget }) {
  const n = data.target_gaps_involved.length
  return (
    <div style={{ fontSize: 13 }}>
      <div>
        <strong style={{ fontSize: 13 }}>
          This role involves {n} outstanding Target requirement{n === 1 ? '' : 's'}
        </strong>
        <ConceptList
          items={data.target_gaps_involved}
          emptyLabel="None of your outstanding Target requirements appear on this opportunity."
        />
      </div>
      <div style={{ marginTop: 10 }}>
        <strong style={{ fontSize: 13 }}>Target gaps this opportunity does not touch</strong>
        <ConceptList
          items={data.target_gaps_not_touched}
          emptyLabel="None — every outstanding Target requirement appears on this opportunity."
        />
      </div>
      <div style={{ marginTop: 10 }}>
        <strong style={{ fontSize: 13 }}>Additional demands this opportunity introduces</strong>
        <ConceptList
          items={data.additional_opportunity_demands}
          emptyLabel="None — this opportunity introduces no required demands outside the Target's own requirements."
        />
      </div>
      {(!data.candidate_review_complete || !data.target_review_complete || !data.target_mapping_complete) && (
        <p className="muted" style={{ fontSize: 12, margin: '10px 0 0' }}>
          {!data.candidate_review_complete && "This opportunity's own requirement review is incomplete. "}
          {!data.target_review_complete && "The Target's requirement review is incomplete. "}
          {!data.target_mapping_complete && "The Target's requirement mapping is incomplete. "}
          Read the relationship above as unclear until these are resolved.
        </p>
      )}
      {data.legacy_requirements_involved > 0 && (
        <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
          {data.legacy_requirements_involved} of the involved requirement(s) rest on legacy (unreviewed) evidence.
        </p>
      )}
    </div>
  )
}

const CONSTRAINT_STATUS_STYLE: Record<DirectConstraintStatus, { label: string; color: string }> = {
  matches: { label: 'Matches', color: 'var(--good)' },
  conflicts: { label: 'Conflicts', color: 'var(--critical)' },
  unknown: { label: 'Unknown', color: 'var(--text-muted)' },
  not_specified: { label: 'Not specified', color: 'var(--text-muted)' },
}

function DirectConstraintsPanel({ constraints }: { constraints: DirectConstraint[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {constraints.map((c) => (
        <div key={c.constraint} style={{ fontSize: 13, display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <strong style={{ minWidth: 130 }}>{c.label}</strong>
          <span
            style={{
              color: CONSTRAINT_STATUS_STYLE[c.status].color,
              fontWeight: 600,
              fontSize: 11,
              textTransform: 'uppercase',
              letterSpacing: 0.3,
            }}
          >
            {CONSTRAINT_STATUS_STYLE[c.status].label}
          </span>
          <span className="muted" style={{ fontSize: 12 }}>
            {c.reason}
          </span>
        </div>
      ))}
    </div>
  )
}

function AlignmentEconomicsPanel({ economics }: { economics: AlignmentEconomics }) {
  return (
    <div>
      <CompensationFigure compensation={economics.opportunity} />
      <h4 style={{ fontSize: 13, margin: '16px 0 6px' }}>Your comparison</h4>
      <PersonalComparisonPanel comparison={economics.vs_personal_earnings} />
      {economics.target && (
        <>
          <h4 style={{ fontSize: 13, margin: '16px 0 6px' }}>
            Target compensation — shown for context, never a "which pays more" verdict
          </h4>
          <CompensationFigure compensation={economics.target} />
        </>
      )}
    </div>
  )
}

/** The opportunity title/organisation header Pathways' overlay card needs
 * (it is showing a *different* role than the page it's embedded in) — Role
 * Detail never renders this, since the page's own header already says it. */
export function AlignmentOpportunityHeader({ alignment }: { alignment: OpportunityAlignment }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <strong style={{ fontSize: 14 }}>{alignment.opportunity.title}</strong>
      {alignment.opportunity.organisation && (
        <span className="secondary" style={{ fontSize: 13 }}>
          {' '}
          · {alignment.opportunity.organisation}
        </span>
      )}
    </div>
  )
}

/** The fuller "You -> this opportunity -> Target" section (build §15/§18):
 * You -> Opportunity, Opportunity -> Target, Direction constraints,
 * Economics, and "Why this relationship" — each its own compact card so a
 * missing piece (no Direction, insufficient Target evidence) never blanks
 * the pieces that are available. Used by Role Detail below the Decision
 * Summary tile, and by Pathways' opportunity overlay (behind its own
 * `AlignmentOpportunityHeader`). Not used by the Application workspace,
 * which shows the compact tile only (build §16). */
export function AlignmentFullSection({
  alignment,
  roleId,
  headingId = 'alignment-full-h',
}: {
  alignment: OpportunityAlignment
  roleId: string
  headingId?: string
}) {
  if (alignment.state === 'no_selected_direction') return null

  return (
    <div>
      {(alignment.state === 'direction_without_target' || alignment.state === 'insufficient_target_evidence') &&
        alignment.message && (
          <div className="card" style={{ marginBottom: 12 }}>
            <p className="secondary" style={{ fontSize: 13, margin: 0 }}>
              {alignment.message}
            </p>
          </div>
        )}

      {alignment.you_to_opportunity && (
        <div className="card" style={{ marginBottom: 12 }} aria-labelledby={`${headingId}-y2o`}>
          <h3 id={`${headingId}-y2o`} style={{ marginTop: 0, fontSize: 14 }}>
            You → Opportunity
          </h3>
          <YouToOpportunityPanel data={alignment.you_to_opportunity} roleId={roleId} />
        </div>
      )}

      {alignment.opportunity_to_target && (
        <div className="card" style={{ marginBottom: 12 }} aria-labelledby={`${headingId}-o2t`}>
          <h3 id={`${headingId}-o2t`} style={{ marginTop: 0, fontSize: 14 }}>
            Opportunity → Target
          </h3>
          <OpportunityToTargetPanel data={alignment.opportunity_to_target} />
        </div>
      )}

      {alignment.direction_constraints && alignment.direction_constraints.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }} aria-labelledby={`${headingId}-constraints`}>
          <h3 id={`${headingId}-constraints`} style={{ marginTop: 0, fontSize: 14 }}>
            Direction constraints
          </h3>
          <DirectConstraintsPanel constraints={alignment.direction_constraints} />
        </div>
      )}

      {alignment.economics && (
        <div className="card" style={{ marginBottom: 12 }} aria-labelledby={`${headingId}-economics`}>
          <h3 id={`${headingId}-economics`} style={{ marginTop: 0, fontSize: 14 }}>
            Economics
          </h3>
          <AlignmentEconomicsPanel economics={alignment.economics} />
        </div>
      )}

      {alignment.market_evidence_context && (
        <div className="card" style={{ marginBottom: 12 }} aria-labelledby={`${headingId}-market-evidence`}>
          <h3 id={`${headingId}-market-evidence`} style={{ marginTop: 0, fontSize: 14 }}>
            Market evidence for this pattern
          </h3>
          <ArchetypeEvidenceInline evidence={alignment.market_evidence_context} />
        </div>
      )}

      {alignment.relationship && (
        <div className="card" aria-labelledby={`${headingId}-why`}>
          <h3 id={`${headingId}-why`} style={{ marginTop: 0, fontSize: 14 }}>
            Why this relationship
          </h3>
          <p style={{ fontSize: 13, margin: 0 }}>{alignment.relationship.reason}</p>
          <p className="muted" style={{ fontSize: 12, margin: '8px 0 0' }}>
            {alignment.method.relationship_caveat}
          </p>
        </div>
      )}
    </div>
  )
}
