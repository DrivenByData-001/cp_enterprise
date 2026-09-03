import { Fragment, useEffect, useState } from 'react'
import { api, type Profile360Row } from '../lib/api'

function RawFields({ row }: { row: Profile360Row }) {
  const entries = Object.entries(row).filter(([k]) => k !== 'id' && k !== '_display')
  if (entries.length === 0) return null
  return (
    <dl
      className="muted"
      style={{ fontSize: 12, margin: '10px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px' }}
    >
      {entries.map(([k, v]) => (
        <Fragment key={k}>
          <dt style={{ fontWeight: 600 }}>{k}</dt>
          <dd style={{ margin: 0, wordBreak: 'break-word' }}>
            {v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)}
          </dd>
        </Fragment>
      ))}
    </dl>
  )
}

export default function Profile() {
  const [snapshot, setSnapshot] = useState<Profile360Row | null>(null)
  const [history, setHistory] = useState<Profile360Row[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getProfile()
      .then(setSnapshot)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const loadHistory = () => {
    setShowHistory(true)
    if (history.length === 0) {
      api.getProfileHistory().then(setHistory).catch((e) => setError(String(e)))
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Your profile</h1>
      <p className="secondary">
        Read-only: the current snapshot from profile360, the authoritative person-side evidence store. Authoring the
        narrative happens in profile360's own tool, not here — this app only displays it and compares roles against
        it.
      </p>

      {error && (
        <p style={{ color: 'var(--critical)' }}>
          {error}
          {error.includes('503') && ' — profile360 is not reachable from this environment (see docs/14 §2/§5).'}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}

      {!loading &&
        !error &&
        (snapshot ? (
          <div className="card">
            <p style={{ marginTop: 0, whiteSpace: 'pre-wrap' }}>{snapshot._display}</p>
            <RawFields row={snapshot} />
          </div>
        ) : (
          <p className="muted">No profile360 snapshot found yet.</p>
        ))}

      {!loading && !error && (
        <div style={{ marginTop: 16 }}>
          {!showHistory ? (
            <button onClick={loadHistory}>Show history</button>
          ) : (
            <>
              <h2 style={{ fontSize: 16 }}>Snapshot history</h2>
              {history.length === 0 && <p className="muted">No earlier snapshots.</p>}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {history.map((s) => (
                  <div key={s.id} className="card">
                    <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{s._display}</p>
                    <RawFields row={s} />
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
