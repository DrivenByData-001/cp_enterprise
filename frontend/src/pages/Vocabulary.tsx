import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import VocabularyReviewView from '../components/vocabulary/VocabularyReviewView'
import VocabularyMapView from '../components/vocabulary/VocabularyMapView'
import { api, type ConceptType } from '../lib/api'

// Top-level Review/Map switch (docs/25-vocabulary-map.md). Review remains the
// default, primary curation view; Map is a secondary, read-only visual
// exploration/navigation layer over the exact same vocabulary state — never a
// second source of truth. `conceptTypes` is fetched once here and handed to
// both tabs so they never duplicate that request or drift out of sync.

type Tab = 'review' | 'map'

export default function Vocabulary() {
  const [tab, setTab] = useState<Tab>('review')
  const [conceptTypes, setConceptTypes] = useState<ConceptType[]>([])
  const [reviewFocus, setReviewFocus] = useState<{ q: string; status: 'pending' | 'accepted' } | null>(null)
  const [params, setParams] = useSearchParams()

  useEffect(() => {
    api.listConceptTypes().then(setConceptTypes).catch(() => setConceptTypes([]))
  }, [])

  // Deep link from elsewhere in the app (e.g. requirement review's "concept
  // type is global — open it here" link): focus the accepted-vocabulary row
  // for a specific concept name, then clear the param so it doesn't re-fire
  // on back/forward navigation.
  useEffect(() => {
    const focusName = params.get('focusConceptName')
    if (!focusName) return
    setReviewFocus({ q: focusName, status: 'accepted' })
    setTab('review')
    setParams((p) => { const next = new URLSearchParams(p); next.delete('focusConceptName'); return next }, { replace: true })
  }, [params, setParams])

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Vocabulary</h1>
          <p className="secondary" style={{ marginTop: 4, maxWidth: 640 }}>
            {tab === 'review'
              ? 'Review the proposal queue as lexical clusters, prioritised by how much analytical value curating each one unlocks — not by raw mention count. The priority score is a curation order, not a statement that a concept is intrinsically more important.'
              : 'Explore how raw job-posting terms connect to pending clusters and canonical concepts. Click a node to inspect evidence or continue into curation.'}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button className={tab === 'review' ? 'primary' : ''} onClick={() => setTab('review')}>
            Review
          </button>
          <button className={tab === 'map' ? 'primary' : ''} onClick={() => setTab('map')}>
            Map
          </button>
        </div>
      </div>

      {/* Review keeps its state (filters, pagination, selection) while
          hidden — it is the primary view, so switching to Map and back must
          not reset it. Map is unmounted while inactive: it is graph-render-
          heavy and always wants a fresh fetch when the curator opens it. */}
      <div style={{ display: tab === 'review' ? 'block' : 'none' }}>
        <VocabularyReviewView conceptTypes={conceptTypes} focusRequest={reviewFocus} onFocusHandled={() => setReviewFocus(null)} />
      </div>
      {tab === 'map' && (
        <VocabularyMapView
          conceptTypes={conceptTypes}
          onOpenClusterReview={(searchText) => {
            setReviewFocus({ q: searchText, status: 'pending' })
            setTab('review')
          }}
        />
      )}
    </div>
  )
}
