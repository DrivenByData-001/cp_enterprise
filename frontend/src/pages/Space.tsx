import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Canvas, useFrame } from '@react-three/fiber'
import { Html, OrbitControls, Stars } from '@react-three/drei'
import * as THREE from 'three'
import { api, type NodeType, type SpacePoint, type SpaceResponse } from '../lib/api'
import { LEGEND_TRACKS, trackLabel } from '../lib/trackColor'
import { nodeAccentColor, PROFILE_COLOR, trackStarColor } from '../lib/spaceColors'

// Scales the (small, roughly [-1, 1]) PCA coordinates out into a roomier 3D scene.
const SCALE = 4.5

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
  onHover,
  onLeave,
  onClick,
}: {
  point: SpacePoint
  texture: THREE.Texture
  onHover: (h: HoverInfo) => void
  onLeave: () => void
  onClick: (id: string) => void
}) {
  const ref = useRef<THREE.Sprite>(null)
  const isTarget = point.node_type !== 'posting'
  const color = isTarget ? nodeAccentColor(point.node_type, point.is_plausible) : trackStarColor(point.career_track)
  const baseScale = isTarget ? 1.5 : 0.9
  const phaseSeed = point.x + point.y + point.z

  useFrame(({ clock }) => {
    if (!ref.current) return
    if (isTarget) {
      const pulse = 1 + Math.sin(clock.elapsedTime * 2 + phaseSeed) * 0.15
      ref.current.scale.setScalar(baseScale * pulse)
    }
  })

  return (
    <sprite
      ref={ref}
      position={[point.x * SCALE, point.y * SCALE, point.z * SCALE]}
      scale={baseScale}
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
      <spriteMaterial map={texture} color={color} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
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
  useFrame(({ clock }) => {
    if (!ref.current) return
    const pulse = 1 + Math.sin(clock.elapsedTime * 1.6) * 0.1
    ref.current.scale.setScalar(2.2 * pulse)
  })
  return (
    <sprite
      ref={ref}
      position={[x * SCALE, y * SCALE, z * SCALE]}
      scale={2.2}
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
      <spriteMaterial
        map={texture}
        color={PROFILE_COLOR}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </sprite>
  )
}

function Scene({
  data,
  autoRotate,
  onHover,
  onLeave,
  onClick,
}: {
  data: SpaceResponse
  autoRotate: boolean
  onHover: (h: HoverInfo) => void
  onLeave: () => void
  onClick: (id: string) => void
}) {
  const texture = useStarTexture()

  return (
    <>
      <color attach="background" args={['#04050a']} />
      <ambientLight intensity={0.6} />
      <Stars radius={120} depth={60} count={3000} factor={2.5} saturation={0} fade speed={0.4} />

      {data.points.map((p) => (
        <RoleStar key={p.id} point={p} texture={texture} onHover={onHover} onLeave={onLeave} onClick={onClick} />
      ))}

      {data.profile && (
        <ProfileStar
          x={data.profile.x}
          y={data.profile.y}
          z={data.profile.z}
          texture={texture}
          onHover={onHover}
          onLeave={onLeave}
        />
      )}

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

export default function Space() {
  const [data, setData] = useState<SpaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [autoRotate, setAutoRotate] = useState(true)
  const [rebuilding, setRebuilding] = useState(false)
  const [rebuildMessage, setRebuildMessage] = useState<string | null>(null)
  const navigate = useNavigate()

  const reload = () => api.getSpace().then(setData).catch((e) => setError(String(e)))

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const rebuildEmbeddings = async () => {
    setRebuilding(true)
    setRebuildMessage(null)
    try {
      const result = await api.rebuildRoleEmbeddings()
      setRebuildMessage(
        `Embedded ${result.embeddings_created} role(s), updated ${result.embeddings_updated}, skipped ${result.skipped}.`,
      )
      await reload()
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
            scroll to zoom, click a star to open it.
          </p>
        </div>
        <label className="secondary" style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={autoRotate} onChange={(e) => setAutoRotate(e.target.checked)} />
          Drift
        </label>
      </div>

      <div className="card" style={{ marginTop: 16, padding: 0, overflow: 'hidden', position: 'relative' }}>
        <div style={{ width: '100%', height: 560 }}>
          <Canvas camera={{ position: [0, 0, 14], fov: 50 }}>
            <Scene
              data={data}
              autoRotate={autoRotate}
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
          <div key={t.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <span aria-hidden style={{ width: 10, height: 10, borderRadius: '50%', background: t.color }} />
            <span className="secondary">{t.label}</span>
          </div>
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
