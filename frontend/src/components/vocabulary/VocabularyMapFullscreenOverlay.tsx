import { useEffect, useRef } from 'react'

// In-app fullscreen overlay (vocab-graph-II brief §4): a fixed, full-viewport
// CSS layer rather than the browser Fullscreen API — this app never needs to
// escape the browser chrome itself, so a simple overlay avoids the browser
// API's permission prompts/vendor-prefix quirks for no loss of capability,
// and keeps fullscreen entirely inside React's normal render tree (no
// `document.fullscreenElement` bookkeeping to reconcile with app state).
// Rendered inline in the component tree (no portal) — same convention as
// this app's existing `ConceptDetailsDrawer` fixed-position panel — so it
// sits a z-index below that drawer (`--z-vocab-fullscreen` < `--z-drawer` in
// index.css) and Concept Details still opens on top of it correctly.

export default function VocabularyMapFullscreenOverlay({ onExit, children }: { onExit: () => void; children: React.ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Escape always exits, regardless of which element inside currently has
  // focus (brief §19) — listening at the document level is simpler and more
  // reliable than trying to scope the listener to the overlay subtree.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onExit()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onExit])

  // Body scroll lock while open (brief §4), restored on exit/unmount.
  useEffect(() => {
    document.body.classList.add('scroll-locked')
    return () => document.body.classList.remove('scroll-locked')
  }, [])

  // Move keyboard focus inside the workspace on entry (brief §4/§20's
  // "focus remains inside... as reasonably as practical") without a hard
  // Tab-cycle trap — Escape above always works no matter where focus ends
  // up, so this never risks actually trapping the user (§20).
  useEffect(() => {
    containerRef.current?.focus()
  }, [])

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label="Vocabulary Map, fullscreen"
      tabIndex={-1}
      className="vocab-map-fullscreen-overlay"
    >
      {children}
    </div>
  )
}
