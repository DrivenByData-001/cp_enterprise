import { Link } from 'react-router-dom'

// Explore my future hub (Phase 1 product shell). Purely explanatory —
// no data fetching here at all, so opening this page never pre-loads
// Preferences/Targets/Pathways/Trends/Economics/Space content it merely
// links to.
export default function Explore() {
  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Explore my future</h1>
      <p className="secondary" style={{ marginTop: 4, maxWidth: 680 }}>
        Understand possible directions, existing targets, paths, preferences and market evidence.
      </p>

      <div className="hub-grid">
        <section className="card">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>What do I want?</h2>
          <p className="secondary" style={{ fontSize: 13 }}>
            Record what matters to you: technical depth, challenge, working style and other preferences.
          </p>
          <Link to="/preferences">Preferences →</Link>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Targets</h2>
          <p className="secondary" style={{ fontSize: 13 }}>
            Existing targets are explicit roles or imagined role descriptions you want to examine. A fuller
            property-driven career-direction builder will come later.
          </p>
          <div className="actions">
            <Link to="/targets">Targets →</Link>
            <Link to="/targets/new">Add target</Link>
          </div>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 16, marginTop: 0 }}>Pathways</h2>
          <p className="secondary" style={{ fontSize: 13 }}>
            Explore structural routes from your current evidence toward a target or archetype, including capability
            and compensation context. No hiring probabilities or exact transition times are implied.
          </p>
          <Link to="/pathways">Pathways →</Link>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 15, marginTop: 0 }}>Understand the market</h2>
          <p className="muted" style={{ fontSize: 13 }}>
            Historical roles and compensation evidence can show patterns in the corpus. They are observations of the
            market, not a claim that the stored corpus is fully representative.
          </p>
          <div className="actions">
            <Link to="/trends">Trends →</Link>
            <Link to="/economics">Economics →</Link>
            <Link to="/opportunities?period=all">Historical opportunities →</Link>
          </div>
        </section>

        <section className="card">
          <h2 style={{ fontSize: 15, marginTop: 0 }}>Visual exploration</h2>
          <p className="muted" style={{ fontSize: 13 }}>
            Optional semantic visualization of role similarity. Position is driven by embeddings/PCA; it is not a
            career recommendation.
          </p>
          <Link to="/space">Career space →</Link>
        </section>
      </div>

      <section className="card" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 16, marginTop: 0 }}>Next</h2>
        <ul className="action-list" style={{ margin: 0 }}>
          <li><Link to="/preferences">Review Preferences</Link></li>
          <li><Link to="/targets">Inspect Targets</Link></li>
          <li><Link to="/pathways">Browse Pathways</Link></li>
          <li><Link to="/opportunities">Return to Opportunities</Link></li>
        </ul>
      </section>
    </div>
  )
}
