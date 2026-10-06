import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useLocation, useParams, useSearchParams } from 'react-router-dom'
import {
  api,
  type ComparisonResult,
  type OpportunityAlignment,
  type RoleCompensationResponse,
  type RoleContextBasis,
  type RoleContextEnrichment,
  type Role,
  type RoleSkill,
  type TeamSizeEstimate,
} from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'
import { roleListUrl } from '../lib/roleNavigation'
import { withApplicationContext } from '../lib/applicationContext'
import ApplicationContextBanner from '../components/ApplicationContextBanner'
import { RoleEconomicsSection } from '../components/economics/RoleEconomicsSection'
import { DecisionSummary, RequirementsAskFor, RequirementReviewPendingNotice } from '../components/opportunity/DecisionSummary'
import { AlignmentFullSection } from '../components/opportunity/AlignmentSection'

// Shared chip rendering for both the reviewed-requirements and legacy-skills
// sections below — same look, so the *labelling of the section itself* is
// what tells them apart, not a different visual treatment per item.
function SkillChip({ skill }: { skill: RoleSkill }) {
  return (
    <span
      className="secondary"
      title={skill.resolved_concept_id ? 'Linked to the vocabulary' : 'Not yet resolved — see Vocabulary'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        border: '1px solid var(--border)',
        borderRadius: 999,
        padding: '4px 10px',
        fontSize: 14,
        opacity: skill.requirement_type === 'preferred' ? 0.7 : 1,
      }}
    >
      {skill.resolved_concept_id && (
        <span aria-hidden style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--series-1)', flexShrink: 0 }} />
      )}
      {skill.name}
      {skill.requirement_type ? ` · ${skill.requirement_type}` : ''}
    </span>
  )
}

// --- Day-in-the-Life / Role Context enrichment (docs/21) --------------------
//
// Compact grounded/inferred labelling (brief §6.10) so the user can tell
// evidence quality at a glance, without a methodology essay on the page.

function BasisBadge({ basis }: { basis: RoleContextBasis }) {
  const fromAdvert = basis === 'advert_grounded'
  return (
    <span
      title={fromAdvert ? 'Directly supported by the source posting' : 'Reasonable occupational inference — not stated in the posting'}
      style={{
        fontSize: 10,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.3,
        color: fromAdvert ? 'var(--good)' : 'var(--text-muted)',
        border: `1px solid ${fromAdvert ? 'var(--good)' : 'var(--border)'}`,
        borderRadius: 999,
        padding: '1px 6px',
        whiteSpace: 'nowrap',
        flexShrink: 0,
      }}
    >
      {fromAdvert ? 'From advert' : 'Inferred'}
    </span>
  )
}

function formatTeamSize(size: TeamSizeEstimate): string {
  if (size.min != null && size.max != null) return `${size.min}–${size.max} people`
  if (size.min != null) return `${size.min}+ people`
  if (size.max != null) return `up to ${size.max} people`
  return 'unclear'
}

function RoleContextRow({ children, basis }: { children: React.ReactNode; basis: RoleContextBasis }) {
  return (
    <div className="secondary" style={{ fontSize: 14, marginTop: 4, display: 'flex', gap: 6, alignItems: 'flex-start' }}>
      <span style={{ flex: 1 }}>{children}</span>
      <BasisBadge basis={basis} />
    </div>
  )
}

