import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import { api, type NodeType, type SpaceFilter, type SpacePoint, type SpaceResponse } from '../lib/api'
import { LEGEND_TRACKS, trackLabel } from '../lib/trackColor'
import { nodeAccentColor, PROFILE_COLOR, trackStarColor } from '../lib/spaceColors'

// Scales the (small, roughly [-1, 1]) PCA coordinates out into a roomier 3D scene.
const SCALE = 4.5

// --- screen-space marker sizing (brief: markers must behave approximately
// as a SCREEN-SPACE size, not a world-space one) ----------------------------
//
// three.js Sprites are sized in world units, so a *fixed* scale (the
// pre-existing behaviour here) balloons as the camera moves closer — exactly
// the complained-about zoom behaviour, since a perspective camera's
// projected size for a fixed world-space object grows as distance shrinks.
// The fix is the standard constant-apparent-size billboard trick: scale each
// sprite proportionally to its current camera distance every frame, which
// cancels the projection's 1/distance falloff, so the marker's size *on
// screen* stays approximately constant across the whole zoom range —
// points spread apart on zoom-in, but individual markers don't grow to
// swallow them. These constants are "world units of scale per world unit of
// camera distance" — deliberately small (a marker should read as a point in
// a cloud of hundreds, not a landmark).
const SCREEN_SIZE_POSTING = 0.026
const SCREEN_SIZE_TARGET = 0.040
const SCREEN_SIZE_PROFILE = 0.058
const HOVER_SCALE_MULTIPLIER = 1.7
const DIMMED_OPACITY = 0.28
const DEFAULT_OPACITY_DENSE = 0.72 // applied to non-highlighted postings once the cloud is large enough that full opacity would smear together
const DENSE_THRESHOLD = 60

function useStarTexture(): THREE.Texture {
  return useMemo(() => {
    const size = 128
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')!
    const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    gradient.addColorStop(0, 'rgba(255,255,255,1)')
    gradient.addColorStop(0.2, 'rgba(255,255,255,0.95)')
    gradient.addColorStop(0.5, 'rgba(255,255,255,0.25)')
    gradient.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, size, size)
    const texture = new THREE.CanvasTexture(canvas)
    texture.needsUpdate = true
    return texture
  }, [])
}

type HoverInfo = { point: SpacePoint; kind: 'role' } | { point: null; kind: 'profile' }

function RoleStar({
  point,
  texture,
  hovered,
  dimmed,
  dense,
  onHover,
  onLeave,
  onClick,
}: {
  point: SpacePoint
  texture: THREE.Texture
  hovered: boolean
  dimmed: boolean
  dense: boolean
  onHover: (h: HoverInfo) => void
  onLeave: () => void
  onClick: (id: string) => void
}) {
  const ref = useRef<THREE.Sprite>(null)
  const isTarget = point.node_type !== 'posting'
  const color = isTarget ? nodeAccentColor(point.node_type, point.is_plausible) : trackStarColor(point.career_track)
  const screenSize = isTarget ? SCREEN_SIZE_TARGET : SCREEN_SIZE_POSTING
  const phaseSeed = point.x + point.y + point.z

  const material = useRef<THREE.SpriteMaterial>(null)

  useFrame(({ camera, clock }) => {
    if (!ref.current) return
    const distance = camera.position.distanceTo(ref.current.position)
    let multiplier = 1
    if (isTarget) multiplier *= 1 + Math.sin(clock.elapsedTime * 2 + phaseSeed) * 0.12 // restrained pulse — semantic emphasis, not motion noise
    if (hovered) multiplier *= HOVER_SCALE_MULTIPLIER
    ref.current.scale.setScalar(screenSize * distance * multiplier)

    if (material.current) {
      const baseOpacity = dense && !isTarget ? DEFAULT_OPACITY_DENSE : 1
      material.current.opacity = hovered ? 1 : dimmed ? DIMMED_OPACITY : baseOpacity
    }
  })

  return (
    <sprite
      ref={ref}
      position={[point.x * SCALE, point.y * SCALE, point.z * SCALE]}
      onPointerOver={(e) => {
        e.stopPropagation()
        onHover({ point, kind: 'role' })
        document.body.style.cursor = 'pointer'
      }}
      onPointerOut={() => {
        onLeave()
        document.body.style.cursor = 'auto'
      }}
      onClick={(e) => {
        e.stopPropagation()
        onClick(point.id)
      }}
    >
      <spriteMaterial ref={material} map={texture} color={color} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </sprite>
  )
}

