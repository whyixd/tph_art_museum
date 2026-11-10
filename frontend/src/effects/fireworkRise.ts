import { EFFECT_SCHEMA_VERSION, type EffectModule, type EffectSampleContext } from './contract'
import { hash01 } from './rouletteRing'

// 參考 WLED Meteor：流星頭全亮，走過的燈各自以不同速度衰減，尾巴不規則地碎開。WLED 每格隨機衰減（需要上一格的畫面），
// 這裡改成只看「流星頭離開多久」與每顆燈固定種子的衰減速度，前後端可逐格重算。
// After WLED's Meteor: a fully lit head, and every LED it passed fades at its own rate so the tail breaks up
// irregularly. WLED decays at random each frame (it needs the previous frame); here the brightness depends only on
// how long ago the head left and a fixed-seed rate per LED, so any frame can be computed on its own.

const DEFAULTS = {
  travelSeconds: 1.5, gapSeconds: 1, headSize: 0.08, trailSeconds: 1, randomness: 0.6, direction: 0, trailColor: 0,
}
/** 衰減到 1/255 所需的「拖尾時間」倍數：exp(-5.5) ≈ 1/245。 / exp(-5.5) ≈ 1/245 at the trail time. */
const TRAIL_DECAY = 5.5
const RATE_SALT = 500

type Timing = { travel: number; gap: number; cycle: number }

export function meteorTiming(parameters: Readonly<Record<string, number>>, fps: number): Timing {
  const travel = Math.max(1, Math.round((parameters.travelSeconds ?? DEFAULTS.travelSeconds) * fps))
  const gap = Math.max(0, Math.round((parameters.gapSeconds ?? DEFAULTS.gapSeconds) * fps))
  return { travel, gap, cycle: travel + gap }
}

/** 0..1：流星頭為 1，拖尾逐漸變暗；還沒有流星經過為 0。 / 1 in the head, fading along the tail; 0 before any pass. */
export function meteorStrength(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number {
  const { travel, cycle } = meteorTiming(parameters, context.controllerFps)
  const frame = Math.max(0, context.controllerFrame)
  const round = Math.floor(frame / cycle)
  const elapsed = frame - round * cycle
  // 兩邊半圓對稱；方向 0 由底部往上，1 由頂部往下。 / Both halves alike; direction 0 rises from the bottom, 1 falls from the top.
  const height = 1 - context.normalizedRadius
  const along = (parameters.direction ?? DEFAULTS.direction) === 0 ? height : 1 - height
  // 流星頭在這一輪第 along × travel 格到達；還沒到時看上一輪那顆流星。
  // The head reaches this LED at frame along × travel of the round; before that, the previous round's meteor counts.
  const pass = along * travel
  const launch = elapsed >= pass ? round : round - 1
  if (launch < 0) return 0
  const since = elapsed >= pass ? elapsed - pass : elapsed + cycle - pass
  const headFrames = (parameters.headSize ?? DEFAULTS.headSize) * travel
  if (since < headFrames) return 1
  // 每顆燈每次流星各取一個衰減速度倍數（1 ± 碎裂程度）。 / Each LED and meteor gets its own rate, 1 ± randomness.
  const randomness = parameters.randomness ?? DEFAULTS.randomness
  const scale = Math.max(0.05, 1 + randomness * (2 * hash01(launch, context.unitIndex, RATE_SALT + context.ledIndex) - 1))
  const trail = Math.max(1, (parameters.trailSeconds ?? DEFAULTS.trailSeconds) * context.controllerFps)
  const value = Math.exp((-TRAIL_DECAY * (since - headFrames)) / (trail * scale))
  return value < 1 / 255 ? 0 : value
}

// 依拖尾變色：漸層第一色是流星頭、越往尾巴越接近最後一色；否則依漸層方向取色。
// Colour by trail: the first gradient colour is the head, the tail moves toward the last; otherwise the usual axis.
const trailColorOn = (parameters?: Readonly<Record<string, number>>) => (parameters?.trailColor ?? DEFAULTS.trailColor) === 1

export const fireworkRise: EffectModule = {
  completion: {
    label: '流星由底部升到頂部一次（含間隔）',
    frames: (parameters, fps) => meteorTiming(parameters, fps).cycle,
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'firework-rise',
    label: '煙火上升',
    description: '參考 WLED Meteor：兩邊半圓同時有一顆流星由底部往上衝到頂部（方向 1 時由頂部往下），流星頭全亮，走過的燈點各自以不同速度變暗，尾巴不規則地碎開；間隔後再發射下一顆。碎裂程度越高，尾巴越零散。開啟「依拖尾變色」時，漸層第一色是流星頭，尾巴漸漸變成最後一色（例如白→金→紅）。',
    parameters: [
      { key: 'travelSeconds', label: '上升時間', unit: 's', min: 0.25, max: 10, step: 0.25, defaultValue: DEFAULTS.travelSeconds },
      { key: 'gapSeconds', label: '發射間隔', unit: 's', min: 0, max: 10, step: 0.25, defaultValue: DEFAULTS.gapSeconds },
      { key: 'headSize', label: '流星頭長度', unit: '半圓比例', min: 0.02, max: 0.3, step: 0.01, defaultValue: DEFAULTS.headSize },
      { key: 'trailSeconds', label: '拖尾時間', unit: 's', min: 0.1, max: 5, step: 0.1, defaultValue: DEFAULTS.trailSeconds },
      { key: 'randomness', label: '碎裂程度', unit: '', min: 0, max: 1, step: 0.05, defaultValue: DEFAULTS.randomness },
      { key: 'direction', label: '方向', unit: '0 上升／1 下降', min: 0, max: 1, step: 1, defaultValue: DEFAULTS.direction },
      { key: 'trailColor', label: '依拖尾變色', unit: '0 關／1 開', min: 0, max: 1, step: 1, defaultValue: DEFAULTS.trailColor },
    ],
  },

  sample(context, parameters) {
    const value = meteorStrength(context, parameters)
    return { r: value, g: value, b: value }
  },
  gradientPosition: (context, axis, _colorCount, parameters) => trailColorOn(parameters)
    ? 1 - meteorStrength(context, parameters ?? {})
    : axis === 'radius' ? context.normalizedRadius : context.normalizedStripPosition,
  unitGradientPosition: (context, parameters) => trailColorOn(parameters)
    ? 1 - meteorStrength(context, parameters ?? {})
    : context.unitCount <= 1 ? 0 : context.unitIndex / (context.unitCount - 1),
}
