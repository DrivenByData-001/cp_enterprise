import { Component, type ReactNode } from 'react'

export default class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() {
    if (this.state.failed) return <section className="card error-notice" role="alert">
      <h1>This page couldn’t open</h1><p>A connection issue or an application update may have interrupted loading. Reload to try again.</p>
      <button onClick={() => window.location.reload()}>Reload page</button>
    </section>
    return this.props.children
  }
}
