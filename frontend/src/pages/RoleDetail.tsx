import { useEffect, useState } from 'react'
import { Link, useNavigate, useLocation, useParams } from 'react-router-dom'
import { api, type RoleContextBasis, type RoleContextEnrichment, type Role, type TeamSizeEstimate } from '../lib/api'
import { trackColor, trackLabel } from '../lib/trackColor'
import { roleListUrl } from '../lib/roleNavigation'

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
    <div className="secondary" style={{ fontSize: 13, marginTop: 4, display: 'flex', gap: 6, alignItems: 'flex-start' }}>
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
          <p className="muted" style={{ fontSize: 12, margin: 0, maxWidth: 520 }}>
            An occupational sketch of this role, generated on demand — never based on your own profile. "From advert"
            claims are directly supported by the source posting; "Inferred" claims are reasonable occupational
            judgement, not a guarantee.
          </p>
        </div>
        {enrichment && (
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div className="muted" style={{ fontSize: 11 }}>
              Generated {new Date(enrichment.generated_at).toLocaleDateString()} · v{enrichment.generator_version}
            </div>
            <button onClick={() => run('regenerate')} disabled={busy} style={{ marginTop: 4, fontSize: 12, padding: '3px 10px' }}>
              {busy ? 'Regenerating…' : 'Regenerate'}
            </button>
          </div>
        )}
      </div>

      {error && (
        <p style={{ color: 'var(--critical)', fontSize: 13, marginTop: 10 }}>{error}</p>
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
              <strong style={{ fontSize: 13 }}>Typical day</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                {enrichment.day_in_life.map((item, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10 }}>
                    <span className="muted" style={{ fontSize: 12, width: 64, flexShrink: 0 }}>
                      {item.time_or_phase}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                        {item.activity}
                        <BasisBadge basis={item.basis} />
                      </div>
                      {item.detail && (
                        <div className="secondary" style={{ fontSize: 12, marginTop: 2 }}>
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
              <strong style={{ fontSize: 13 }}>Typical week</strong>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 6 }}>
                {enrichment.typical_week.map((item, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10 }}>
                    <span className="muted" style={{ fontSize: 12, width: 90, flexShrink: 0 }}>
                      {item.day_or_theme}
                    </span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
                        {item.activity}
                        <BasisBadge basis={item.basis} />
                      </div>
                      {item.detail && (
                        <div className="secondary" style={{ fontSize: 12, marginTop: 2 }}>
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
              <strong style={{ fontSize: 13 }}>Team</strong>
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
              <strong style={{ fontSize: 13 }}>Reports to / management dynamic</strong>
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
              <strong style={{ fontSize: 13 }}>Key stakeholders</strong>
              {enrichment.stakeholder_context.stakeholders.map((s, i) => (
                <RoleContextRow key={i} basis={s.basis}>
                  {s.text}
                </RoleContextRow>
              ))}
            </div>
          )}

          {enrichment.career_progression.length > 0 && (
            <div>
              <strong style={{ fontSize: 13 }}>Career progression</strong>
              {enrichment.career_progression.map((c, i) => (
                <RoleContextRow key={i} basis={c.basis}>
                  {c.step}
                </RoleContextRow>
              ))}
            </div>
          )}

          {enrichment.caveats && (
            <p className="muted" style={{ fontSize: 12, margin: 0, fontStyle: 'italic' }}>
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
          <p className="secondary" style={{ margin: '4px 0 0', fontSize: 13 }}>
            The source for this role may be incomplete or uncertain — this is a usable extraction, not a failure, and
            it's eligible for later review.
          </p>
          {notes && (
            <p className="secondary" style={{ margin: '6px 0 0', fontSize: 13 }}>
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
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [role, setRole] = useState<Role | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api
      .getRole(id)
      .then(setRole)
      .catch((e) => setError(String(e)))
  }, [id])

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

  return (
    <div>
      {deleteError && <p role="alert">{deleteError} Your role is still open; retry Delete below.</p>}
      <Link to={isTarget ? '/targets' : location.state?.returnTo ?? roleListUrl()} className="muted" style={{ fontSize: 13 }}>
        ← Back to {isTarget ? 'targets' : 'roles'}
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
            <span className="secondary" style={{ fontSize: 13 }}>
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
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 28, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
            {role.similarity !== null ? `${Math.round(role.similarity! * 100)}%` : '—'}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {isTarget ? 'narrative similarity' : 'similarity to profile'}
          </div>
        </div>
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

      {!isTarget && (
        <div className="form-grid" style={{ marginTop: 16 }}>
          <div className="card">
            <h3 style={{ marginTop: 0, fontSize: 14 }}>Salary</h3>
            {role.salary_min || role.salary_max ? (
              <p>
                {role.salary_min ?? '?'} – {role.salary_max ?? '?'} {role.currency ?? ''}
              </p>
            ) : (
              <p className="muted">Not stated</p>
            )}
          </div>
          <div className="card">
            <h3 style={{ marginTop: 0, fontSize: 14 }}>Dates</h3>
            <p className="secondary">
              Posted: {role.posting_date ?? '—'}
              <br />
              Captured: {role.captured_at?.slice(0, 10) ?? '—'}
            </p>
          </div>
        </div>
      )}

      {isTarget && role.path && (
        <section className="card" style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 18 }}>Potential steps toward this target</h2>
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
                <strong style={{ fontSize: 13 }}>{s.skill}</strong>
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
                <strong style={{ fontSize: 13 }}>{s.subject}</strong>
                {s.why && <p className="secondary" style={{ margin: '2px 0' }}>{s.why}</p>}
                {s.resources.length > 0 && (
                  <p className="muted" style={{ margin: 0, fontSize: 12 }}>
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
          <h3 style={{ marginTop: 0, fontSize: 14 }}>Skills</h3>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {role.skills.map((s, i) => (
              <span
                key={i}
                className="secondary"
                title={s.resolved_concept_id ? 'Linked to the vocabulary' : 'Not yet resolved — see Vocabulary'}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  border: '1px solid var(--border)',
                  borderRadius: 999,
                  padding: '4px 10px',
                  fontSize: 12,
                  opacity: s.requirement_type === 'preferred' ? 0.7 : 1,
                }}
              >
                {s.resolved_concept_id && (
                  <span
                    aria-hidden
                    style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--series-1)', flexShrink: 0 }}
                  />
                )}
                {s.name}
                {s.requirement_type ? ` · ${s.requirement_type}` : ''}
              </span>
            ))}
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
              <strong style={{ fontSize: 13 }}>Requirements</strong>
              <p className="secondary">{role.requirements}</p>
            </>
          )}
          {role.responsibilities && (
            <>
              <strong style={{ fontSize: 13 }}>Responsibilities</strong>
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
          <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
            This role was captured as raw source text rather than a structured extraction — shown verbatim below.
          </p>
          <p className="secondary" style={{ whiteSpace: 'pre-wrap' }}>
            {role.source_document_text}
          </p>
        </div>
      )}

      <RoleContextSection roleId={role.id} />

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 16 }}>
        {role.url ? (
          <a href={role.url} target="_blank" rel="noreferrer" className="muted" style={{ fontSize: 13 }}>
            View original posting ↗
          </a>
        ) : (
          <span />
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <Link to={`/role-instances/${role.id}/requirements`}>
            <button>Requirements</button>
          </Link>
          <Link to={`/comparison/${role.id}`}>
            <button>Compare</button>
          </Link>
          <Link to={`/roles/${role.id}/edit`}>
            <button>Edit</button>
          </Link>
          <button disabled={deleting} onClick={handleDelete} style={{ color: 'var(--critical)' }}>
            {deleting ? 'Deleting…' : `Delete ${isTarget ? 'target' : 'role'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
