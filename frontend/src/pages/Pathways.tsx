import { useCallback, useEffect, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  api,
  type ArchetypeContextResponse,
  type DirectRoute,
  type GapValueItem,
  type IntermediateArchetypeRoute,
  type PathwaysResult,
  type PersonalEarningsState,
  type Role,
} from '../lib/api'
import {
  CompensationFigure,
  EvidenceQualityBadge,
  PersonalComparisonPanel,
  StaleEconomicsNotice,
} from '../components/economics/Compensation'
import { PlanningAssumptionEditor } from '../components/economics/PlanningAssumption'
import { DayInTheLife } from '../components/economics/DayInTheLife'
import { formatMoney } from '../lib/money'

type Perspective = 'economics' | 'fit' | 'transition' | 'day'

const PERSPECTIVES: { key: Perspective; label: string }[] = [
  { key: 'economics', label: 'Economics' },
  { key: 'fit', label: 'Fit' },
  { key: 'transition', label: 'Transition' },
  { key: 'day', label: 'Day in the life' },
]

// Each route node's state gets a colour and a plain-English heading. An
// incomplete or insufficient state is shown as itself, never smoothed into
// something more confident.
const STATE_STYLE: Record<string, { colour: string; label: string }> = {
  structurally_evidenced: { colour: 'var(--good)', label: 'Every required capability is evidenced' },
  unverified_gaps: { colour: 'var(--warning)', label: 'Required capabilities only partly evidenced' },
  blocking_gaps: { colour: 'var(--critical)', label: 'Required capabilities with no evidence' },
  review_incomplete: { colour: 'var(--warning)', label: 'Requirement review incomplete' },
  mapping_incomplete: { colour: 'var(--warning)', label: 'Requirement mapping incomplete' },
  insufficient_evidence: { colour: 'var(--text-muted)', label: 'Insufficient evidence' },
  no_required_requirements: { colour: 'var(--text-muted)', label: 'No required requirements' },
  useful_intermediate: { colour: 'var(--good)', label: 'Useful intermediate step' },
  route_without_compensation: { colour: 'var(--warning)', label: 'Route with no compensation benchmark' },
  no_target_progress: { colour: 'var(--text-muted)', label: 'No progress towards the target' },
}

function StateBanner({ state, reason }: { state: string; reason: string }) {
  const style = STATE_STYLE[state] ?? { colour: 'var(--text-muted)', label: state }
  return (
    <div style={{ borderLeft: `3px solid ${style.colour}`, paddingLeft: 10, margin: '8px 0 12px' }}>
      <div style={{ fontWeight: 600, fontSize: 13, color: style.colour }}>{style.label}</div>
      <div className="secondary" style={{ fontSize: 13 }}>
        {reason}
      </div>
    </div>
  )
}

function GapList({ title, names, colour }: { title: string; names: string[]; colour: string }) {
  if (names.length === 0) return null
  return (
    <div style={{ marginTop: 8 }}>
      <div className="muted" style={{ fontSize: 12 }}>
        {title}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
        {names.map((name) => (
          <span
            key={name}
            style={{ fontSize: 12, padding: '2px 7px', borderRadius: 4, border: `1px solid ${colour}`, color: colour }}
          >
            {name}
          </span>
        ))}
      </div>
    </div>
  )
}

