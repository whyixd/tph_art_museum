import {
  EFFECT_SCHEMA_VERSION,
  clamp01,
  effectTimeSeconds,
  type EffectModule,
  type EffectSampleContext,
  type RgbColor,
} from './contract'

const TAU = Math.PI * 2
const BLACK = { r: 0, g: 0, b: 0 }

/**
 * 每個環在這顆燈的亮度。每個環都在整個半圓（頂部 0 到底部 1）上往返，相鄰的環方向相反、相位錯開 π/N，所以會互相穿過。
 * 環心投影到相鄰兩個半徑座標的燈上，兩顆亮度加起來是 1，移動時不會跳格。
 * Each ring's brightness at this LED. Every ring travels the whole half (top 0 to bottom 1); neighbours move in
 * opposite directions with phases π/N apart, so they cross. The centre is split between the two nearest radial LED
 * coordinates (weights summing to 1), so motion has no jumps.
 */
export function ringWeights(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number[] {
  const speedHz = parameters.speedHz ?? 0.25
  const count = Math.min(6, Math.max(1, Math.round(parameters.ringCount ?? 3)))
  const time = effectTimeSeconds(context)
  const step = context.ledCount <= 1 ? 1 : 2 / (context.ledCount - 1)
  const offset = context.ledCount % 2 === 0 ? step / 2 : 0
  const tolerance = step * 1e-6
  const weights: number[] = []
  for (let ring = 0; ring < count; ring += 1) {
    const direction = ring % 2 === 0 ? 1 : -1
    const centre = 0.5 + 0.5 * direction * Math.sin(TAU * speedHz * time + (Math.PI * ring) / count)
    const position = (Math.max(offset, clamp01(centre)) - offset) / step
    const lower = Math.floor(position)
    const upper = Math.ceil(position)
    const blend = position - lower
    let weight = 0
    if (Math.abs(context.normalizedRadius - (offset + lower * step)) <= tolerance) weight = Math.max(weight, 1 - blend)
    if (Math.abs(context.normalizedRadius - (offset + upper * step)) <= tolerance) weight = Math.max(weight, blend)
    weights.push(weight)
  }
  return weights
}

export const movingRings: EffectModule = {
  completion: {
    label: '光環完成一次往返',
    frames: (parameters, fps) => fps / (parameters.speedHz ?? 0.25),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'moving-rings',
    label: 'Moving rings / 往返光環',
    description: '幾個光環在整個半圓上由頂部到底部往返，相鄰的環方向相反、時間錯開，會互相穿過。漸層時第 1 個環用第 1 色、第 2 個環用第 2 色……（顏色不夠時從頭輪流），環重疊處顏色相加；純色時所有環同一色。',
    parameters: [
      { key: 'speedHz', label: 'Movement speed', unit: 'Hz', min: 0.05, max: 1.5, step: 0.05, defaultValue: 0.25 },
      { key: 'ringCount', label: 'Ring count', unit: 'rings', min: 1, max: 6, step: 1, defaultValue: 3 },
    ],
  },

  sample(context, parameters) {
    const brightness = Math.min(1, ringWeights(context, parameters).reduce((sum, weight) => sum + weight, 0))
    return brightness === 0 ? BLACK : { r: brightness, g: brightness, b: brightness }
  },
  // 漸層：第 k 個環取第 k 色，重疊處相加。 / Gradient: ring k takes colour k; overlaps add up.
  gradientColor(context, parameters, colors) {
    const total: RgbColor = { r: 0, g: 0, b: 0 }
    ringWeights(context, parameters).forEach((weight, ring) => {
      if (weight <= 0) return
      const color = colors[ring % colors.length]
      total.r += color.r * weight
      total.g += color.g * weight
      total.b += color.b * weight
    })
    return { r: clamp01(total.r), g: clamp01(total.g), b: clamp01(total.b) }
  },
}
