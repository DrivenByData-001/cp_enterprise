import { useEffect, useState } from 'react'
import {
  api,
  type Concept,
  type ConceptInput,
  type ConceptType,
  type ProposalGroup,
  type ProposalResolveInput,
  type ProposalStats,
} from '../lib/api'

function ProposalCard({
  group,
  conceptTypes,
  concepts,
  onResolved,
}: {
  group: ProposalGroup
  conceptTypes: ConceptType[]
  concepts: Concept[]
  onResolved: () => Promise<void>
}) {
  const [mode, setMode] = useState<'idle' | 'new' | 'alias'>('idle')
  const [typeCode, setTypeCode] = useState(group.suggested_type ?? conceptTypes[0]?.code ?? '')
  const [name, setName] = useState(group.surface_form)
  const [definition, setDefinition] = useState('')
  const [aliasConceptId, setAliasConceptId] = useState<number | ''>(group.nearest_concept_id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (payload: ProposalResolveInput) => {
    setBusy(true)
    setError(null)
    try {
      await api.resolveProposal(payload)
      await onResolved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }

  const nearest = group.nearest_concept_id !== null ? concepts.find((c) => c.id === group.nearest_concept_id) : null

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <div>
          <div style={{ fontWeight: 600 }}>{group.surface_form}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            seen in {group.occurrence_count} skill{group.occurrence_count === 1 ? '' : 's'}
            {nearest && group.nearest_similarity !== null
              ? ` · nearest match: ${nearest.canonical_name} (${Math.round(group.nearest_similarity * 100)}%)`
              : ''}
          </div>
        </div>
        {mode === 'idle' && (
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            <button className="primary" onClick={() => setMode('new')} disabled={busy}>
              New concept
            </button>
            <button onClick={() => setMode('alias')} disabled={busy}>
              Alias of…
            </button>
            <button onClick={() => run({ surface_form: group.surface_form, action: 'reject' })} disabled={busy}>
              Reject
            </button>
            <button onClick={() => run({ surface_form: group.surface_form, action: 'defer' })} disabled={busy}>
              Defer
            </button>
          </div>
        )}
      </div>

      {mode === 'new' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span className="secondary">Type</span>
              <select value={typeCode} onChange={(e) => setTypeCode(e.target.value)}>
                {conceptTypes.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span className="secondary">Canonical name</span>
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          </div>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
            <span className="secondary">Definition (optional)</span>
            <textarea rows={2} value={definition} onChange={(e) => setDefinition(e.target.value)} />
          </label>
          {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="primary"
              disabled={busy || !name.trim() || !typeCode}
              onClick={() =>
                run({
                  surface_form: group.surface_form,
                  action: 'accept_new',
                  type_code: typeCode,
                  canonical_name: name.trim(),
                  definition: definition.trim() || undefined,
                })
              }
            >
              {busy ? 'Saving…' : 'Create & resolve'}
            </button>
            <button onClick={() => setMode('idle')} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {mode === 'alias' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
            <span className="secondary">Alias of</span>
            <select
              value={aliasConceptId}
              onChange={(e) => setAliasConceptId(e.target.value ? Number(e.target.value) : '')}
            >
              <option value="">— choose a concept —</option>
              {concepts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.canonical_name} ({c.type_code})
                </option>
              ))}
            </select>
          </label>
          {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="primary"
              disabled={busy || aliasConceptId === ''}
              onClick={() =>
                run({
                  surface_form: group.surface_form,
                  action: 'accept_alias',
                  concept_id: aliasConceptId as number,
                })
              }
            >
              {busy ? 'Saving…' : 'Link & resolve'}
            </button>
            <button onClick={() => setMode('idle')} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

const EMPTY_CONCEPT: ConceptInput = { type_code: '', canonical_name: '', definition: '' }

function AddConceptForm({ conceptTypes, onAdded }: { conceptTypes: ConceptType[]; onAdded: () => Promise<void> }) {
  const [form, setForm] = useState<ConceptInput>({ ...EMPTY_CONCEPT, type_code: conceptTypes[0]?.code ?? '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!form.canonical_name.trim() || !form.type_code) return
    setBusy(true)
    setError(null)
    try {
      await api.createConcept({ ...form, canonical_name: form.canonical_name.trim() })
      setForm({ ...EMPTY_CONCEPT, type_code: form.type_code })
      await onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Type</span>
          <select value={form.type_code} onChange={(e) => setForm({ ...form, type_code: e.target.value })}>
            {conceptTypes.map((t) => (
              <option key={t.code} value={t.code}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          <span className="secondary">Canonical name</span>
          <input
            value={form.canonical_name}
            onChange={(e) => setForm({ ...form, canonical_name: e.target.value })}
            placeholder="e.g. Chain ladder"
          />
        </label>
      </div>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
        <span className="secondary">Definition (optional)</span>
        <input
          value={form.definition ?? ''}
          onChange={(e) => setForm({ ...form, definition: e.target.value })}
        />
      </label>
      {error && <p style={{ color: 'var(--critical)', fontSize: 13, margin: 0 }}>{error}</p>}
      <div>
        <button className="primary" onClick={submit} disabled={busy || !form.canonical_name.trim()}>
          {busy ? 'Adding…' : 'Add concept'}
        </button>
      </div>
    </div>
  )
}

export default function Vocabulary() {
  const [conceptTypes, setConceptTypes] = useState<ConceptType[]>([])
  const [proposals, setProposals] = useState<ProposalGroup[]>([])
  const [stats, setStats] = useState<ProposalStats | null>(null)
  const [concepts, setConcepts] = useState<Concept[]>([])
  const [browseType, setBrowseType] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reloadProposals = async () => {
    const [p, s] = await Promise.all([api.listProposals('pending'), api.getProposalStats()])
    setProposals(p)
    setStats(s)
  }

  const reloadConcepts = () => api.listConcepts({ type_code: browseType || undefined }).then(setConcepts)

  useEffect(() => {
    setLoading(true)
    Promise.all([api.listConceptTypes(), reloadProposals(), reloadConcepts()])
      .then(([types]) => setConceptTypes(types))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    reloadConcepts().catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [browseType])

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Vocabulary</h1>
        <p className="secondary" style={{ marginTop: 4 }}>
          The curated concept taxonomy every posting resolves against — domain, regulation, tool, function, product,
          and the rest of docs/11 §2.3's ten types. Resolving a proposal here links every posting that used that
          surface form, immediately.
        </p>
      </div>

      {error && <p style={{ color: 'var(--critical)' }}>{error}</p>}
      {loading && <p className="muted">Loading…</p>}

      {!loading && (
        <>
          <section style={{ marginBottom: 28 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10 }}>
              <h2 style={{ fontSize: 16, margin: 0 }}>Proposal queue</h2>
              {stats && (
                <span className="muted" style={{ fontSize: 12 }}>
                  {stats.pending_groups} pending
                  {stats.proposals_per_document !== null
                    ? ` · ${stats.proposals_per_document} proposals / document (${stats.total_documents} postings)`
                    : ''}
                </span>
              )}
            </div>
            {proposals.length === 0 && <p className="muted">Nothing pending — the vocabulary is caught up.</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {proposals.map((g) => (
                <ProposalCard
                  key={g.surface_form}
                  group={g}
                  conceptTypes={conceptTypes}
                  concepts={concepts}
                  onResolved={async () => {
                    await reloadProposals()
                    await reloadConcepts()
                  }}
                />
              ))}
            </div>
          </section>

          <section>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <h2 style={{ fontSize: 16, margin: 0 }}>Concepts</h2>
              <select value={browseType} onChange={(e) => setBrowseType(e.target.value)}>
                <option value="">All types</option>
                {conceptTypes.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ marginBottom: 12 }}>
              <AddConceptForm conceptTypes={conceptTypes} onAdded={reloadConcepts} />
            </div>
            {concepts.length === 0 && <p className="muted">No concepts yet in this type.</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {concepts.map((c) => (
                <div key={c.id} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontWeight: 600 }}>{c.canonical_name}</div>
                    {c.definition && (
                      <div className="secondary" style={{ fontSize: 13 }}>
                        {c.definition}
                      </div>
                    )}
                  </div>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {c.type_code}
                  </span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
