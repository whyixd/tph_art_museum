import { EFFECT_SCHEMA_VERSION, clamp01, effectTimeSeconds, type EffectModule, type EffectSampleContext } from './contract'

const DEFAULTS = { periodSeconds: 4, tailLength: 0.3 }

const cycle = (value: number) => value - Math.floor(value)
const smoothstep = (value: number) => {
  const t = clamp01(value)
  return t * t * (3 - 2 * t)
}

/**
 * 拖尾強度 0..1：燈帶從中間分成兩個半圓（中間在頂部、兩端在底部），亮點在兩邊半圓同時由底部往上、到頂再往下，
 * 一個來回 = periodSeconds。每顆燈依「亮點上次經過它多久」決定：剛經過是 1，經過半圓 tailLength 的時間後回到 0。
 * Trail strength 0..1. The strip splits into two halves (middle at the top, ends at the bottom); on both halves a dot
 * runs from the bottom up to the top and back, one round trip per periodSeconds. Each LED shows how long ago the dot
 * passed it: 1 right after, back to 0 once the dot has covered tailLength of the half.
 */
export function twoToneTrailStrength(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number {
  const period = parameters.periodSeconds ?? DEFAULTS.periodSeconds
  const phase = cycle(effectTimeSeconds(context) / period)
  // 半徑 0 = 頂部，1 = 底部；上行在 (1 - r) / 2 經過，下行在 0.5 + r / 2 經過（一個來回的比例）。
  // Radius 0 is the top, 1 the bottom; going up the dot passes at (1 - r) / 2, coming down at 0.5 + r / 2.
  const radius = context.normalizedRadius
  const since = Math.min(cycle(phase - (1 - radius) / 2), cycle(phase - (0.5 + radius / 2)))
  // 走完半圓要半個來回，所以拖尾長度換成來回比例要除以 2。 / Covering the half takes half a round trip.
  const tail = (parameters.tailLength ?? DEFAULTS.tailLength) / 2
  return 1 - smoothstep(since / tail)
}

const trailPosition = (context: EffectSampleContext, parameters?: Readonly<Record<string, number>>) =>
  twoToneTrailStrength(context, parameters ?? {})

export const twoToneTrail: EffectModule = {
  completion: {
    label: '亮點在半圓上下來回一次',
    frames: (parameters, fps) => fps * (parameters.periodSeconds ?? DEFAULTS.periodSeconds),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'two-tone-trail',
    label: '雙色拖尾',
    description: '燈帶分成左右兩個半圓，整條先亮第一個顏色當底色；亮點在兩邊半圓同時由底部往上、到頂再往下來回移動，經過的燈點變成第二個顏色並帶著拖尾，漸漸回到底色。漸層第一色是底色、最後一色是亮點（中間的顏色是拖尾經過的顏色），不論漸層方向都一樣；純色時沒有底色，只有亮點與拖尾。',
    parameters: [
      { key: 'periodSeconds', label: '來回週期', unit: 's', min: 0.5, max: 20, step: 0.5, defaultValue: DEFAULTS.periodSeconds },
      { key: 'tailLength', label: '拖尾長度', unit: '半圓比例', min: 0.05, max: 1, step: 0.05, defaultValue: DEFAULTS.tailLength },
    ],
  },
  // 漸層時整條全亮（底色），亮點與拖尾靠顏色表現；純色時只亮亮點與拖尾。
  // Gradient: fully lit (the base colour), the dot and trail are colour; solid: only the dot and trail light.
  brightness: (context, colorMode, parameters) =>
    colorMode === 'gradient' ? 1 : twoToneTrailStrength(context, parameters ?? {}),
  sample(context, parameters) {
    const strength = twoToneTrailStrength(context, parameters)
    return { r: strength, g: strength, b: strength }
  },
  gradientPosition: (context, _axis, _colorCount, parameters) => trailPosition(context, parameters),
  unitGradientPosition: trailPosition,
}
