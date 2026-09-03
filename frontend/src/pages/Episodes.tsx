import { Fragment, useEffect, useState } from 'react'
import { api, type Profile360Row } from '../lib/api'

function RawFields({ row }: { row: Profile360Row }) {
  const entries = Object.entries(row).filter(([k]) => k !== 'id' && k !== '_display')
  if (entries.length === 0) return null
  return (
    <dl
      className="muted"
      style={{ fontSize: 12, margin: '8px 0 0', display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px' }}
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

export default function Episodes() {
  const [episodes, setEpisodes] = useState<Profile360Row[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listEpisodes()
      .then(setEpisodes)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Career history</h1>
      <p className="secondary">
        Read-only: episodes from profile360, the authoritative person-side evidence store — jobs, projects, study,
        and the rest of your career history. Authoring episodes happens in profile360's own tool, not here.
      </p>

      {error && (
        <p style={{ color: 'var(--critical)' }}>
          {error}
          {error.includes('503') && ' — profile360 is not reachable from this environment (see docs/14 §2/§5).'}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}

      {!loading && !error && episodes.length === 0 && <p className="muted">No episodes found in profile360 yet.</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {episodes.map((ep) => (
          <div key={ep.id} className="card">
            <div style={{ fontWeight: 600 }}>{ep._display}</div>
            <RawFields row={ep} />
          </div>
        ))}
      </div>
    </div>
  )
}
