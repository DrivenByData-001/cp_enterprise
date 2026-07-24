import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type SpacePoint, type SpaceResponse } from '../lib/api'
import { LEGEND_TRACKS, trackColor, trackLabel } from '../lib/trackColor'

const WIDTH = 900
const HEIGHT = 560
const PAD = 40

export default function Space() {
  const [data, setData] = useState<SpaceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [hover, setHover] = useState<{ point: SpacePoint; x: number; y: number } | null>(null)
  const navigate = useNavigate()
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.getSpace().then(setData).catch((e) => setError(String(e)))
  }, [])

  const scaled = useMemo(() => {
    if (!data || data.points.length === 0) return null
    const xs = data.points.map((p) => p.x).concat(data.profile ? [data.profile.x] : [])
    const ys = data.points.map((p) => p.y).concat(data.profile ? [data.profile.y] : [])
    const xMin = Math.min(...xs)
    const xMax = Math.max(...xs)
    const yMin = Math.min(...ys)
    const yMax = Math.max(...ys)
    const xRange = xMax - xMin || 1
    const yRange = yMax - yMin || 1

    const sx = (x: number) => PAD + ((x - xMin) / xRange) * (WIDTH - 2 * PAD)
    const sy = (y: number) => HEIGHT - PAD - ((y - yMin) / yRange) * (HEIGHT - 2 * PAD)

    return {
      points: data.points.map((p) => ({ ...p, sx: sx(p.x), sy: sy(p.y) })),
      profile: data.profile ? { sx: sx(data.profile.x), sy: sy(data.profile.y) } : null,
    }
  }, [data])

  if (error) return <p style={{ color: 'var(--critical)' }}>{error}</p>
  if (!data) return <p className="muted">Loading…</p>
  if (data.note) return <p className="muted">{data.note}</p>

  return (
    <div>
      <h1 style={{ fontSize: 22 }}>Career space</h1>
      <p className="secondary">
        A 2D projection (PCA) of captured roles by embedding similarity. Closer roles are more semantically similar
        — the axes themselves carry no direct meaning.
      </p>

      <div className="card" style={{ marginTop: 16, position: 'relative' }} ref={containerRef}>
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" style={{ display: 'block' }}>
          <line x1={PAD} y1={HEIGHT / 2} x2={WIDTH - PAD} y2={HEIGHT / 2} stroke="var(--gridline)" strokeWidth={1} />
          <line x1={WIDTH / 2} y1={PAD} x2={WIDTH / 2} y2={HEIGHT - PAD} stroke="var(--gridline)" strokeWidth={1} />

          {scaled?.points.map((p) => (
            <circle
              key={p.id}
              cx={p.sx}
              cy={p.sy}
              r={6}
              fill={trackColor(p.career_track)}
              stroke="var(--surface-1)"
              strokeWidth={2}
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => setHover({ point: p, x: p.sx, y: p.sy })}
              onMouseLeave={() => setHover(null)}
              onClick={() => navigate(`/roles/${p.id}`)}
            />
          ))}

          {scaled?.profile && (
            <path
              d={diamondPath(scaled.profile.sx, scaled.profile.sy, 9)}
              fill="var(--text-primary)"
              stroke="var(--surface-1)"
              strokeWidth={2}
            />
          )}
        </svg>

        {hover && (
          <div
            style={{
              position: 'absolute',
              left: `${(hover.x / WIDTH) * 100}%`,
              top: `${(hover.y / HEIGHT) * 100}%`,
              transform: 'translate(-50%, -130%)',
              background: 'var(--surface-1)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              padding: '6px 10px',
              fontSize: 12,
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
              boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
            }}
          >
            <strong>{hover.point.title}</strong>
            <div className="secondary">
              {hover.point.organisation ?? 'Unknown org'} · {trackLabel(hover.point.career_track)}
            </div>
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        {LEGEND_TRACKS.map((t) => (
          <div key={t.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <span aria-hidden style={{ width: 10, height: 10, borderRadius: '50%', background: t.color }} />
            <span className="secondary">{t.label}</span>
          </div>
        ))}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
          <span aria-hidden style={{ width: 10, height: 10, background: 'var(--text-primary)', transform: 'rotate(45deg)' }} />
          <span className="secondary">You</span>
        </div>
      </div>
    </div>
  )
}

function diamondPath(cx: number, cy: number, r: number) {
  return `M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`
}
