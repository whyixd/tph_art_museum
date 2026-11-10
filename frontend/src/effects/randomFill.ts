import { EFFECT_SCHEMA_VERSION, clamp01, type EffectModule, type EffectSampleContext } from './contract'
import { RAIN_LINES, rainStreak } from './rain'
import { randomRanks } from './randomLeds'

const DEFAULTS = { fillSeconds: 60, acceleration: 2, ledFadeSeconds: 0, holdSeconds: 5, fadeOutSeconds: 3, staggerSeconds: 0 }

type Timing = {
  fill: number; acceleration: number; ledFade: number; hold: number; fadeOut: number
  /** 每台自己一輪的長度。 / One unit's own round. */
  unitCycle: number
  /** 第一台到最後一台開始的間隔。 / From the first unit's start to the last one's. */
  stagger: number
  /** 整輪 = 每台一輪 + 錯落。 / The whole round: a unit's round plus the stagger. */
  cycle: number
}

// 各段長度取整數影格，每台一輪 = 填滿 + 單點淡入 + 全亮停留 + 淡出；錯落時各台依序晚開始，整輪等最後一台做完。
// Whole-frame stages; a unit's round = fill + LED fade + hold + fade out. With a stagger the units start one after
// another and the whole round waits for the last one.
function timing(parameters: Readonly<Record<string, number>>, fps: number): Timing {
  const frames = (key: keyof typeof DEFAULTS, min: number) =>
    Math.max(min, Math.round((parameters[key] ?? DEFAULTS[key]) * fps))
  const fill = frames('fillSeconds', 1)
  const ledFade = frames('ledFadeSeconds', 0)
  const hold = frames('holdSeconds', 0)
  const fadeOut = frames('fadeOutSeconds', 0)
  const stagger = frames('staggerSeconds', 0)
  const acceleration = Math.max(1, parameters.acceleration ?? DEFAULTS.acceleration)
  const unitCycle = fill + ledFade + hold + fadeOut
  return { fill, acceleration, ledFade, hold, fadeOut, unitCycle, stagger, cycle: unitCycle + stagger }
}

let lastOrder: { round: number; unitCount: number; ranks: Uint32Array } | undefined

/** 這一輪這台晚幾格開始：九台順序每輪重新隨機，平均分布在錯落時間內。 / A unit's delay: a fresh random order per round, evenly spread. */
function unitDelay(stagger: number, round: number, unitIndex: number, unitCount: number): number {
  if (stagger === 0 || unitCount <= 1) return 0
  // 以台數當作「台」的種子，和各台燈點的洗牌分開。 / Seeded with unitCount as the unit, apart from each unit's LED shuffle.
  // 每顆燈點都會問一次，記住上一輪的順序，免得和燈點洗牌互相擠掉快取。 / Asked per LED; keep the last order.
  if (lastOrder?.round !== round || lastOrder.unitCount !== unitCount) {
    lastOrder = { round, unitCount, ranks: randomRanks(round, unitCount, unitCount) }
  }
  const order = lastOrder.ranks[unitIndex]
  return Math.round((stagger * order) / (unitCount - 1))
}

function greatestCommonDivisor(a: number, b: number): number {
  return b === 0 ? a : greatestCommonDivisor(b, a % b)
}

// 以約半數的互質步長跳著排列 unit，相鄰 unit 在色盤上相隔約一半。
// Stride through the units by a coprime step near half, so neighbours sit about half the palette apart.
export function unitPalettePosition(unitIndex: number, unitCount: number): number {
  if (unitCount <= 1) return 0
  let stride = Math.max(1, Math.floor(unitCount / 2))
  while (stride > 1 && greatestCommonDivisor(stride, unitCount) !== 1) stride -= 1
  return ((unitIndex * stride) % unitCount) / (unitCount - 1)
}

const unitPosition = (context: EffectSampleContext) => unitPalettePosition(context.unitIndex, context.unitCount)