function TransitionPanel({ transition }: { transition: DirectRoute['transition'] }) {
  const actions = transition.development_actions
  return (
    <div>
      <GapList
        title="Target gaps this addresses"
        names={transition.target_gaps_addressed}
        colour="var(--series-1)"
      />
      <GapList
        title="Blocking required gaps"
        names={transition.blocking_required_gap_names}
        colour="var(--critical)"
      />
      <GapList
        title="Unverified required gaps"
        names={transition.unverified_required_gap_names}
        colour="var(--warning)"
      />

      <div style={{ marginTop: 12 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          Your development actions
        </div>
        <div className="secondary" style={{ fontSize: 13 }}>
          {actions.open} open, {actions.done} done
          {actions.earliest_planned_start ? ` · earliest planned start ${actions.earliest_planned_start}` : ''}
          {actions.estimated_effort_hours !== null
            ? ` · ${actions.estimated_effort_hours}h estimated across ${actions.actions_with_estimated_effort} action(s)`
            : ''}
        </div>
      </div>

      <details style={{ marginTop: 12 }}>
        <summary className="muted" style={{ fontSize: 12, cursor: 'pointer' }}>
          What this does not estimate
        </summary>
        <ul className="muted" style={{ fontSize: 12, margin: '6px 0 0', paddingLeft: 18 }}>
          {transition.not_estimated.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}

function ArchetypeDayInTheLife({ archetypeId }: { archetypeId: string }) {
  const [context, setContext] = useState<ArchetypeContextResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setContext(null)
    setError(null)
    api
      .getArchetypeContext(archetypeId)
      .then(setContext)
      .catch((e) => setError(String(e)))
  }, [archetypeId])

  const generate = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.generateArchetypeContext(archetypeId)
      setContext(await api.getArchetypeContext(archetypeId))
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  if (error) return <p style={{ color: 'var(--critical)', fontSize: 13 }}>{error}</p>
  if (!context) return <p className="muted">Loading…</p>
  if (!context.enrichment) {
    return (
      <div>
        <p className="secondary" style={{ fontSize: 13 }}>
          No archetype context has been generated yet. Generating it is an explicit action and calls the model once.
        </p>
        <button type="button" onClick={generate} disabled={busy}>
          {busy ? 'Generating…' : 'Generate archetype context'}
        </button>
      </div>
    )
  }
  return <DayInTheLife context={context.enrichment} synthesisNote={context.synthesis_note} />
}

function NodeCard({
  title,
  subtitle,
  state,
  reason,
  economics,
  fit,
  transition,
  day,
  footer,
}: {
  title: React.ReactNode
  subtitle?: React.ReactNode
  state: string
  reason: string
  economics: React.ReactNode
  fit: React.ReactNode
  transition: React.ReactNode
  day: React.ReactNode
  footer?: React.ReactNode
}) {
  const [perspective, setPerspective] = useState<Perspective>('economics')
  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{title}</div>
      {subtitle && (
        <div className="secondary" style={{ fontSize: 13 }}>
          {subtitle}
        </div>
      )}
      <StateBanner state={state} reason={reason} />

      <div role="tablist" aria-label="Perspectives" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {PERSPECTIVES.map((p) => (
          <button
            key={p.key}
            type="button"
            role="tab"
            aria-selected={perspective === p.key}
            onClick={() => setPerspective(p.key)}
            style={{
              fontSize: 12,
              padding: '4px 10px',
              borderRadius: 4,
              border: '1px solid var(--border)',
              background: perspective === p.key ? 'var(--surface-1)' : 'transparent',
              fontWeight: perspective === p.key ? 600 : 400,
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div style={{ marginTop: 12 }}>
        {perspective === 'economics' && economics}
        {perspective === 'fit' && fit}
        {perspective === 'transition' && transition}
        {perspective === 'day' && day}
      </div>

      {footer}
    </div>
  )
}

function DirectRouteCard({ route }: { route: DirectRoute }) {
  return (
    <NodeCard
      title={<>Direct: {route.title}</>}
      subtitle={
        <>
          {route.organisation ?? 'No organisation'}
          {route.archetype ? ` · ${route.archetype.name}` : ' · unclassified'}
        </>
      }
      state={route.state}
      reason={route.state_reason}
      economics={
        <div>
          <CompensationFigure compensation={route.compensation} />
          <div style={{ marginTop: 12 }}>
            <PersonalComparisonPanel comparison={route.personal_comparison} />
          </div>
        </div>
      }
      fit={
        <div>
          <div className="secondary" style={{ fontSize: 13 }}>
            {route.fit.evidenced_requirements} of {route.fit.requirements_total} reviewed requirements evidenced
            {route.fit.evidence_coverage !== null
              ? ` (${Math.round(route.fit.evidence_coverage * 100)}%)`
              : ''}
          </div>
          <GapList title="Blocking gaps" names={route.fit.blocking_required_gaps} colour="var(--critical)" />
          <GapList title="Unverified gaps" names={route.fit.unverified_required_gaps} colour="var(--warning)" />
          <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            Requirement review:{' '}
            {route.fit.review_complete
              ? 'complete'
              : route.fit.review_blockers.map((b) => `${b.count} ${b.label}`).join('; ')}
            {' · '}
            Vocabulary mapping: {route.fit.mapping_complete ? 'complete' : `${route.fit.unmapped_requirements} unresolved`}
          </div>
        </div>
      }
      transition={<TransitionPanel transition={route.transition} />}
      day={
        <p className="secondary" style={{ fontSize: 13 }}>
          Open <Link to={`/roles/${route.role_instance_id}`}>{route.title}</Link> to read or generate its Day in the
          Life.
        </p>
      }
    />
  )
}

function IntermediateCard({ node }: { node: IntermediateArchetypeRoute }) {
  return (
    <NodeCard
      title={<>Via: {node.archetype_name}</>}
      subtitle={
        <>
          {node.seniority_band ?? 'No seniority band'} · {node.supporting_posting_count} supporting posting
          {node.supporting_posting_count === 1 ? '' : 's'}
        </>
      }
      state={node.state}
      reason={node.state_reason}
      economics={
        <div>
          <CompensationFigure compensation={node.compensation} />
          <div style={{ marginTop: 12 }}>
            <PersonalComparisonPanel comparison={node.personal_comparison} />
          </div>
        </div>
      }
      fit={
        <div>
          <div className="secondary" style={{ fontSize: 13 }}>
            Best evidence coverage across the supporting postings:{' '}
            {node.fit.best_evidence_coverage !== null
              ? `${Math.round(node.fit.best_evidence_coverage * 100)}%`
              : 'unknown'}
          </div>
          <GapList title="Blocking gaps" names={node.fit.blocking_required_gaps} colour="var(--critical)" />
          <GapList title="Unverified gaps" names={node.fit.unverified_required_gaps} colour="var(--warning)" />
          <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
            Requirement review:{' '}
            {node.fit.review_complete
              ? 'complete'
              : node.fit.review_blockers.map((b) => `${b.count} ${b.label}`).join('; ')}
          </div>
        </div>
      }
      transition={<TransitionPanel transition={node.transition} />}
      day={<ArchetypeDayInTheLife archetypeId={node.archetype_concept_id} />}
      footer={
        <details style={{ marginTop: 14 }}>
          <summary style={{ cursor: 'pointer', fontSize: 13 }}>
            Supporting postings ({node.supporting_posting_count})
          </summary>
          <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
            {node.supporting_postings.map((posting) => (
              <li key={posting.id} style={{ marginBottom: 8 }}>
                <Link to={`/roles/${posting.id}`}>{posting.title}</Link>
                <div className="secondary" style={{ fontSize: 13 }}>
                  {posting.organisation ?? 'No organisation'}
                  {posting.posting_date ? ` · ${posting.posting_date}` : ''}
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {posting.evidenced_requirements}/{posting.requirements_total} requirements evidenced
                  {posting.target_gaps_addressed.length > 0
                    ? ` · involves ${posting.target_gaps_addressed.join(', ')}`
                    : ''}
                </div>
              </li>
            ))}
          </ul>
        </details>
      }
    />
  )
}

function GapValueCard({ gap }: { gap: GapValueItem }) {
  const market = gap.market_option_value
  return (
    <div className="card" style={{ padding: 14 }}>
      <div style={{ fontWeight: 600 }}>{gap.canonical_name}</div>

      <div style={{ marginTop: 10 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          Target relevance
        </div>
        <div className="secondary" style={{ fontSize: 13 }}>
          Required by this target · addresses {gap.target_relevance.addresses_target_gaps} current target gap
          {gap.target_relevance.addresses_target_gaps === 1 ? '' : 's'}
          {gap.evidence_status ? ` · currently ${gap.evidence_status.replace('_', ' ')}` : ''}
        </div>
        {gap.target_relevance.intermediate_archetypes_involving_it.length > 0 && (
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            Involved in:{' '}
            {gap.target_relevance.intermediate_archetypes_involving_it.map((a) => a.archetype_name).join(', ')}
          </div>
        )}
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          Market option value
        </div>
        {market.available ? (
          <div className="secondary" style={{ fontSize: 13 }}>
            {market.archetypes_unlocked} archetype{market.archetypes_unlocked === 1 ? '' : 's'} unlocked,{' '}
            {market.archetypes_improved} improved
            {market.highest_qualifying_reference_compensation !== null && (
              <>
                <br />
                Highest qualifying reference compensation:{' '}
                {formatMoney(market.highest_qualifying_reference_compensation, market.currency)}
              </>
            )}
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
              {market.scope_note}
            </div>
          </div>
        ) : (
          <div className="muted" style={{ fontSize: 13 }}>
            {market.reason}
          </div>
        )}
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          Your context
        </div>
        <PersonalComparisonPanel comparison={gap.your_context as never} compact />
      </div>

      <div style={{ marginTop: 10 }}>
        <EvidenceQualityBadge quality={gap.evidence_quality} />
      </div>
    </div>
  )
}

function EarningsHeader({ earnings }: { earnings: PersonalEarningsState }) {
  if (earnings.status === 'unavailable' || earnings.status === 'profile360_unavailable') {
    return (
      <div>
        <div className="muted" style={{ fontSize: 12 }}>
          Your earnings
        </div>
        <div className="secondary" style={{ fontSize: 13 }}>
          {earnings.notes[0] ?? 'No accepted personal compensation evidence.'}
        </div>
      </div>
    )
  }
  return (
    <div>
      <div className="muted" style={{ fontSize: 12 }}>
        {earnings.status === 'current' ? 'Current earnings' : 'Latest known earnings'}
      </div>
      {earnings.baselines.map((baseline) => (
        <div key={baseline.observation_id} style={{ marginTop: 2 }}>
          <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
            {formatMoney(baseline.amount, baseline.currency ?? '')}
            {baseline.unit && baseline.unit !== 'annual' ? ` / ${baseline.unit}` : ''}
          </span>
          <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
            {baseline.employment_basis ?? 'unknown basis'} · {baseline.component.replace('_', ' ')}
            {baseline.evidence_period ? ` · ${baseline.evidence_period}` : ''}
            {baseline.episode_title ? ` · ${baseline.episode_title}` : ''}
          </span>
          {baseline.evidence_status_reason && (
            <div className="muted" style={{ fontSize: 12 }}>
              {baseline.evidence_status_reason}
            </div>
          )}
          {baseline.planning_equivalent && (
            <div className="muted" style={{ fontSize: 12 }}>
              Planning equivalent{' '}
              {formatMoney(baseline.planning_equivalent.amount, baseline.planning_equivalent.currency)} (
              {baseline.planning_equivalent.label}). {baseline.planning_equivalent.caveat}
            </div>
          )}
        </div>
      ))}
      {earnings.notes.map((note) => (
        <div key={note} className="muted" style={{ fontSize: 12, marginTop: 2 }}>
          {note}
        </div>
      ))}
    </div>
  )
}

export default function Pathways() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [targets, setTargets] = useState<Role[]>([])
  const [result, setResult] = useState<PathwaysResult | null>(null)
  const [contextKey, setContextKey] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)

  useEffect(() => {
    api.listTargets().then(setTargets).catch((e) => setError(String(e)))
  }, [])

  const load = useCallback(
    (targetId: string, selection: string) => {
      setLoading(true)
      setError(null)
      const [marketId, currency] = selection ? selection.split('::') : [undefined, undefined]
      api
        .getPathways(targetId, { market_id: marketId, currency })
        .then(setResult)
        .catch((e) => setError(String(e)))
        .finally(() => setLoading(false))
    },
    [],
  )

  useEffect(() => {
    if (id) load(id, contextKey)
    else setResult(null)
  }, [id, contextKey, load])

  const reload = () => {
    if (id) load(id, contextKey)
  }

  const rebuildEconomics = async () => {
    setRebuilding(true)
    setError(null)
    try {
      await api.rebuildEconomics()
      reload()
    } catch (e) {
      setError(String(e))
    } finally {
      setRebuilding(false)
    }
  }

  // The day rate the contract assumption would apply to, so the editor can
  // show the actual arithmetic rather than an abstract multiplier.
  const contractBaseline = result?.personal_earnings.baselines.find(
    (baseline) => baseline.component === 'day_rate' || baseline.unit === 'daily',
  )

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Pathways</h1>
      <p className="secondary" style={{ marginTop: 4 }}>
        Where you are, where you could go, what each destination pays, and which intermediate moves are actually
        supported by evidence. Compensation never affects where roles sit in the Role map — that stays semantic.
      </p>

      <div className="card" style={{ padding: 14, marginTop: 12, display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span className="muted" style={{ fontSize: 12 }}>
            Target
          </span>
          <select
            value={id ?? ''}
            onChange={(e) => navigate(e.target.value ? `/pathways/${e.target.value}` : '/pathways')}
          >
            <option value="">Choose a target…</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
              </option>
            ))}
          </select>
        </label>

        {result && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="muted" style={{ fontSize: 12 }}>
              Market and currency
            </span>
            <select value={contextKey} onChange={(e) => setContextKey(e.target.value)}>
              <option value="">
                {result.market_context.market_label
                  ? `Automatic — ${result.market_context.market_label} (${result.market_context.currency})`
                  : 'Automatic — no compensation evidence yet'}
              </option>
              {result.available_market_contexts.map((context) => (
                <option key={`${context.market_id}::${context.currency}`} value={`${context.market_id}::${context.currency}`}>
                  {context.market_label} ({context.currency}) · {context.accepted_observations} observations
                </option>
              ))}
            </select>
          </label>
        )}

        {result && <EarningsHeader earnings={result.personal_earnings} />}

        {result && (
          <PlanningAssumptionEditor
            dayRate={contractBaseline?.amount ?? null}
            currency={contractBaseline?.currency ?? null}
            onChanged={reload}
          />
        )}
      </div>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      {!id && !loading && (
        <p className="muted" style={{ marginTop: 16 }}>
          Choose a target above to see the direct route and the intermediate archetypes that could get you there.
          No targets yet? <Link to="/targets/new">Add one</Link>.
        </p>
      )}

      {result && !loading && (
        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <StaleEconomicsNotice
            freshness={result.economics_freshness}
            onRebuild={rebuildEconomics}
            busy={rebuilding}
          />

          {result.incomplete.length > 0 && (
            <div className="card" style={{ padding: 12, borderLeft: '3px solid var(--warning)' }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>Some inputs are incomplete</div>
              <ul className="secondary" style={{ fontSize: 13, margin: '6px 0 0', paddingLeft: 18 }}>
                {result.incomplete.map((gate) => (
                  <li key={gate}>{gate.replace(/_/g, ' ')}</li>
                ))}
              </ul>
              <p className="muted" style={{ fontSize: 12, margin: '6px 0 0' }}>
                Results below are shown with explicit states rather than confident numbers where an input is missing.
              </p>
            </div>
          )}

          <section>
            <h2 style={{ fontSize: 16, margin: '0 0 8px' }}>Direct route</h2>
            <DirectRouteCard route={result.direct_route} />
          </section>

          <section>
            <h2 style={{ fontSize: 16, margin: '0 0 8px' }}>
              One-step intermediate archetypes ({result.intermediate_archetypes.length})
            </h2>
            {result.intermediate_archetypes.length === 0 ? (
              <p className="muted" style={{ fontSize: 13 }}>
                No reviewed archetype currently groups postings that would move you towards this target.
                {result.unclassified_supporting_postings.length > 0 &&
                  ' Some useful postings are unclassified — assigning them an archetype would surface them here.'}
              </p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {result.intermediate_archetypes.map((node) => (
                  <IntermediateCard key={node.archetype_concept_id} node={node} />
                ))}
              </div>
            )}

            {result.unclassified_supporting_postings.length > 0 && (
              <details style={{ marginTop: 10 }}>
                <summary className="muted" style={{ fontSize: 13, cursor: 'pointer' }}>
                  Useful postings with no reviewed archetype ({result.unclassified_supporting_postings.length})
                </summary>
                <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                  {result.unclassified_supporting_postings.map((posting) => (
                    <li key={posting.id}>
                      <Link to={`/roles/${posting.id}`}>{posting.title}</Link>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {posting.target_gaps_addressed.length > 0
                          ? ` — involves ${posting.target_gaps_addressed.join(', ')}`
                          : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>

          <section>
            <h2 style={{ fontSize: 16, margin: '0 0 8px' }}>Value of the gaps ({result.gap_value.length})</h2>
            {result.gap_value.length === 0 ? (
              <p className="muted" style={{ fontSize: 13 }}>
                No outstanding required capability gaps for this target.
              </p>
            ) : (
              <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
                {result.gap_value.map((gap) => (
                  <GapValueCard key={gap.concept_id} gap={gap} />
                ))}
              </div>
            )}
          </section>

          <details className="card" style={{ padding: 12 }}>
            <summary style={{ cursor: 'pointer', fontSize: 13 }}>Method and limitations</summary>
            <ul className="secondary" style={{ fontSize: 13, margin: '8px 0 0', paddingLeft: 18 }}>
              <li>{result.method.route_depth_limitation}</li>
              <li>{result.method.intermediate_basis}</li>
              <li>{result.method.ranking}</li>
              <li>{result.method.compensation}</li>
              <li>{result.method.not_a_prediction}</li>
            </ul>
            <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              {result.candidates_assessed} postings assessed across {result.distinct_concepts} distinct concepts ·{' '}
              {result.metrics.cache_hit ? 'served from cache' : `computed in ${result.metrics.elapsed_ms}ms`}
            </p>
          </details>
        </div>
      )}
    </div>
  )
}
