import { useEffect, useState } from 'react'
import { api, type Concept, type ConceptDossier, type ConceptType } from '../lib/api'

// Concept Details drawer (cp_round_of_changes.md §B/§C/§D/§E/§F/§G) — the
// maintenance interface for one accepted canonical concept: curator editing
// of name/type/definition/aliases/status, plus the persisted, AI-assisted
// Concept Dossier (generate/regenerate/adopt/discard/manual edit) and its
// related-concept chips. Kept visually quiet per §G: no large form is shown
// until [Edit] is selected, and the distinction between the current curated
// dossier and an unadopted AI draft is always unmistakable.

function Chip({ children, onClick, title }: { children: React.ReactNode; onClick?: () => void; title?: string }) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{ fontSize: 12, padding: '3px 10px', borderRadius: 999, borderColor: 'var(--series-1)', color: 'var(--series-1)' }}
    >
      {children}
    </button>
  )
}

function TextField({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div style={{ marginTop: 10 }}>
      <div className="secondary" style={{ fontSize: 12, fontWeight: 600 }}>
        {label}
      </div>
      <p style={{ margin: '4px 0 0', fontSize: 13 }}>{value}</p>
    </div>
  )
}

function ListField({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null
  return (
    <div style={{ marginTop: 10 }}>
      <div className="secondary" style={{ fontSize: 12, fontWeight: 600 }}>
        {label}
      </div>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 13 }}>
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

function RelatedConceptChips({ dossier, onNavigate }: { dossier: ConceptDossier; onNavigate: (id: string) => void }) {
  if (dossier.related_concepts.length === 0) return null
  return (
    <div style={{ marginTop: 12 }}>
      <div className="secondary" style={{ fontSize: 12, fontWeight: 600 }}>
        Related concepts
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
        {dossier.related_concepts.map((r) => (
          <Chip key={r.concept_id} onClick={() => onNavigate(r.concept_id)} title={`${r.relationship} — ${r.explanation}`}>
            {r.canonical_name}
          </Chip>
        ))}
      </div>
    </div>
  )
}

type DossierFormState = {
  plain_definition: string
  classification_rationale: string
  practical_meaning: string
  underlying_elements: string
  stronger_expressions: string
  weaker_expressions: string
  boundaries_and_overlaps: string
  caveats: string
}

function toForm(d: ConceptDossier): DossierFormState {
  return {
    plain_definition: d.plain_definition,
    classification_rationale: d.classification_rationale,
    practical_meaning: d.practical_meaning,
    underlying_elements: d.underlying_elements.join('\n'),
    stronger_expressions: d.stronger_expressions.join('\n'),
    weaker_expressions: d.weaker_expressions.join('\n'),
    boundaries_and_overlaps: d.boundaries_and_overlaps,
    caveats: d.caveats ?? '',
  }
}

function fromForm(f: DossierFormState) {
  const toList = (s: string) =>
    s
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean)
  return {
    plain_definition: f.plain_definition,
    classification_rationale: f.classification_rationale,
    practical_meaning: f.practical_meaning,
    underlying_elements: toList(f.underlying_elements),
    stronger_expressions: toList(f.stronger_expressions),
    weaker_expressions: toList(f.weaker_expressions),
    boundaries_and_overlaps: f.boundaries_and_overlaps,
    caveats: f.caveats.trim() || null,
  }
}

