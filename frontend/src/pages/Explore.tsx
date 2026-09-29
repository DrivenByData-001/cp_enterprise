import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type CareerDirection } from '../lib/api'

// /future — the Career Direction workspace home (docs/37 build §22). Each
// card loads independently with its own loading/error state, same failure-
// isolation discipline as Home.tsx: a Career Direction API failure must
// never blank the supporting-exploration links below it (build §28).

function CurrentDirectionCard({ direction, loading, error }: { direction: CareerDirection | null; loading: boolean; error: string | null }) {
  return (
    <section className="card" aria-labelledby="current-direction-h">
      <h2 id="current-direction-h" style={{ fontSize: 16, marginTop: 0 }}>Current direction</h2>
      {loading && <p className="muted">Loading…</p>}
      {error && <p role="alert" style={{ fontSize: 14 }}>Could not load your current direction: {error}</p>}
      {!loading && !error && direction && (
        <>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>{direction.name}</p>
          {direction.dimensions.length > 0 && (
            <p className="secondary" style={{ fontSize: 14 }}>
              {direction.dimensions.slice(0, 3).map((d) => `${d.dimension_code} (${d.desired_direction})`).join(' · ')}
            </p>
          )}
          {direction.target ? (
            <p className="secondary" style={{ fontSize: 14 }}>Target: {direction.target.title}</p>
          ) : (
            <p className="muted" style={{ fontSize: 14 }}>No concrete Target linked yet.</p>
          )}
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to={`/future/directions/${direction.id}`} className="button primary">Open direction</Link>
            {direction.target ? (
              <Link to={`/pathways/${direction.target.id}`}>Pathways</Link>
            ) : (
              <Link to={`/targets/new?direction_id=${direction.id}`}>Create a Target from it</Link>
            )}
          </div>
        </>
      )}
      {!loading && !error && !direction && (
        <>
          <p style={{ fontWeight: 600, margin: '4px 0' }}>You have not selected a Career Direction yet.</p>
          <div className="actions" style={{ marginTop: 12 }}>
            <Link to="/future/build" className="button primary">Design a direction</Link>
          </div>
        </>
      )}
    </section>
  )
}

function ExploringDirectionsCard({
  directions, selectedId, loading, error,
}: {
  directions: CareerDirection[]
  selectedId: string | null
  loading: boolean
  error: string | null
}) {
  const exploring = directions.filter((d) => d.id !== selectedId)
  return (
    <section className="card" aria-labelledby="exploring-h">
      <h2 id="exploring-h" style={{ fontSize: 16, marginTop: 0 }}>Directions I'm exploring</h2>
      {loading && <p className="muted">Loading…</p>}
      {error && <p role="alert" style={{ fontSize: 14 }}>Could not load your directions: {error}</p>}
      {!loading && !error && exploring.length === 0 && (
        <p className="muted" style={{ fontSize: 14 }}>Nothing else in progress yet.</p>
      )}
      {!loading && !error && exploring.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0' }}>
          {exploring.map((d) => (
            <Link key={d.id} to={`/future/directions/${d.id}`} style={{ textDecoration: 'none' }}>
              <strong>{d.name}</strong>
              <div className="secondary" style={{ fontSize: 14 }}>
                {d.origin === 'ai_adopted' ? 'AI-adopted' : 'User-defined'}{d.target ? ` · Target: ${d.target.title}` : ''}
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}

export default function Explore() {
  const [selected, setSelected] = useState<CareerDirection | null>(null)
  const [selectedLoading, setSelectedLoading] = useState(true)
  const [selectedError, setSelectedError] = useState<string | null>(null)

  const [directions, setDirections] = useState<CareerDirection[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    api.getSelectedCareerDirection()
      .then((res) => { if (current) setSelected(res.direction) })
      .catch((e) => { if (current) setSelectedError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (current) setSelectedLoading(false) })
    return () => { current = false }
  }, [])

  useEffect(() => {
    let current = true
    api.listCareerDirections()
      .then((res) => { if (current) setDirections(res.items) })
      .catch((e) => { if (current) setListError(e instanceof Error ? e.message : String(e)) })
      .finally(() => { if (current) setListLoading(false) })
    return () => { current = false }
  }, [])

  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Explore my future</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 680 }}>
        Start with the work you want to do and the life you want it to fit. Explore possible roles, then choose a direction to work toward.
      </p>

      <details className="workspace-guide"><summary>Direction, target, pathway — what’s the difference?</summary>
        <p><strong>Your direction</strong> describes the work and conditions you want. <strong>A target</strong> is a specific role to aim toward. <strong>A pathway</strong> explores possible steps toward it, based on the evidence available.</p>
        <p>An <strong>archetype</strong> groups similar kinds of work. A <strong>checkpoint</strong> is a saved view of your evidence, so you can see what changes over time.</p>
      </details>

      <div className="hub-grid">
        <CurrentDirectionCard direction={selected} loading={selectedLoading} error={selectedError} />
        <ExploringDirectionsCard directions={directions} selectedId={selected?.id ?? null} loading={listLoading} error={listError} />

        <section className="card">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Discover possible directions</h2>
          <p className="secondary" style={{ fontSize: 14 }}>
            Tell us what matters: the work itself, leadership, pay and location. Explore ideas to consider, without committing to one yet.
          </p>
          <Link to="/future/build" className="button primary">Design a direction →</Link>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Targets</h2>
          <p className="secondary" style={{ fontSize: 14 }}>
            Existing targets are explicit roles or imagined role descriptions you want to examine in detail.
          </p>
          <div className="actions">
            <Link to="/targets">Targets →</Link>
            <Link to="/targets/new">Add target</Link>
          </div>
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginTop: 0 }}>Supporting exploration</h2>
        <div className="hub-grid" style={{ marginTop: 8 }}>
          <div>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>Pathways</h3>
            <p className="muted" style={{ fontSize: 14 }}>
              Structural routes from your current evidence toward a target or archetype. No hiring probabilities or exact transition times
              are implied.
            </p>
            <Link to="/pathways">Pathways →</Link>
          </div>
          <div>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>Preferences</h3>
            <p className="muted" style={{ fontSize: 14 }}>What matters to you — technical depth, challenge, working style.</p>
            <Link to="/preferences">Preferences →</Link>
          </div>
          <div>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>Understand the market</h3>
            <p className="muted" style={{ fontSize: 14 }}>
              Historical roles and compensation evidence show patterns in the corpus — never a claim it is fully representative.
            </p>
            <div className="actions">
              <Link to="/trends">Trends →</Link>
              <Link to="/economics">Economics →</Link>
            </div>
          </div>
          <div>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>How much evidence do I actually have?</h3>
            <p className="muted" style={{ fontSize: 14 }}>
              What the captured corpus covers, and what it cannot establish about the wider market — the evidence
              base behind every market-derived conclusion in this app.
            </p>
            <Link to="/market/coverage">Market coverage →</Link>
          </div>
          <div>
            <h3 style={{ fontSize: 14, marginTop: 0 }}>Visual exploration</h3>
            <p className="muted" style={{ fontSize: 14 }}>
              Optional semantic visualization of role similarity — not a career recommendation.
            </p>
            <Link to="/space">Career space →</Link>
          </div>
        </div>
      </section>
    </div>
  )
}
