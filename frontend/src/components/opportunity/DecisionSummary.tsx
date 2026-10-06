import { Link, useSearchParams } from 'react-router-dom'
import { withApplicationContext } from '../../lib/applicationContext'
import {
  type ComparisonResult,
  type ComparisonStatus,
  type OpportunityAlignment,
  type Role,
  type RoleCompensationResponse,
} from '../../lib/api'
import { CompensationFigure, PersonalComparisonPanel } from '../economics/Compensation'
import { AlignmentDecisionTile } from './AlignmentSection'

// The Opportunity Decision Workspace (Phase 2, docs/33) turns a posting's
// detail page into a decision surface: structural facts first, diagnostics
// below. Every count here reads data an existing engine already computed —
// nothing in this file scores, ranks or judges an opportunity, and nothing
// here makes a network call of its own. Callers own fetching (once) and pass
// results down, so the compact tiles here and the full detail lower on the
// page never issue a second request for the same state.

const STATUS_LABEL: Record<ComparisonStatus, string> = {
  evidenced: 'Evidenced',
  partial: 'Partially evidenced',
  user_asserted: 'User asserted',
  not_found: 'No evidence found',
}

const STATUS_COLOR: Record<ComparisonStatus, string> = {
  evidenced: 'var(--good)',
  partial: 'var(--warning)',
  user_asserted: 'var(--series-1)',
  not_found: 'var(--muted, #888)',
}

function pluralize(n: number, noun: string, plural = `${noun}s`): string {
  return `${n} ${n === 1 ? noun : plural}`
}

/** The pre-Phase-2 "review pending" notice, unchanged verbatim (Workflows.
 * test.tsx asserts this exact copy) but now reusable: postings show it inside
 * "What this role asks for" (section 3), targets keep it at the page's
 * bottom exactly where it always was — see RoleDetail. */
export function RequirementReviewPendingNotice({ role }: { role: Role }) {
  if (!role.requirement_review || role.requirement_review.complete) return null
  const { unreviewed, unresolved_proposals, needs_reextraction } = role.requirement_review
  const total = unreviewed + unresolved_proposals + needs_reextraction
  return (
    <p style={{ marginTop: 16, fontSize: 13, color: 'var(--warning)' }}>
      Requirements review pending — {total} item
      {total === 1 ? '' : 's'} not yet reviewed
      {unresolved_proposals > 0 ? ' (including terms not yet matched to the vocabulary)' : ''}
      {needs_reextraction > 0 ? ' (including terms newly added to the vocabulary awaiting re-extraction)' : ''} and
      excluded from comparison/analysis.{' '}
      <Link to={withApplicationContext(`/role-instances/${role.id}/requirements`, applicationId)}>Review now</Link>
    </p>
  )
}

const REQUIREMENT_GROUPS: { type: string; label: string }[] = [
  { type: 'required', label: 'Required' },
  { type: 'preferred', label: 'Preferred' },
  { type: 'contextual', label: 'Contextual' },
]

/** Section 3: "What this role asks for" — a grouped preview of the accepted/
 * reviewed requirements, before the full chip list further down the page.
 * Never invents importance the data doesn't have, and never upgrades legacy
 * (unreviewed) skills into this reviewed picture. */
