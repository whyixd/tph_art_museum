/**
 * 美術館轉燈幾何：每台 3 條燈條並聯（同一份資料），每條沿垂直圓周繞一整圈，由頂部起往下繞；
 * 三條在水平面上相隔 60°，俯視是 6 條半圓。轉子繞垂直軸旋轉。
 *
 * Museum spinner geometry: 3 strips wired in parallel (same data), each running once around a vertical circle from
 * the top; the strips sit 60° apart around the vertical axis (6 half circles from above). The rotor spins about the
 * vertical axis. Physical LED i shows effect pixel (i + pixelRotation) % count (backend `[hardware] pixel_rotation`).
 */
import type { ScenePoint } from './layout'

export const STRIP_COUNT = 3
export const DEFAULT_LED_COUNT = 129
export const DEFAULT_PIXEL_ROTATION = 64
/** 轉燈半徑（m）：側視圖紅點（頂端）比中心高約 35 cm。 / Rotor radius (m): the drawn top sits ~35 cm above the centre. */
export const SPINNER_RADIUS = 0.35

export type LoopLed = {
  /** Physical index along the strip (0 = top). */
  index: number
  strip: number
  /** Effect pixel shown by this LED. */
  effectIndex: number
  position: ScenePoint
  /** Outward unit normal. */
  normal: ScenePoint
}

/** Effect pixel shown by physical LED `index`. */
export function effectIndexFor(index: number, count: number, rotation: number): number {
  return ((index + rotation) % count + count) % count
}

/** Angle from the top (radians) of physical LED `index`, evenly around the full circle. */
export function loopAngle(index: number, count: number): number {
  return (2 * Math.PI * index) / Math.max(1, count)
}

/** Local position (rotor at rest) of physical LED `index` on `strip`, radius `radius`. */
export function loopLedPosition(index: number, count: number, strip: number, radius = SPINNER_RADIUS): ScenePoint {
  const angle = loopAngle(index, count)
  const plane = (strip * Math.PI) / STRIP_COUNT
  const across = Math.sin(angle) * radius
  return [across * Math.cos(plane), Math.cos(angle) * radius, across * Math.sin(plane)]
}

export function buildLoopLeds(count: number, rotation: number, radius = SPINNER_RADIUS): LoopLed[] {
  const leds: LoopLed[] = []
  for (let strip = 0; strip < STRIP_COUNT; strip += 1) {
    for (let index = 0; index < count; index += 1) {
      const position = loopLedPosition(index, count, strip, radius)
      leds.push({
        index,
        strip,
        effectIndex: effectIndexFor(index, count, rotation),
        position,
        normal: [position[0] / radius, position[1] / radius, position[2] / radius],
      })
    }
  }
  return leds
}