function ProfileStar({
  x,
  y,
  z,
  texture,
  onHover,
  onLeave,
}: {
  x: number
  y: number
  z: number
  texture: THREE.Texture
  onHover: (h: HoverInfo) => void
  onLeave: () => void
}) {
  const ref = useRef<THREE.Sprite>(null)
  useFrame(({ camera, clock }) => {
    if (!ref.current) return
    const distance = camera.position.distanceTo(ref.current.position)
    const pulse = 1 + Math.sin(clock.elapsedTime * 1.6) * 0.1
    ref.current.scale.setScalar(SCREEN_SIZE_PROFILE * distance * pulse)
  })
  return (
    <sprite
      ref={ref}
      position={[x * SCALE, y * SCALE, z * SCALE]}
      onPointerOver={(e) => {
        e.stopPropagation()
        onHover({ point: null, kind: 'profile' })
        document.body.style.cursor = 'pointer'
      }}
      onPointerOut={() => {
        onLeave()
        document.body.style.cursor = 'auto'
      }}
    >
      <spriteMaterial map={texture} color={PROFILE_COLOR} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </sprite>
  )
}

// The previous decorative drei <Stars> background field (3000 small white
// glowing points) is dropped entirely, not just dimmed: at a few hundred
// *real* role markers — themselves small white/coloured glowing points on
// the same black background — a large field of look-alike fake points
// directly worked against readability (brief: "substantially more usable
// dense-cloud presentation"), effectively adding indistinguishable noise to
// the exact data density problem this pass exists to fix.
function Background() {
  const { scene } = useThree()
  useEffect(() => {
    scene.background = new THREE.Color('#04050a')
  }, [scene])
  return <ambientLight intensity={0.6} />
}

function Scene({
  data,
  autoRotate,
  hoveredId,
  highlightTrack,
  onHover,
  onLeave,
  onClick,
}: {
  data: SpaceResponse
  autoRotate: boolean
  hoveredId: string | null
  highlightTrack: string | null
  onHover: (h: HoverInfo) => void
  onLeave: () => void
  onClick: (id: string) => void
}) {
  const texture = useStarTexture()
  const dense = data.points.length > DENSE_THRESHOLD

  return (
    <>
      <Background />

      {data.points.map((p) => (
        <RoleStar
          key={p.id}
          point={p}
          texture={texture}
          hovered={p.id === hoveredId}
          dimmed={highlightTrack !== null && p.node_type === 'posting' && p.career_track !== highlightTrack}
          dense={dense}
          onHover={onHover}
          onLeave={onLeave}
          onClick={onClick}
        />
      ))}

      {data.profile && <ProfileStar x={data.profile.x} y={data.profile.y} z={data.profile.z} texture={texture} onHover={onHover} onLeave={onLeave} />}

      <OrbitControls
        enableDamping
        dampingFactor={0.08}
        autoRotate={autoRotate}
        autoRotateSpeed={0.5}
        minDistance={3}
        maxDistance={60}
      />
    </>
  )
}

function nodeTypeLabel(nodeType: NodeType): string {
  if (nodeType === 'target_real') return 'Target (real)'
  if (nodeType === 'target_imagined') return 'Target (imagined)'
  return 'Posting'
}

type TemporalMode = 'all' | 'year' | 'range'