export function RequirementsAskFor({ role }: { role: Role }) {
  const reviewed = role.skills ?? []
  const legacyCount = role.legacy_skills?.length ?? 0
  const review = role.requirement_review
  const neverExtracted = review ? !review.extraction_attempted : false
  // Extraction can have run and produced only pending work — items awaiting
  // review, unresolved vocabulary terms, or claims awaiting re-extraction —
  // with zero currently accepted. That is not "nothing extracted"; the
  // pending notice below already says so, so this prose stays out of its way
  // instead of contradicting it.
  const hasPending = review ? review.unreviewed + review.unresolved_proposals + review.needs_reextraction > 0 : false

  return (
    <section className="card" style={{ marginTop: 16 }} aria-labelledby="asks-for-h">
      <h2 id="asks-for-h" style={{ fontSize: 18, marginTop: 0 }}>
        What this role asks for
      </h2>

      {reviewed.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {REQUIREMENT_GROUPS.map(({ type, label }) => {
            const items = reviewed.filter((s) => s.requirement_type === type)
            if (items.length === 0) return null
            return (
              <div key={type}>
                <strong style={{ fontSize: 13 }}>
                  {label} ({items.length})
                </strong>
                <div className="secondary" style={{ fontSize: 13 }}>
                  {items.map((s) => s.name).join(', ')}
                </div>
              </div>
            )
          })}
        </div>
      ) : neverExtracted ? (
        <p className="muted" style={{ fontSize: 13 }}>
          Requirements haven't been extracted from this posting yet.
        </p>
      ) : hasPending ? (
        <p className="muted" style={{ fontSize: 13 }}>
          Nothing accepted yet — see below.
        </p>
      ) : legacyCount > 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          No reviewed requirements yet — {pluralize(legacyCount, 'legacy, unreviewed skill')} extracted from the
          source (see Legacy skills below). Legacy extraction is never silently upgraded into a reviewed
          requirement.
        </p>
      ) : (
        <p className="muted" style={{ fontSize: 13 }}>
          No requirements extracted for this role yet.
        </p>
      )}

      <RequirementReviewPendingNotice role={role} />

      <p style={{ marginTop: 12, marginBottom: 0 }}>
        <Link to={withApplicationContext(`/role-instances/${role.id}/requirements`, applicationId)}>Review requirements</Link>
      </p>
    </section>
  )
}

function RequirementsStateTile({ role }: { role: Role }) {
  const review = role.requirement_review
  const legacyCount = role.legacy_skills?.length ?? 0

  return (
    <section className="card" aria-labelledby="ds-requirements-h">
      <h3 id="ds-requirements-h" style={{ marginTop: 0, fontSize: 14 }}>
        Requirements
      </h3>
      {!review ? (
        <p className="muted" style={{ fontSize: 13 }}>
          Requirement review data unavailable.
        </p>
      ) : !review.extraction_attempted && review.accepted === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          Not yet extracted{legacyCount > 0 ? ` — ${pluralize(legacyCount, 'legacy skill')} on file` : ''}.
        </p>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 13 }}>
          <div>{pluralize(review.accepted, 'reviewed requirement')}</div>
          {review.unreviewed + review.needs_reextraction > 0 && (
            <div className="secondary">{pluralize(review.unreviewed + review.needs_reextraction, 'item')} still need review</div>
          )}
          {review.unresolved_proposals > 0 && (
            <div className="secondary">{pluralize(review.unresolved_proposals, 'unresolved vocabulary term')}</div>
          )}
          {review.complete && review.accepted > 0 && (
            <div className="muted">Review complete.</div>
          )}
        </div>
      )}
    </section>
  )
}