export default function ConceptDetailsDrawer({
  conceptId,
  conceptTypes,
  onClose,
  onConceptChanged,
  onNavigate,
}: {
  conceptId: string
  conceptTypes: ConceptType[]
  onClose: () => void
  onConceptChanged: () => void
  onNavigate: (conceptId: string) => void
}) {
  const [concept, setConcept] = useState<Concept | null>(null)
  const [active, setActive] = useState<ConceptDossier | null>(null)
  const [draft, setDraft] = useState<ConceptDossier | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [editingMeta, setEditingMeta] = useState(false)
  const [metaForm, setMetaForm] = useState({ canonical_name: '', type_code: '', definition: '' })
  const [newAlias, setNewAlias] = useState('')

  const [editingDossier, setEditingDossier] = useState(false)
  const [dossierForm, setDossierForm] = useState<DossierFormState | null>(null)

  const [showGuidance, setShowGuidance] = useState(false)
  const [guidance, setGuidance] = useState('')

  const load = async (id: string) => {
    setLoading(true)
    setError(null)
    setEditingMeta(false)
    setEditingDossier(false)
    setShowGuidance(false)
    setGuidance('')
    try {
      const [c, d] = await Promise.all([api.getConcept(id), api.getConceptDossier(id)])
      setConcept(c)
      setActive(d.active)
      setDraft(d.draft)
      setMetaForm({ canonical_name: c.canonical_name, type_code: c.type_code, definition: c.definition ?? '' })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(conceptId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conceptId])

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await load(conceptId)
      onConceptChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const saveMeta = () =>
    run(() =>
      api.updateConcept(conceptId, {
        canonical_name: metaForm.canonical_name.trim(),
        type_code: metaForm.type_code,
        definition: metaForm.definition.trim() || null,
      }),
    )

  const toggleStatus = () => run(() => api.updateConcept(conceptId, { status: concept?.status === 'active' ? 'deprecated' : 'active' }))

  const addAlias = () => {
    if (!newAlias.trim()) return
    const alias = newAlias.trim()
    setNewAlias('')
    run(() => api.addConceptAlias(conceptId, alias))
  }

  const removeAlias = (aliasId: string) => run(() => api.removeConceptAlias(conceptId, aliasId))

  const generate = () => run(() => api.generateConceptDossier(conceptId, guidance.trim() || undefined))
  const regenerate = () => run(() => api.regenerateConceptDossier(conceptId, guidance.trim() || undefined))
  const adopt = () => run(() => api.adoptConceptDossierDraft(conceptId))
  const discard = () => run(() => api.discardConceptDossierDraft(conceptId))

  const startEditDossier = () => {
    if (!active) return
    setDossierForm(toForm(active))
    setEditingDossier(true)
  }

  const saveDossier = () => {
    if (!dossierForm) return
    run(() => api.saveConceptDossierEdit(conceptId, fromForm(dossierForm)))
  }

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div className="drawer-panel" role="dialog" aria-label="Concept details">
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <button onClick={onClose}>Close</button>
        </div>

        {loading && <p className="muted">Loading…</p>}
        {error && (
          <p className="card" style={{ color: 'var(--critical)', fontSize: 13 }}>
            {error}
          </p>
        )}

        {concept && !loading && (
          <>
            {/* --- metadata --- */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
              <h2 style={{ fontSize: 18, margin: 0 }}>{concept.canonical_name}</h2>
              {!editingMeta && (
                <button onClick={() => setEditingMeta(true)} disabled={busy}>
                  Edit
                </button>
              )}
            </div>
            {concept.status !== 'active' && (
              <span style={{ fontSize: 11, color: 'var(--warning)', border: '1px solid var(--warning)', borderRadius: 999, padding: '1px 8px' }}>
                {concept.status}
              </span>
            )}

            {!editingMeta ? (
              <div style={{ marginTop: 10 }}>
                <TextField label="Type" value={conceptTypes.find((t) => t.code === concept.type_code)?.label ?? concept.type_code} />
                <TextField label="Definition" value={concept.definition} />
                <div style={{ marginTop: 10 }}>
                  <div className="secondary" style={{ fontSize: 12, fontWeight: 600 }}>
                    Aliases
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
                    {(concept.aliases ?? []).map((a) => (
                      <span key={a.id} className="card" style={{ padding: '2px 6px 2px 10px', fontSize: 12, display: 'flex', gap: 6, alignItems: 'center' }}>
                        {a.alias}
                        <button
                          onClick={() => removeAlias(a.id)}
                          disabled={busy}
                          aria-label={`Remove alias ${a.alias}`}
                          style={{ border: 'none', padding: '0 2px', background: 'none', fontSize: 13, color: 'var(--text-muted)' }}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    {(concept.aliases ?? []).length === 0 && (
                      <span className="muted" style={{ fontSize: 12 }}>
                        none
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <input
                      placeholder="Add alias…"
                      value={newAlias}
                      onChange={(e) => setNewAlias(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && addAlias()}
                      style={{ flex: 1, fontSize: 12, padding: '4px 8px' }}
                    />
                    <button onClick={addAlias} disabled={busy || !newAlias.trim()} style={{ fontSize: 12, padding: '4px 10px' }}>
                      Add
                    </button>
                  </div>
                </div>
                <div style={{ marginTop: 12 }}>
                  <button onClick={toggleStatus} disabled={busy} style={{ fontSize: 12 }}>
                    {concept.status === 'active' ? 'Deprecate' : 'Reactivate'}
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Canonical name</span>
                  <input value={metaForm.canonical_name} onChange={(e) => setMetaForm({ ...metaForm, canonical_name: e.target.value })} />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Type</span>
                  <select value={metaForm.type_code} onChange={(e) => setMetaForm({ ...metaForm, type_code: e.target.value })}>
                    {conceptTypes.map((t) => (
                      <option key={t.code} value={t.code}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Definition</span>
                  <textarea rows={3} value={metaForm.definition} onChange={(e) => setMetaForm({ ...metaForm, definition: e.target.value })} />
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="primary" onClick={saveMeta} disabled={busy || !metaForm.canonical_name.trim()}>
                    {busy ? 'Saving…' : 'Save'}
                  </button>
                  <button onClick={() => setEditingMeta(false)} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <hr style={{ margin: '18px 0', border: 'none', borderTop: '1px solid var(--gridline)' }} />

            {/* --- Concept Dossier --- */}
            <h3 style={{ fontSize: 15, margin: '0 0 8px' }}>Concept Dossier</h3>

            {!active && !editingDossier && (
              <div>
                <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
                  No dossier yet for this concept.
                </p>
                <button className="primary" onClick={generate} disabled={busy}>
                  {busy ? 'Generating…' : 'Generate'}
                </button>
              </div>
            )}

            {active && !editingDossier && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span className="secondary" style={{ fontSize: 12 }}>
                    Current curated version{active.origin === 'ai' ? ' (AI-generated)' : ' (curator-edited)'} ·{' '}
                    {new Date(active.generated_at).toLocaleDateString()}
                  </span>
                  <button onClick={startEditDossier} disabled={busy} style={{ fontSize: 12 }}>
                    Edit
                  </button>
                </div>
                <TextField label="Plain-English meaning" value={active.plain_definition} />
                <TextField label="What this means in practice" value={active.practical_meaning} />
                <ListField label="What sits beneath / around it" items={active.underlying_elements} />
                <ListField label="More specific expressions" items={active.stronger_expressions} />
                <ListField label="Weaker / more ambiguous wording" items={active.weaker_expressions} />
                <TextField label="Boundaries & overlaps" value={active.boundaries_and_overlaps} />
                <TextField label="Classification rationale" value={active.classification_rationale} />
                <TextField label="Caveats" value={active.caveats} />
                <RelatedConceptChips dossier={active} onNavigate={onNavigate} />

                <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {showGuidance && (
                    <textarea
                      rows={2}
                      placeholder='Optional — e.g. "Focus on actuarial use." or "Explain how this differs from Capital Management."'
                      value={guidance}
                      onChange={(e) => setGuidance(e.target.value)}
                      style={{ fontSize: 12 }}
                    />
                  )}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button onClick={() => setShowGuidance((s) => !s)} disabled={busy} style={{ fontSize: 12 }}>
                      {showGuidance ? 'Hide guidance' : 'Guide AI'}
                    </button>
                    <button onClick={regenerate} disabled={busy} style={{ fontSize: 12 }}>
                      {busy ? 'Regenerating…' : 'Regenerate'}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {editingDossier && dossierForm && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Plain-English meaning</span>
                  <textarea
                    rows={2}
                    value={dossierForm.plain_definition}
                    onChange={(e) => setDossierForm({ ...dossierForm, plain_definition: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">What this means in practice</span>
                  <textarea
                    rows={2}
                    value={dossierForm.practical_meaning}
                    onChange={(e) => setDossierForm({ ...dossierForm, practical_meaning: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">What sits beneath / around it (one per line)</span>
                  <textarea
                    rows={3}
                    value={dossierForm.underlying_elements}
                    onChange={(e) => setDossierForm({ ...dossierForm, underlying_elements: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">More specific expressions (one per line)</span>
                  <textarea
                    rows={2}
                    value={dossierForm.stronger_expressions}
                    onChange={(e) => setDossierForm({ ...dossierForm, stronger_expressions: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Weaker / ambiguous wording (one per line)</span>
                  <textarea
                    rows={2}
                    value={dossierForm.weaker_expressions}
                    onChange={(e) => setDossierForm({ ...dossierForm, weaker_expressions: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Boundaries & overlaps</span>
                  <textarea
                    rows={2}
                    value={dossierForm.boundaries_and_overlaps}
                    onChange={(e) => setDossierForm({ ...dossierForm, boundaries_and_overlaps: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Classification rationale</span>
                  <textarea
                    rows={2}
                    value={dossierForm.classification_rationale}
                    onChange={(e) => setDossierForm({ ...dossierForm, classification_rationale: e.target.value })}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
                  <span className="secondary">Caveats</span>
                  <textarea rows={2} value={dossierForm.caveats} onChange={(e) => setDossierForm({ ...dossierForm, caveats: e.target.value })} />
                </label>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="primary" onClick={saveDossier} disabled={busy}>
                    {busy ? 'Saving…' : 'Save'}
                  </button>
                  <button onClick={() => setEditingDossier(false)} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {draft && (
              <div className="card" style={{ marginTop: 16, borderColor: 'var(--series-1)', borderWidth: 2, display: 'flex', flexDirection: 'column', gap: 4 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontSize: 13, color: 'var(--series-1)' }}>AI draft — not yet adopted</strong>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {new Date(draft.generated_at).toLocaleDateString()}
                  </span>
                </div>
                {draft.guidance && (
                  <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                    Guidance used: “{draft.guidance}”
                  </p>
                )}
                <TextField label="Plain-English meaning" value={draft.plain_definition} />
                <TextField label="What this means in practice" value={draft.practical_meaning} />
                <ListField label="What sits beneath / around it" items={draft.underlying_elements} />
                <ListField label="More specific expressions" items={draft.stronger_expressions} />
                <TextField label="Boundaries & overlaps" value={draft.boundaries_and_overlaps} />
                <RelatedConceptChips dossier={draft} onNavigate={onNavigate} />
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <button className="primary" onClick={adopt} disabled={busy}>
                    {busy ? 'Adopting…' : 'Adopt AI draft'}
                  </button>
                  <button onClick={discard} disabled={busy}>
                    {busy ? 'Discarding…' : 'Discard'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
