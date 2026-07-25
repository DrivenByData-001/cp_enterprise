// Numeric colors for the three.js starfield — CSS custom properties aren't resolvable
// by WebGL materials, so this mirrors trackColor.ts's palette as hex literals tuned to
// glow against a permanently dark "outer space" canvas background.
import type { NodeType } from './api'

const TRACK_STAR_COLORS: Record<string, number> = {
  actuarial: 0x5b9dff,
  data_science: 0xff8a4c,
  quant: 0x35d6a0,
  risk: 0x35d6a0,
}

export function trackStarColor(track: string | null): number {
  if (!track) return 0xb9b8ae
  return TRACK_STAR_COLORS[track] ?? 0xb9b8ae
}

export const PROFILE_COLOR = 0xffe066 // warm "sun" — you are here
export const TARGET_REAL_COLOR = 0xffffff // bright white star — a real, reachable role
export const TARGET_IMAGINED_COLOR = 0xc792ff // violet star — a synthesized/hypothetical role
export const TARGET_IMPLAUSIBLE_COLOR = 0xff5c5c // red-tinted — flagged as not realistically reachable

export function nodeAccentColor(nodeType: NodeType, isPlausible: boolean | null): number {
  if (nodeType === 'target_imagined') {
    return isPlausible === false ? TARGET_IMPLAUSIBLE_COLOR : TARGET_IMAGINED_COLOR
  }
  if (nodeType === 'target_real') {
    return isPlausible === false ? TARGET_IMPLAUSIBLE_COLOR : TARGET_REAL_COLOR
  }
  return 0
}