// Temporal control state (docs/18 §2). Deliberately a plain {mode, year,
// dateFrom, dateTo} shape that maps 1:1 onto the GET /api/space query params
// (see toFilter below) — a later "time travel"/animated year progression can
// drive this same state on a timer (mode='year', increment `year`) with no
// redesign of the request path.
function toFilter(mode: TemporalMode, year: number | '', dateFrom: string, dateTo: string): SpaceFilter {
  if (mode === 'year' && year !== '') return { year }
  if (mode === 'range') return { date_from: dateFrom || undefined, date_to: dateTo || undefined }
  return {}
}

function TemporalControls({
  mode,
  setMode,
  year,
  setYear,
  dateFrom,
  setDateFrom,
  dateTo,
  setDateTo,
  yearRange,
}: {
  mode: TemporalMode
  setMode: (m: TemporalMode) => void
  year: number | ''
  setYear: (y: number | '') => void
  dateFrom: string
  setDateFrom: (v: string) => void
  dateTo: string
  setDateTo: (v: string) => void
  yearRange: SpaceResponse['year_range']
}) {
  const years = yearRange ? Array.from({ length: yearRange.max - yearRange.min + 1 }, (_, i) => yearRange.max - i) : []
  return (
    <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', padding: '8px 12px', marginTop: 12 }}>
      <span className="secondary" style={{ fontSize: 13 }}>
        Time:
      </span>
      <select value={mode} onChange={(e) => setMode(e.target.value as TemporalMode)}>
        <option value="all">All years{yearRange ? ` (${yearRange.min}–${yearRange.max})` : ''}</option>
        <option value="year">A specific year…</option>
        <option value="range">A date range…</option>
      </select>
      {mode === 'year' && (
        <select value={year} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : '')}>
          <option value="">Choose a year</option>
          {years.map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      )}
      {mode === 'range' && (
        <>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label="From date" />
          <span className="muted">–</span>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label="To date" />
        </>
      )}
      <span className="muted" style={{ fontSize: 12 }}>
        Space shows the full historical cloud by default — targets and your profile are never filtered out by time.
      </span>
    </div>
  )
}

