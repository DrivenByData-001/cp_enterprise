import { CONCEPT_COLOR, GROUP_COLOR } from './vocabConstants'

// Static legend (brief §11/§24): node shapes and edge line patterns are
// explained in prose too, not colour alone — matches VocabularyGraph.tsx's
// actual rendering exactly (same shapes/dash patterns), so this can never
// drift out of sync with what the canvas draws.

function EdgeSwatch({ style }: { style: React.CSSProperties }) {
  return (
    <svg width="28" height="10" style={{ flexShrink: 0 }}>
      <line x1="0" y1="5" x2="28" y2="5" style={style} />
    </svg>
  )
}

export default function VocabularyMapLegend() {
  const row: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
      <div>
        <div className="secondary" style={{ fontWeight: 600, marginBottom: 6 }}>
          Node types
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={row}>
            <span style={{ width: 12, height: 12, borderRadius: '50%', border: '1.5px solid var(--baseline)', background: 'var(--surface-1)' }} />
            Raw word / surface form
          </div>
          <div style={row}>
            <span style={{ width: 16, height: 16, borderRadius: '50%', border: '2.5px solid var(--warning)', background: 'var(--surface-1)' }} />
            Pending cluster
          </div>
          <div style={row}>
            <span
              style={{
                width: 14, height: 14, border: `2.5px solid ${CONCEPT_COLOR}`, background: 'var(--surface-1)', transform: 'rotate(45deg)',
              }}
            />
            Accepted concept
          </div>
          <div style={row}>
            <span style={{ padding: '1px 8px', borderRadius: 999, border: `1px solid ${GROUP_COLOR}`, fontSize: 10, color: 'var(--text-muted)' }}>
              group
            </span>
            Priority / type group (not a vocabulary item)
          </div>
        </div>
      </div>
      <div>
        <div className="secondary" style={{ fontWeight: 600, marginBottom: 6 }}>
          Edge types
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={row}>
            <EdgeSwatch style={{ stroke: 'var(--text-secondary)', strokeWidth: 1.5 }} />
            Cluster membership
          </div>
          <div style={row}>
            <EdgeSwatch style={{ stroke: CONCEPT_COLOR, strokeWidth: 3 }} />
            Accepted mapping (from a resolved job-posting term)
          </div>
          <div style={row}>
            <EdgeSwatch style={{ stroke: CONCEPT_COLOR, strokeWidth: 2, strokeDasharray: '6,3' }} />
            Curator-added alias
          </div>
          <div style={row}>
            <EdgeSwatch style={{ stroke: 'var(--text-muted)', strokeWidth: 1, strokeDasharray: '1,4' }} />
            Semantic similarity (advisory only)
          </div>
          <div style={row}>
            <EdgeSwatch style={{ stroke: 'var(--series-2)', strokeWidth: 1.5, strokeDasharray: '8,3,2,3' }} />
            Formal ontology relation
          </div>
        </div>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: 11, lineHeight: 1.5 }}>
        Similarity links show semantically nearby terms; they are not automatic merge recommendations. Viewing the map
        never changes curation state.
      </p>
    </div>
  )
}