function RoleContextSection({ roleId }: { roleId: string }) {
  const [enrichment, setEnrichment] = useState<RoleContextEnrichment | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoaded(false)
    api
      .getRoleContext(roleId)
      .then((res) => setEnrichment(res.enrichment))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoaded(true))
  }, [roleId])

  const run = async (action: 'generate' | 'regenerate') => {
    setBusy(true)
    setError(null)
    try {
      const result = action === 'generate' ? await api.generateRoleContext(roleId) : await api.regenerateRoleContext(roleId)
      setEnrichment(result.enrichment)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!loaded) return null

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h3 style={{ marginTop: 0, marginBottom: 4, fontSize: 14 }}>Day in the Life</h3>
          <p className="muted" style={{ fontSize: 14, margin: 0, maxWidth: 520 }}>
            An occupational sketch of this role, generated on demand — never based on your own profile. "From advert"
            claims are directly supported by the source posting; "Inferred" claims are reasonable occupational
            judgement, not a guarantee.
          </p>
        </div>
        {enrichment && (
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div className="muted" style={{ fontSize: 14 }}>
              Generated {new Date(enrichment.generated_at).toLocaleDateString()} · v{enrichment.generator_version}
            </div>
            <button onClick={() => run('regenerate')} disabled={busy} style={{ marginTop: 4, fontSize: 14, padding: '3px 10px' }}>
              {busy ? 'Regenerating…' : 'Regenerate'}
            </button>
          </div>
        )}
      </div>

      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 14, marginTop: 10 }}>{error}</p>
      )}

      {!enrichment && (
        <div style={{ marginTop: 10 }}>
          <button className="primary" onClick={() => run('generate')} disabled={busy}>
            {busy ? 'Generating…' : 'Generate'}
          </button>
        </div>
      )}

      {enrichment && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 12 }}>
          {enrichment.day_in_life.length > 0 && (
            <div>
              <strong style={{ fontSize: 14 }}>Typical day</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                {enrichment.day_in_life.map((item, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10 }}>
                    <span className="muted" style={{ fontSize: 14, width: 64, flexShrink: 0 }}>
                      {item.time_or_phase}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 14, display: 'flex', gap: 6, alignItems: 'center' }}>
                        {item.activity}
                        <BasisBadge basis={item.basis} />
                      </div>
                      {item.detail && (
                        <div className="secondary" style={{ fontSize: 14, marginTop: 2 }}>
                          {item.detail}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {enrichment.typical_week.length > 0 && (
            <div>
              <strong style={{ fontSize: 14 }}>Typical week</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                {enrichment.typical_week.map((item, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10 }}>
                    <span className="muted" style={{ fontSize: 14, width: 90, flexShrink: 0 }}>
                      {item.day_or_theme}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 14, display: 'flex', gap: 6, alignItems: 'center' }}>
                        {item.activity}
                        <BasisBadge basis={item.basis} />
                      </div>
                      {item.detail && (
                        <div className="secondary" style={{ fontSize: 14, marginTop: 2 }}>
                          {item.detail}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {(enrichment.team_context.expected_team_size || enrichment.team_context.team_work.length > 0) && (
            <div>
              <strong style={{ fontSize: 14 }}>Team</strong>
              {enrichment.team_context.expected_team_size && (
                <RoleContextRow basis={enrichment.team_context.expected_team_size.basis}>
                  Expected team size: {formatTeamSize(enrichment.team_context.expected_team_size)}
                </RoleContextRow>
              )}
              {enrichment.team_context.team_work.map((t, i) => (
                <RoleContextRow key={i} basis={t.basis}>
                  {t.text}
                </RoleContextRow>
              ))}
            </div>
          )}

          {(enrichment.manager_context.likely_manager_title || enrichment.manager_context.dynamic) && (
            <div>
              <strong style={{ fontSize: 14 }}>Reports to / management dynamic</strong>
              {enrichment.manager_context.likely_manager_title && (
                <RoleContextRow basis={enrichment.manager_context.title_basis ?? 'inferred'}>
                  {enrichment.manager_context.likely_manager_title}
                </RoleContextRow>
              )}
              {enrichment.manager_context.dynamic && (
                <RoleContextRow basis={enrichment.manager_context.dynamic_basis ?? 'inferred'}>
                  {enrichment.manager_context.dynamic}
                </RoleContextRow>
              )}
            </div>
          )}

          {enrichment.stakeholder_context.stakeholders.length > 0 && (
            <div>
              <strong style={{ fontSize: 14 }}>Key stakeholders</strong>
              {enrichment.stakeholder_context.stakeholders.map((s, i) => (
                <RoleContextRow key={i} basis={s.basis}>
                  {s.text}
                </RoleContextRow>
              ))}
            </div>
          )}

          {enrichment.career_progression.length > 0 && (
            <div>
              <strong style={{ fontSize: 14 }}>Career progression</strong>
              {enrichment.career_progression.map((c, i) => (
                <RoleContextRow key={i} basis={c.basis}>
                  {c.step}
                </RoleContextRow>
              ))}
            </div>
          )}

          {enrichment.caveats && (
            <p className="muted" style={{ fontSize: 14, margin: 0, fontStyle: 'italic' }}>
              {enrichment.caveats}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// docs/18 §5: `partial` means a usable extraction with known/model-declared
// incompleteness — not failure, and never mutated/reanalysed merely by
// viewing it here. `role.extraction_quality` (the run's own verdict) is
// authoritative when present; it can diverge from the role's self-reported
// `extraction_status` column (see backend/app/document_processing.py::
// role_extraction_quality's docstring for why), so it's preferred whenever
// available. A role with no extraction_quality at all (legacy/bulk import,
// hand-entered — never went through the document-processing pipeline) falls
// back to the older self-reported field, the only signal such a role has.
function ExtractionQualityNotice({ role }: { role: Role }) {
  const quality = role.extraction_quality
  const isPartial = quality ? quality.status === 'partial' : role.extraction_status != null && role.extraction_status !== 'ok'
  if (!isPartial) return null

  const notes = quality ? quality.notes : role.extraction_notes

  return (
    <div className="card" style={{ marginTop: 16, borderColor: 'var(--warning)', background: 'rgba(250,178,25,0.08)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <strong>Partial extraction</strong>
          <p className="secondary" style={{ margin: '4px 0 0', fontSize: 14 }}>
            The source for this role may be incomplete or uncertain — this is a usable extraction, not a failure, and
            it's eligible for later review.
          </p>
          {notes && (
            <p className="secondary" style={{ margin: '6px 0 0', fontSize: 14 }}>
              {notes}
            </p>
          )}
        </div>
        <button
          disabled
          title="Controlled review/reanalysis workflow — not yet available. Viewing this page never re-analyses the source."
          style={{ flexShrink: 0 }}
        >
          Review / reanalyse extraction
        </button>
      </div>
    </div>
  )
}

export default function RoleDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const applicationId = searchParams.get('application')
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [role, setRole] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Every fetch below is guarded against resolving for a since-superseded
  // id: navigating from /roles/A to /roles/B without unmounting must never
  // let a slow A response overwrite B's state. `currentId` always holds the
  // id this component is currently showing; a `.then`/`.catch` only applies
  // if the id it was fetched for still matches it.
  const currentId = useRef(id)

  const reload = useCallback(() => {
    if (!id) return
    api
      .getRole(id)
      .then((r) => { if (currentId.current === id) setRole(r) })
      .catch((e) => { if (currentId.current === id) setError(String(e)) })
  }, [id])

  useEffect(reload, [reload])

  // Compensation and comparison are fetched once here — by RoleDetail, not by
  // the components that display them — so the Decision Summary's compact
  // tiles and the detailed sections below never issue a second request for
  // the same state (docs/33). Each fails independently: a broken comparison
  // fetch never blanks the compensation tile or the rest of the page, and
  // vice versa.
  const [compensation, setCompensation] = useState<RoleCompensationResponse | null>(null)
  const [compensationError, setCompensationError] = useState<string | null>(null)
  const loadCompensation = useCallback(() => {
    if (!id) return
    setCompensationError(null)
    api
      .getRoleCompensation(id)
      .then((r) => { if (currentId.current === id) setCompensation(r) })
      .catch((e) => { if (currentId.current === id) setCompensationError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  const [comparison, setComparison] = useState<ComparisonResult | null>(null)
  const [comparisonError, setComparisonError] = useState<string | null>(null)
  const loadComparison = useCallback(() => {
    if (!id) return
    setComparisonError(null)
    api
      .compareRole(id)
      .then((r) => { if (currentId.current === id) setComparison(r) })
      .catch((e) => { if (currentId.current === id) setComparisonError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  // Phase 7 (docs/38): `You -> Opportunity -> Target` against the selected
  // Career Direction's linked Target. Fetched once here alongside
  // compensation/comparison, posting-only (a target keeps its own framing
  // and never shows the Decision Summary at all), so it never becomes a
  // second request for the same state. Replaces Phase 6's bare "name the
  // direction" fetch — this is the richer analysis that was deferred then.
  const [alignment, setAlignment] = useState<OpportunityAlignment | null>(null)
  const [alignmentError, setAlignmentError] = useState<string | null>(null)
  const loadAlignment = useCallback(() => {
    if (!id) return
    setAlignmentError(null)
    api
      .getCareerAlignment(id)
      .then((r) => { if (currentId.current === id) setAlignment(r) })
      .catch((e) => { if (currentId.current === id) setAlignmentError(e instanceof Error ? e.message : String(e)) })
  }, [id])

  // The instant id changes, forget the previous role entirely — role,
  // compensation, comparison and alignment alike — rather than leaving any
  // of it on screen while the new id's own requests are still in flight.
  useEffect(() => {
    currentId.current = id
    setRole(null)
    setError(null)
    setCompensation(null)
    setCompensationError(null)
    setComparison(null)
    setComparisonError(null)
    setAlignment(null)
    setAlignmentError(null)
  }, [id])

  useEffect(() => {
    if (!role) return
    loadCompensation()
    // The Decision Summary is posting-only (targets keep their own
    // Explore-my-future framing) — no reason to ask the comparison engine or
    // the alignment API for a page that will never show their answer.
    if (role.node_type === 'posting') {
      loadComparison()
      loadAlignment()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role?.id, role?.node_type])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!role) return <p className="muted">Loading…</p>

  const isTarget = role.node_type !== 'posting'

  const handleDelete = async () => {
    if (!confirm(`Delete "${role.title}"? This can't be undone.`)) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await api.deleteRole(role.id)
      navigate(isTarget ? '/targets' : location.state?.returnTo ?? roleListUrl())
    } catch (e) { setDeleteError(e instanceof Error ? e.message : String(e)) }
    finally { setDeleting(false) }
  }

  // Phase 3 (docs/34 §7): a mutation button, not a navigation Link — always
  // hits the idempotent create/reopen endpoint and lets the backend decide
  // whether this opens a new application or an existing active one. Never
  // gated on requirement review, compensation, or evidence completeness:
  // those affect preparation state, not whether the user may pursue the role.
  const handleApply = async () => {
    if (applying) return
    setApplying(true)
    setApplyError(null)
    try {
      const result = await api.createOrReopenApplication(role.id)
      navigate(`/applications/${result.id}`)
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : String(e))
      setApplying(false)
    }
  }

  return (
    <div>
      <ApplicationContextBanner />
      {deleteError && <p role="alert">{deleteError} Your role is still open; retry Delete below.</p>}
      <Link to={applicationId ? `/applications/${applicationId}` : isTarget ? '/targets' : location.state?.returnTo ?? roleListUrl()} className="muted" style={{ fontSize: 14 }}>
        ← Back to {applicationId ? 'application' : isTarget ? 'targets' : 'opportunities'}
      </Link>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginTop: 12 }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              aria-hidden
              style={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: isTarget
                  ? role.node_type === 'target_imagined'
                    ? '#c792ff'
                    : '#ffffff'
                  : trackColor(role.career_track),
                border: isTarget ? '1px solid var(--border)' : undefined,
              }}
            />
            <span className="secondary" style={{ fontSize: 14 }}>
              {isTarget
                ? role.node_type === 'target_imagined'
                  ? 'Target · imagined'
                  : 'Target · real'
                : trackLabel(role.career_track)}
              {role.career_track && isTarget ? ` · ${trackLabel(role.career_track)}` : ''}
            </span>
          </div>
          <h1 style={{ fontSize: 24, margin: '4px 0' }}>{role.title}</h1>
          <div className="secondary">
            {role.organisation ?? (isTarget ? 'No organisation specified' : 'Unknown org')}
            {role.location ? ` · ${role.location}` : ''}
            {role.remote_type ? ` · ${role.remote_type}` : ''}
            {!isTarget && role.employment_type ? ` · ${role.employment_type}` : ''}
          </div>
          {/* Opportunity header (build §1): posting date is primary provenance,
              captured date is secondary — never headlined the way similarity
              used to be. Targets keep their own large similarity treatment
              on the right; it is never shown this small/secondary for them. */}
          {!isTarget && (
            <div className="muted" style={{ fontSize: 14, marginTop: 4 }}>
              {role.posting_date ? `Posted ${role.posting_date}` : 'Posting date unknown'}
              {role.captured_at ? ` · captured ${role.captured_at.slice(0, 10)}` : ''}
              {role.similarity !== null ? ` · ${Math.round(role.similarity! * 100)}% similarity to profile` : ''}
            </div>
          )}
          {!isTarget && role.url && (
            <div style={{ marginTop: 4 }}>
              <a href={role.url} target="_blank" rel="noreferrer" className="muted" style={{ fontSize: 14 }}>
                View original posting ↗
              </a>
            </div>
          )}
        </div>
        {isTarget && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 28, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
              {role.similarity !== null ? `${Math.round(role.similarity! * 100)}%` : '—'}
            </div>
            <div className="muted" style={{ fontSize: 14 }}>
              narrative similarity
            </div>
          </div>
        )}
      </div>

      {isTarget && role.is_plausible === false && (
        <div
          className="card"
          style={{ marginTop: 16, borderColor: 'var(--critical)', background: 'rgba(208,59,59,0.08)' }}
        >
          <strong style={{ color: 'var(--critical)' }}>Flagged as not realistically reachable</strong>
          {role.feasibility_note && <p style={{ margin: '4px 0 0' }}>{role.feasibility_note}</p>}
        </div>
      )}

      <ExtractionQualityNotice role={role} />

      {/* Decision-workspace summary (build §2/§3, docs/33) — posting-only.
          Targets keep their existing Explore-my-future-oriented content below
          instead; see the isTarget branches further down this file. */}
      {!isTarget && (
        <>
          <DecisionSummary
            role={role}
            comparison={comparison}
            comparisonError={comparisonError}
            onRetryComparison={loadComparison}
            compensation={compensation}
            compensationError={compensationError}
            alignment={alignment}
            alignmentError={alignmentError}
            onRetryAlignment={loadAlignment}
          />
          <RequirementsAskFor role={role} />
        </>
      )}

      {/* Phase 7 (docs/38 build §15): the fuller You -> this opportunity ->
          Target section, directly below the Decision Summary tile above.
          Renders nothing for `no_selected_direction` (the compact tile
          already says everything there is to say); every other state shows
          whatever pieces are available without blanking the rest of the
          page on a fetch failure (build §26). */}
      {!isTarget && alignment && (
        <section aria-labelledby="alignment-full-h" style={{ marginTop: 16 }}>
          <h2 id="alignment-full-h" style={{ fontSize: 18, marginBottom: 8 }}>
            You → this opportunity → Target
          </h2>
          <AlignmentFullSection alignment={alignment} roleId={role.id} />
          {alignment.target && (
            <p style={{ marginTop: 12 }}>
              <Link to={`/pathways/${alignment.target.id}?opportunity_id=${role.id}`}>View this opportunity in Pathways</Link>
            </p>
          )}
        </section>
      )}

      {/* Build §14: compensation with its basis, the personal comparison,
          the reviewed archetype and the Pathways entry point. This replaces
          the old bare "Salary" card, which showed the legacy salary_min/max
          columns with no indication of where the numbers came from — the
          resolver behind this section still reads those columns as its
          lowest-precedence tier, clearly labelled as a legacy estimate. */}
      <RoleEconomicsSection
        roleId={role.id}
        isTarget={isTarget}
        hasSourceDocument={Boolean(role.url || role.source_document_text || role.description)}
        archetype={role.archetype}
        onArchetypeChanged={reload}
        data={compensation}
        error={compensationError}
        reload={loadCompensation}
      />

      {isTarget && role.path && (
        <section className="card" style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 18 }}>Potential steps toward this target</h2>
          {role.path.target_mapping && <div>
            <p>{role.path.target_mapping.mapped} of {role.path.target_mapping.total} target requirements mapped and included.</p>
            {!role.path.target_mapping.complete && <p role="alert">Target mapping is incomplete. Readiness and intermediate-step conclusions are withheld. <Link to={`/targets/${role.id}/edit`}>Review target requirements</Link></p>}
            <ul>{role.path.target_mapping.items.map((item, index) => <li key={index}>{item.name}: {item.mapping_status === 'mapped' ? `Mapped → ${item.canonical_name}` : item.mapping_status === 'excluded' ? `Mapped → ${item.canonical_name}, but excluded by requirement review — needs review` : 'Unmapped — excluded from analysis; needs review'}</li>)}</ul>
          </div>}
          <p className="secondary">{role.path.method}</p>
          <p className="muted">Assessed {role.path.candidates_assessed} captured roles. Missing evidence does not mean missing ability.</p>
          {role.path.stepping_stones.map((step) => (
            <article key={step.id} className="card" style={{ marginTop: 8 }}>
              <Link to={`/roles/${step.id}`}><strong>{step.title}</strong></Link>
              <p>{step.organisation ?? 'Unknown employer'} · {step.posting_date ?? 'Posting date unknown'}</p>
              <strong>{{ potential_step: 'Potential development step', target_evidenced: 'Target requirements already evidenced', no_target_progress: 'No mapped progress toward target gaps', not_an_intermediate_step: 'Not a more reachable intermediate step', needs_evidence: 'Person-side evidence needed', insufficient_evidence: 'More requirement evidence needed' }[step.assessment] ?? 'Review evidence'}</strong>
              <p>{step.explanation}</p>
              <p>Evidence coverage: {step.evidenced_requirements}/{step.requirements_total}. Required evidence missing: {step.missing_required.length}; unverified: {step.unverified_required.length}.</p>
              {step.target_gaps_addressed.length > 0 && <p>Target requirements this role involves: {step.target_gaps_addressed.join(', ')}.</p>}
              {step.missing_required.length > 0 && <p>Investigate: {step.missing_required.join(', ')}.</p>}
              {step.unverified_required.length > 0 && <p>Verify: {step.unverified_required.join(', ')}.</p>}
              {step.legacy_requirements > 0 && <p className="muted">Includes {step.legacy_requirements} legacy requirements; verify them against the source.</p>}
              <Link to={`/comparison/${step.id}`}>Review evidence and plan next steps</Link>
            </article>
          ))}
          {role.path.stepping_stones.length === 0 && <Link to="/import">Add postings to explore possible steps</Link>}
        </section>
      )}

      {isTarget && role.feasibility_note && role.is_plausible !== false && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Feasibility</h3>
          <p className="secondary">{role.feasibility_note}</p>
        </div>
      )}

      {isTarget && role.grounding_note && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Grounding</h3>
          <p className="secondary">{role.grounding_note}</p>
        </div>
      )}

      {isTarget && role.typical_tasks && role.typical_tasks.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Typical tasks</h3>
          <ul className="secondary" style={{ margin: 0, paddingLeft: 20 }}>
            {role.typical_tasks.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {isTarget && role.skill_decomposition && role.skill_decomposition.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Skill decomposition</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {role.skill_decomposition.map((s, i) => (
              <div key={i}>
                <strong style={{ fontSize: 14 }}>{s.skill}</strong>
                {s.examples.length > 0 && (
                  <ul className="secondary" style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                    {s.examples.map((ex, j) => (
                      <li key={j}>{ex}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {isTarget && role.technical_subjects && role.technical_subjects.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Technical subjects to study</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {role.technical_subjects.map((s, i) => (
              <div key={i}>
                <strong style={{ fontSize: 14 }}>{s.subject}</strong>
                {s.why && <p className="secondary" style={{ margin: '2px 0' }}>{s.why}</p>}
                {s.resources.length > 0 && (
                  <p className="muted" style={{ margin: 0, fontSize: 14 }}>
                    Resources: {s.resources.join(', ')}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {role.summary && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Summary</h3>
          <p>{role.summary}</p>
        </div>
      )}

      {role.skills && role.skills.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Reviewed requirements</h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {role.skills.map((s, i) => <SkillChip key={i} skill={s} />)}
          </div>
        </div>
      )}

      {role.legacy_skills && role.legacy_skills.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Legacy skills</h3>
          <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
            Extracted, not yet reviewed as requirements — never treated as equivalent to the reviewed list above.
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {role.legacy_skills.map((s, i) => <SkillChip key={i} skill={s} />)}
          </div>
        </div>
      )}

      {role.top_adjacent_roles && role.top_adjacent_roles.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Adjacent roles</h3>
          <p className="secondary">{role.top_adjacent_roles.join(', ')}</p>
        </div>
      )}

      {(role.description || role.requirements || role.responsibilities) && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>{isTarget ? 'Role narrative' : 'Raw posting'}</h3>
          {role.description && <p className="secondary">{role.description}</p>}
          {role.requirements && (
            <>
              <strong style={{ fontSize: 14 }}>Requirements</strong>
              <p className="secondary">{role.requirements}</p>
            </>
          )}
          {role.responsibilities && (
            <>
              <strong style={{ fontSize: 14 }}>Responsibilities</strong>
              <p className="secondary">{role.responsibilities}</p>
            </>
          )}
        </div>
      )}

      {/* Fallback for roles captured via source-aware ingest + requirement
          extraction (2026 Role Detail regression, docs/21): no model-composed
          description/requirements/responsibilities exist, but the originally
          captured text does. Shown only when those flat fields are empty. */}
      {!role.description && !role.requirements && !role.responsibilities && role.source_document_text && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Captured source text</h3>
          <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
            This role was captured as raw source text rather than a structured extraction — shown verbatim below.
          </p>
          <p className="secondary" style={{ whiteSpace: 'pre-wrap' }}>
            {role.source_document_text}
          </p>
        </div>
      )}

      <RoleContextSection roleId={role.id} />

      {/* Targets keep this notice exactly where it always was. Postings show
          the same notice earlier, inside "What this role asks for". */}
      {isTarget && <RequirementReviewPendingNotice role={role} />}

      {/* Section 8: next actions. Phase 1 cleanup folded in here too — these
          were `<Link><button>…</button></Link>` (invalid nested interactive
          markup); now real `a.button` links, consistent with the rest of the
          app. Phase 3 (docs/34 §7): "I want to apply" is the prominent
          primary action for an observed opportunity — a mutation button, not
          a Link, so it can call the create/reopen endpoint before
          navigating. Never shown for targets. */}
      <section aria-labelledby="next-actions-h" style={{ marginTop: 16 }}>
        <h2 id="next-actions-h" style={{ fontSize: 18 }}>
          Next actions
        </h2>
        {applyError && (
          <p role="alert" style={{ color: 'var(--critical)', fontSize: 14 }}>
            Couldn't open the application: {applyError}
          </p>
        )}
        <div className="actions">
          {!isTarget && !applicationId && (
            <button type="button" className="button primary" disabled={applying} onClick={handleApply}>
              {applying ? 'Opening application…' : 'I want to apply'}
            </button>
          )}
          <Link to={withApplicationContext(`/role-instances/${role.id}/requirements`, applicationId)} className="button">
            {isTarget ? 'Requirements' : 'Review requirements'}
          </Link>
          <Link to={withApplicationContext(`/comparison/${role.id}`, applicationId)} className="button">
            {isTarget ? 'Compare' : 'Review evidence in detail'}
          </Link>
          <Link to={isTarget ? `/targets/${role.id}/edit` : `/roles/${role.id}/edit`} className="button">
            {isTarget ? 'Edit' : 'Correct role details'}
          </Link>
          {!isTarget && (
            <Link to="/future" className="button">
              Explore my future
            </Link>
          )}
          <button disabled={deleting} onClick={handleDelete} style={{ color: 'var(--critical)' }}>
            {deleting ? 'Deleting…' : `Delete ${isTarget ? 'target' : 'role'}`}
          </button>
        </div>
      </section>
    </div>
  )
}