export default function Space() {
  const [data, setData] = useState<SpaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMessage, setRebuildMessage] = useState<string | null>(null)
  const [highlightTrack, setHighlightTrack] = useState<string | null>(null)
  const [mode, setMode] = useState<TemporalMode>('all')
  const [year, setYear] = useState<number | ''>('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const navigate = useNavigate()

  const filter = toFilter(mode, year, dateFrom, dateTo)
  const filterKey = JSON.stringify(filter)

  useEffect(() => {
    api.getSpace(filter).then(setData).catch((e) => setError(String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterKey])

  const rebuildEmbeddings = async () => {
    setRebuilding(true)
    setRebuildMessage(null)
    try {
      const result = await api.rebuildRoleEmbeddings()
      setRebuildMessage(
        `Embedded ${result.embeddings_created} role(s), updated ${result.embeddings_updated}, skipped ${result.skipped}.`,
      )
      api.getSpace(filter).then(setData).catch((e) => setError(String(e)))
    } catch (e) {
      setRebuildMessage(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (data.note) {
    const staleRoles = data.role_count > 0 && data.embedded_role_count < data.role_count
    return (
      <div>
        <p className="muted">{data.note}</p>
        {staleRoles ? (
          <>
            <p className="secondary">
              {data.role_count} role{data.role_count === 1 ? '' : 's'} loaded, but only {data.embedded_role_count}{' '}
              currently {data.embedded_role_count === 1 ? 'has' : 'have'} an embedding for the active model (
              {data.embedding_model}). This is expected right after a model change, or for roles captured before
              embeddings moved into their own table — rebuild to restore Space.
            </p>
            <button className="primary" onClick={rebuildEmbeddings} disabled={rebuilding}>
              {rebuilding ? 'Rebuilding…' : 'Rebuild role embeddings'}
            </button>
            {rebuildMessage && (
              <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
                {rebuildMessage}
              </p>
            )}
          </>
        ) : (
          <p className="secondary">
            {data.role_count} role{data.role_count === 1 ? '' : 's'} loaded — capture at least one more, and a
            profile360 snapshot, to see a projection.
          </p>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
        <div>
          <h1 style={{ fontSize: 22, margin: 0 }}>Career space</h1>
          <p className="secondary" style={{ maxWidth: 640 }}>
            A 3D PCA projection of every captured role, target, and your profile by embedding similarity. Closer
            stars are more semantically similar — the axes themselves carry no direct meaning. Drag to rotate,
            scroll to zoom, click a star to open it. {data.points.length} point{data.points.length === 1 ? '' : 's'}{' '}
            in view.
          </p>
        </div>
        <label className="secondary" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={autoRotate} onChange={(e) => setAutoRotate(e.target.checked)} />
          Drift
        </label>
      </div>

      <TemporalControls
        mode={mode}
        setMode={setMode}
        year={year}
        setYear={setYear}
        dateFrom={dateFrom}
        setDateFrom={setDateFrom}
        dateTo={dateTo}
        setDateTo={setDateTo}
        yearRange={data.year_range}
      />

      <div className="card" style={{ marginTop: 12, padding: 0, overflow: 'hidden', position: 'relative' }}>
        <div style={{ width: '100%', height: 560 }}>
          <Canvas camera={{ position: [0, 0, 14], fov: 50 }}>
            <Scene
              data={data}
              autoRotate={autoRotate}
              hoveredId={hover?.kind === 'role' ? hover.point.id : null}
              highlightTrack={highlightTrack}
              onHover={setHover}
              onLeave={() => setHover(null)}
              onClick={(id) => navigate(`/roles/${id}`)}
            />
            {hover && (
              <Html
                position={
                  hover.kind === 'profile' && data.profile
                    ? [data.profile.x * SCALE, data.profile.y * SCALE, data.profile.z * SCALE]
                    : hover.point
                      ? [hover.point.x * SCALE, hover.point.y * SCALE, hover.point.z * SCALE]
                      : [0, 0, 0]
                }
                style={{ pointerEvents: 'none' }}
                distanceFactor={12}
              >
                <div
                  style={{
                    background: 'rgba(10,10,14,0.9)',
                    border: '1px solid rgba(255,255,255,0.15)',
                    borderRadius: 8,
                    padding: '6px 10px',
                    fontSize: 12,
                    color: '#fff',
                    whiteSpace: 'nowrap',
                    transform: 'translate(12px, -12px)',
                  }}
                >
                  {hover.kind === 'profile' ? (
                    <strong>You</strong>
                  ) : (
                    <>
                      <strong>{hover.point!.title}</strong>
                      <div style={{ opacity: 0.75 }}>
                        {hover.point!.organisation ?? 'Unknown org'} ·{' '}
                        {hover.point!.node_type === 'posting'
                          ? trackLabel(hover.point!.career_track)
                          : nodeTypeLabel(hover.point!.node_type)}
                        {hover.point!.posting_date ? ` · ${hover.point!.posting_date}` : ''}
                      </div>
                    </>
                  )}
                </div>
              </Html>
            )}
          </Canvas>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        {LEGEND_TRACKS.map((t) => (
          <button
            key={t.key}
            onClick={() => setHighlightTrack(highlightTrack === t.key ? null : t.key)}
            title="Click to highlight this track, click again to clear"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 13,
              background: 'none',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              opacity: highlightTrack && highlightTrack !== t.key ? 0.5 : 1,
            }}
          >
            <span aria-hidden style={{ width: 10, height: 10, borderRadius: '50%', background: t.color }} />
            <span className="secondary">{t.label}</span>
          </button>
        ))}
        <LegendDot color="#ffe066" label="You" />
        <LegendDot color="#ffffff" label="Target (real)" />
        <LegendDot color="#c792ff" label="Target (imagined)" />
        <LegendDot color="#ff5c5c" label="Flagged implausible" />
      </div>
    </div>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
      <span aria-hidden style={{ width: 10, height: 10, borderRadius: '50%', background: color }} />
      <span className="secondary">{label}</span>
    </div>
  )
}