function EvidenceStateTile({
  comparison,
  error,
  roleId,
  onRetry,
}: {
  comparison: ComparisonResult | null
  error: string | null
  roleId: string
  onRetry: () => void
}) {
  return (
    <section className="card" aria-labelledby="ds-evidence-h">
      <h3 id="ds-evidence-h" style={{ marginTop: 0, fontSize: 14 }}>
        Evidence
      </h3>
      {error && (
        <div role="alert" style={{ fontSize: 13 }}>
          <p style={{ margin: 0, color: 'var(--critical)' }}>Evidence comparison couldn't be loaded.</p>
          <button type="button" onClick={onRetry} style={{ marginTop: 6 }}>
            Retry
          </button>
        </div>
      )}
      {!comparison && !error && <p className="muted">Loading…</p>}
      {comparison && comparison.items.length === 0 ? (
        <p className="muted" style={{ fontSize: 13 }}>
          No reviewed requirements to compare yet.
        </p>
      ) : (
        comparison && (
          <div style={{ fontSize: 13 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {(Object.keys(STATUS_LABEL) as ComparisonStatus[]).map((s) => (
                <span key={s}>
                  <strong style={{ color: STATUS_COLOR[s] }}>{comparison.counts[s]}</strong>{' '}
                  <span className="muted">{STATUS_LABEL[s].toLowerCase()}</span>
                </span>
              ))}
            </div>
            {comparison.blocking_gaps.length > 0 && (
              <p style={{ color: 'var(--critical)', margin: '8px 0 0' }}>
                {pluralize(comparison.blocking_gaps.length, 'required blocking gap')}:{' '}
                {comparison.blocking_gaps.map((g) => g.canonical_name).join(', ')}
              </p>
            )}
            {comparison.unverified_required.length > 0 && (
              <p style={{ color: 'var(--warning)', margin: '4px 0 0' }}>
                {pluralize(comparison.unverified_required.length, 'required capability')} not fully verified
              </p>
            )}
            <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>
              No accepted evidence found does not mean you lack the capability.
            </p>
          </div>
        )
      )}
      <p style={{ marginTop: 10, marginBottom: 0 }}>
        <Link to={withApplicationContext(`/comparison/${roleId}`, applicationId)}>Review evidence in detail</Link>
      </p>
    </section>
  )
}

/** The read-only headline — basis, figure and personal comparison — factored
 * out so it renders exactly once per page. For a posting this tile is that
 * one place; the detailed Economics section further down (RoleEconomicsSection)
 * shows only the operational controls (extract, review, archetype, pathways)
 * and does not repeat it. Targets have no Decision Summary, so their detailed
 * section keeps showing this same headline itself — see RoleEconomicsSection. */
function EconomicsStateTile({
  compensation,
  error,
}: {
  compensation: RoleCompensationResponse | null
  error: string | null
}) {
  return (
    <section className="card" aria-labelledby="ds-economics-h">
      <h3 id="ds-economics-h" style={{ marginTop: 0, fontSize: 14 }}>
        Economics
      </h3>
      {error && (
        <p role="alert" style={{ color: 'var(--critical)', fontSize: 13 }}>
          Economics couldn't be loaded.
        </p>
      )}
      {!compensation && !error && <p className="muted">Loading…</p>}
      {compensation && (
        <>
          <CompensationFigure compensation={compensation.compensation} />
          <h4 style={{ fontSize: 13, margin: '16px 0 6px' }}>Your comparison</h4>
          <PersonalComparisonPanel comparison={compensation.personal_comparison} />
        </>
      )}
    </section>
  )
}

/** Section 2: the decision summary — structural facts, never a verdict.
 * Posting-only (see RoleDetail's `isTarget` gate); a target keeps its own
 * Explore-my-future framing instead. */
export function DecisionSummary({
  role,
  comparison,
  comparisonError,
  onRetryComparison,
  compensation,
  compensationError,
  alignment,
  alignmentError,
  onRetryAlignment,
}: {
  role: Role
  comparison: ComparisonResult | null
  comparisonError: string | null
  onRetryComparison: () => void
  compensation: RoleCompensationResponse | null
  compensationError: string | null
  alignment: OpportunityAlignment | null
  alignmentError: string | null
  onRetryAlignment: () => void
}) {
  return (
    <section aria-labelledby="decision-summary-h" style={{ marginTop: 16 }}>
      <h2 id="decision-summary-h" style={{ fontSize: 18, marginBottom: 8 }}>
        Decision summary
      </h2>
      <div className="home-grid" style={{ marginTop: 0 }}>
        <RequirementsStateTile role={role} />
        <EvidenceStateTile comparison={comparison} error={comparisonError} roleId={role.id} onRetry={onRetryComparison} />
        <EconomicsStateTile compensation={compensation} error={compensationError} />
        <AlignmentDecisionTile alignment={alignment} error={alignmentError} onRetry={onRetryAlignment} />
      </div>
    </section>
  )
}
