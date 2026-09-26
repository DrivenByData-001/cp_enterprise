import { Link } from 'react-router-dom'

// Phase 1 placeholder: no application-workspace data model exists yet.
// Phase 3 replaces this empty state with persistent application data — see
// docs/32-phase1-product-shell.md. Deliberately no browser-local state here.
export default function Applications() {
  return (
    <div>
      <h1 style={{ fontSize: 22, margin: 0 }}>Applications</h1>
      <div className="card" style={{ marginTop: 12 }}>
        <p style={{ marginTop: 0, fontWeight: 600 }}>No application workspaces yet</p>
        <p className="secondary">
          Application workspaces are the next stage of the product: they will turn a chosen opportunity into an
          evidence-backed positioning brief, tailored CV and interview preparation.
        </p>
        <p className="secondary">For now, choose a role from Opportunities and use the existing comparison tools.</p>
        <div className="actions">
          <Link to="/opportunities" className="button primary">Browse opportunities</Link>
          <Link to="/profile360">Profile &amp; evidence</Link>
        </div>
      </div>
    </div>
  )
}