export const randomFill: EffectModule = {
  completion: {
    label: '燈點隨機填滿、停留並淡出',
    frames: (parameters, fps) => timing(parameters, fps).cycle,
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'random-fill',
    label: '隨機填滿',
    description: '從全暗開始，每次在隨機位置點亮一顆燈點，一開始很慢、越來越快，直到填滿整條燈帶，停留後淡出再重新開始。錯落開始大於 0 時，各台依每輪重新隨機的順序先後開始，最後一台晚這麼多秒，全部做完才進下一輪。每組從色盤取一個純色，相鄰組別跳著取色，沒有顏色連續性。',
    parameters: [
      { key: 'fillSeconds', label: '填滿時間', unit: 's', min: 5, max: 300, step: 5, defaultValue: DEFAULTS.fillSeconds },
      { key: 'acceleration', label: '加速程度', unit: '倍', min: 1, max: 4, step: 0.25, defaultValue: DEFAULTS.acceleration },
      { key: 'ledFadeSeconds', label: '單點淡入', unit: 's', min: 0, max: 5, step: 0.25, defaultValue: DEFAULTS.ledFadeSeconds },
      { key: 'holdSeconds', label: '全亮停留', unit: 's', min: 0, max: 30, step: 1, defaultValue: DEFAULTS.holdSeconds },
      { key: 'fadeOutSeconds', label: '淡出時間', unit: 's', min: 0, max: 10, step: 0.5, defaultValue: DEFAULTS.fadeOutSeconds },
      { key: 'staggerSeconds', label: '錯落開始', unit: 's', min: 0, max: 30, step: 0.25, defaultValue: DEFAULTS.staggerSeconds },
    ],
  },

  sample(context, parameters) {
    if (context.ledCount <= 0) return { r: 0, g: 0, b: 0 }
    const { fill, acceleration, ledFade, hold, fadeOut, unitCycle, stagger, cycle } = timing(parameters, context.controllerFps)
    const frame = Math.max(0, context.controllerFrame)
    const round = Math.floor(frame / cycle)
    const elapsed = frame - round * cycle - unitDelay(stagger, round, context.unitIndex, context.unitCount)
    // 還沒輪到或已做完的台全暗。 / Dark before this unit's start and after its round.
    if (elapsed < 0 || elapsed >= unitCycle) return { r: 0, g: 0, b: 0 }
    // 每一輪、每組各自重新洗牌；同組兩條半圓共用順序。 / Reshuffle per round and unit; both arcs share the order.
    const rank = randomRanks(round, context.unitIndex, context.ledCount)[context.ledIndex]
    // 一輪從全暗開始逐顆點亮，已亮數量 = 燈數 × (經過時間 / 填滿時間)^加速程度：一開始慢、後面越來越快，
    // 最後一顆在填滿時間點亮；加速程度 1 為等速。
    // Each round starts dark; lit count = ledCount * (t / fill)^acceleration, so lighting starts slow and speeds up,
    // with the last LED at the fill time. Acceleration 1 is a constant rate.
    const start = Math.floor(fill * ((rank + 1) / context.ledCount) ** (1 / acceleration))
    const lit = elapsed < start ? 0 : ledFade === 0 ? 1 : clamp01((elapsed - start + 1) / ledFade)
    const fadeStart = fill + ledFade + hold
    const out = fadeOut === 0 || elapsed < fadeStart ? 1 : clamp01(1 - (elapsed - fadeStart + 1) / fadeOut)
    const brightness = lit * out
    return { r: brightness, g: brightness, b: brightness }
  },
  // 雨天：點亮順序不變，已亮的燈改閃雨絲，填滿時等於全亮的滿版雨絲。
  // Rain: same order, lit LEDs flash streaks instead, so the filled disc matches full-on's rain.
  rain: (context, _parameters, base) => base() * rainStreak(context, RAIN_LINES.sparseDensity),
  // 不論漸層方向，每組都取一個純色。 / Every axis gives each unit one solid colour.
  gradientPosition: unitPosition,
  unitGradientPosition: unitPosition,
}
