import type { ConceptType, VocabularyGraphNode } from '../../lib/api'
import PendingClusterMapDetails from './PendingClusterMapDetails'
import SurfaceFormDetails from './SurfaceFormDetails'

export type VocabularyMapSummaryCounts = { returnedNodes: number; pendingClusters: number; surfaceForms: number; concepts: number }

// Dispatches the right-hand details panel by selected node kind (brief §9).
// Pending-cluster and surface-form cases delegate to their own components;
// the accepted-concept case is small enough to stay inline (only §15's
// "entry point into Concept Details" — the drawer itself is the existing,
// unmodified ConceptDetailsDrawer).

export default function VocabularyMapDetails({
  node,
  conceptTypes,
  summary,
  onChanged,
  onOpenClusterReview,
  onOpenConcept,
  onFocus,
}: {
  node: VocabularyGraphNode | null
  conceptTypes: ConceptType[]
  summary: VocabularyMapSummaryCounts | null
  onChanged: () => Promise<void>
  onOpenClusterReview: (searchText: string) => void
  onOpenConcept: (conceptId: string) => void
  onFocus: (focusId: string) => void
}) {
  if (!node) {
    return (
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <h2 style={{ fontSize: 15, margin: 0 }}>Vocabulary Map</h2>
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          Select a raw term, pending cluster, or accepted concept to inspect it.
        </p>
        {summary && (
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            Showing {summary.returnedNodes} node{summary.returnedNodes === 1 ? '' : 's'}
            {summary.pendingClusters > 0 && ` · ${summary.pendingClusters} pending cluster${summary.pendingClusters === 1 ? '' : 's'}`}
            {summary.concepts > 0 && ` · ${summary.concepts} concept${summary.concepts === 1 ? '' : 's'}`}
            {summary.surfaceForms > 0 && ` · ${summary.surfaceForms} surface form${summary.surfaceForms === 1 ? '' : 's'}`}
          </p>
        )}
      </div>
    )
  }

  if (node.kind === 'surface_form') {
    return (
      <div className="card">
        <SurfaceFormDetails node={node} onOpenClusterReview={onOpenClusterReview} onOpenConcept={onOpenConcept} onFocus={onFocus} />
      </div>
    )
  }

  if (node.kind === 'pending_cluster') {
    return (
      <div className="card">
        <PendingClusterMapDetails
          node={node}
          conceptTypes={conceptTypes}
          onChanged={onChanged}
          onOpenClusterReview={onOpenClusterReview}
          onFocus={onFocus}
        />
      </div>
    )
  }

  if (node.kind === 'concept') {
    return (
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15 }}>{node.label}</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Accepted concept
          </div>
        </div>
        <div style={{ fontSize: 13, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span>Type: {node.type_code}</span>
          {node.alias_count !== undefined && <span>Aliases: {node.alias_count}</span>}
          {node.status !== 'active' && <span className="muted">Status: {node.status}</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="primary" onClick={() => onOpenConcept(node.target.id)}>
            Open Concept Details
          </button>
          <button onClick={() => onFocus(`concept:${node.target.id}`)}>View similarity neighbourhood</button>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <p className="muted" style={{ fontSize: 13, margin: 0 }}>
        This is a presentation grouping, not a vocabulary item.
      </p>
    </div>
  )
}
