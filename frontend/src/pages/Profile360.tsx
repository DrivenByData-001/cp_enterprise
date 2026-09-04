import { useEffect, useState } from 'react'
import { api, type Profile360Mapping, type Profile360Row } from '../lib/api'

type Kind = 'claim' | 'capability'

function RowCard({ row, kind, onMapped }: { row: Profile360Row; kind: Kind; onMapped: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const rowId = String(row.id)

  const map = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const result = kind === 'claim' ? await api.mapProfile360Claim(rowId) : await api.mapProfile360Capability(rowId)
      if (result.status === 'failed') {
        setMessage(`AI mapping failed: ${result.error}`)
      } else if (result.mapped) {
        setMessage('Mapped — see the review queue below.')
        await onMapped()
      } else if (result.reason === 'no_candidates_available') {
        setMessage(
          'No canonical vocabulary candidates exist yet to map this against — the catalogue isn’t populated enough ' +
            'for capability mapping yet. This says nothing about the strength of this evidence; map manually if appropriate.',
        )
      } else {
        const n = result.candidates_considered
        setMessage(
          `No confident match among ${n ?? 'the'} existing candidate${n === 1 ? '' : 's'} — map manually if appropriate.`,
        )
      }
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
      <div>
        <div style={{ fontSize: 13 }}>{row._display}</div>
        <div className="muted" style={{ fontSize: 11 }}>
          id: {rowId}
        </div>
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <button onClick={map} disabled={busy}>
          {busy ? 'Mapping…' : 'Suggest mapping (AI)'}
        </button>
        {message && (
          <div className="muted" style={{ fontSize: 11, marginTop: 4, maxWidth: 220 }}>
            {message}
          </div>
        )}
      </div>
    </div>
  )
}

function MappingQueue({ kind }: { kind: Kind }) {
  const [mappings, setMappings] = useState<Profile360Mapping[]>([])
  const [error, setError] = useState<string | null>(null)

  const reload = () => api.listProfile360Mappings(kind, 'unreviewed').then(setMappings)

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind])

  const review = async (id: string, action: 'accept' | 'reject') => {
    await api.reviewProfile360Mapping(id, kind, action)
    await reload()
  }

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (mappings.length === 0) return <p className="muted">Nothing pending review.</p>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {mappings.map((m) => (
        <div key={m.id} className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ fontSize: 13 }}>
            {m._display ?? m.profile360_id} → <strong>{m.canonical_name}</strong>{' '}
            <span className="muted">({m.type_code}, {m.mapping_basis})</span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={() => review(m.id, 'accept')}>Accept</button>
            <button onClick={() => review(m.id, 'reject')}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function Profile360() {
  const [tab, setTab] = useState<Kind>('claim')
  const [rows, setRows] = useState<Profile360Row[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [queueVersion, setQueueVersion] = useState(0)

  const load = () => (tab === 'claim' ? api.listProfile360Claims() : api.listProfile360Capabilities())

  useEffect(() => {
    setLoading(true)
    setError(null)
    load()
      .then(setRows)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>profile360 mapping</h1>
      <p className="secondary">
        Maps person-side evidence — read-only from the authoritative profile360 store, never copied here — onto the
        canonical jobber vocabulary. AI suggestions land unreviewed; nothing is accepted automatically.
      </p>

      <div style={{ display: 'flex', gap: 8, margin: '16px 0' }}>
        <button className={tab === 'claim' ? 'primary' : ''} onClick={() => setTab('claim')}>
          Claims
        </button>
        <button className={tab === 'capability' ? 'primary' : ''} onClick={() => setTab('capability')}>
          Capabilities
        </button>
      </div>

      {error && (
        <p style={{ color: 'var(--critical)' }}>
          {error}
          {error.includes('503') && ' — profile360 is not reachable from this environment (see docs/14 §2/§5).'}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}

      {!loading && !error && (
        <>
          <section style={{ marginBottom: 24 }}>
            <h2 style={{ fontSize: 16 }}>Unmapped {tab === 'claim' ? 'claims' : 'capabilities'}</h2>
            {rows.length === 0 && <p className="muted">Nothing here.</p>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {rows.map((r) => (
                <RowCard key={String(r.id)} row={r} kind={tab} onMapped={async () => setQueueVersion((v) => v + 1)} />
              ))}
            </div>
          </section>

          <section>
            <h2 style={{ fontSize: 16 }}>Review queue</h2>
            <MappingQueue key={`${tab}-${queueVersion}`} kind={tab} />
          </section>
        </>
      )}
    </div>
  )
}
