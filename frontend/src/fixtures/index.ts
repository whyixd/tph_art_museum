/**
 * DMX fixture effects (ball lights, wall washers): one colour per fixture per frame.
 * Reference implementation; backend/moon_observe_backend/fixture_effects.py must match it value for value
 * (checked by scripts/generateFixtureVectors.ts → backend/fixtures/fixtures_v1.json).
 *
 * 球燈／洗牆燈效果：每顆每影格一個顏色；後端 fixture_effects.py 逐值一致。
 */
import { clamp01, type RgbColor } from '../effects/contract'
import { sampleGradient } from '../effects/color'
import { hash01 } from '../effects/rouletteRing'
import { sampleWindBand, windFlowParameters, WIND_FLOW_COLORS, WIND_FLOW_FIXTURE_DESCRIPTION } from '../wind/windPreview'

export const FIXTURE_SCHEMA_VERSION = 1 as const

export type FixtureEffectId =
  | 'off'
  | 'fixture-solid'
  | 'fixture-breathing'
  | 'height-sweep'
  | 'space-wave'
  | 'fixture-twinkle'
  | 'fixture-chase'
  | 'fixture-firework'
  | 'fixture-rise'
  | 'pole-rise'
  | 'base-wave'
  | 'horizontal-flow'
  | 'wind-flow'

export type FixtureAxis = 'height' | 'x' | 'y' | 'index'

export type FixtureColor =
  | { mode: 'solid'; color: RgbColor; weatherPalette?: false }
  | { mode: 'gradient'; colors: RgbColor[]; axis: FixtureAxis; weatherPalette?: false }

export type FixturePreset = {
  schemaVersion: typeof FIXTURE_SCHEMA_VERSION
  effectId: FixtureEffectId
  parameters: Record<string, number>
  color: FixtureColor
  /** 0..1 group ceiling (wall washers are bright). Omitted means 1. */
  maxLevel?: number
}

export type FixtureContext = {
  controllerFrame: number
  controllerFps: number
  /** Order by fixture id (the DMX chain order). */
  fixtureIndex: number
  fixtureCount: number
  /** Position within the group, 0..1 (fixture order when the layout lacks that axis). */
  normalizedHeight: number
  normalizedX: number
  normalizedY: number
  /** 第幾根柱子（0 起）與柱子總數；沒有時每顆自成一根（依編號）。 / Pole rank and count; each fixture alone when missing. */
  poleRank?: number
  poleCount?: number
  /** 在柱子上由低到高第幾顆（0 起）。 / Order on its pole, low to high. */
  poleStep?: number
}

export type FixtureParameterDefinition = {
  key: string
  label: string
  unit: string
  min: number
  max: number
  step: number
  defaultValue: number
}

export type FixtureManifestEntry = {
  id: FixtureEffectId
  label: string
  description: string
  parameters: readonly FixtureParameterDefinition[]
}

const parameter = (
  key: string, label: string, unit: string, min: number, max: number, step: number, defaultValue: number,
): FixtureParameterDefinition => ({ key, label, unit, min, max, step, defaultValue })
const direction = parameter('direction', '方向', '0/1', 0, 1, 1, 0)

export const FIXTURE_MANIFEST: readonly FixtureManifestEntry[] = [
  { id: 'off', label: '關閉', description: '全暗。', parameters: [] },
  { id: 'fixture-solid', label: '恆亮', description: '全部恆亮；漸層可依高度、x、y 或編號分佈。', parameters: [] },
  {
    id: 'fixture-breathing', label: '呼吸', description: '整體一起呼吸，最暗保留 minLevel。',
    parameters: [parameter('breatheHz', '頻率', 'Hz', 0.05, 1, 0.05, 0.2), parameter('minLevel', '最低亮度', '', 0, 1, 0.05, 0.1)],
  },
  {
    id: 'height-sweep', label: '高度掃描', description: '一條光帶依高度由下往上（方向 1：由上往下）掃過。',
    parameters: [parameter('speedHz', '頻率', 'Hz', 0.05, 2, 0.05, 0.25), parameter('width', '光帶寬度', '', 0.05, 1, 0.05, 0.3), direction],
  },
  {
    id: 'space-wave', label: '空間波', description: '亮度波沿 x 方向推進（方向 1：反向）。',
    parameters: [parameter('speedHz', '頻率', 'Hz', 0.05, 2, 0.05, 0.2), parameter('waveCount', '波數', '', 0.5, 4, 0.5, 1), direction],
  },
  {
    id: 'fixture-twinkle', label: '閃爍', description: '每顆各自隨機亮起並淡出；每段 holdSeconds，亮起機率 density。',
    parameters: [parameter('holdSeconds', '每段時間', 's', 0.1, 10, 0.1, 1), parameter('density', '亮起比例', '', 0.05, 1, 0.05, 0.3)],
  },
  {
    id: 'fixture-chase', label: '巡行', description: '依編號（DMX 鏈順序）逐顆巡行，後面拖 tailCount 顆漸暗。',
    parameters: [parameter('stepSeconds', '每步時間', 's', 0.05, 5, 0.05, 0.3), parameter('tailCount', '尾巴顆數', '', 0, 10, 1, 2), direction],
  },
  {
    id: 'fixture-firework', label: '煙火蔓延',
    description: '像煙火：從最低的球燈開始，依高度往高處一顆接一顆炸開（最高一顆晚 spreadSeconds 秒）；每顆先全亮，再隨機閃爍並漸暗，持續 burstSeconds 秒。全部結束後全暗 gapSeconds 秒再重新開始。預設一輪 5 秒，剛好是轉燈「煙火上升」兩顆流星。',
    parameters: [
      parameter('spreadSeconds', '蔓延時間', 's', 0, 10, 0.25, 2),
      parameter('burstSeconds', '每顆持續', 's', 0.25, 10, 0.25, 2.5),
      parameter('gapSeconds', '全暗時間', 's', 0, 10, 0.25, 0.5),
      parameter('density', '亮起比例', '', 0.05, 1, 0.05, 0.6),
      parameter('flickerFrames', '閃爍間隔', '影格', 1, 20, 1, 2),
    ],
  },
  {
    id: 'fixture-rise', label: '由低往高點亮',
    description: '依高度由最低的球燈開始往上逐顆緩慢點亮（最高一顆晚 riseSeconds 秒開始），每顆在 fadeInSeconds 內柔和地淡入。循環為 0 時亮完就保持全亮；為 1 時全亮停留後一起淡出、全暗，再重新開始。',
    parameters: [
      parameter('riseSeconds', '蔓延時間', 's', 0, 60, 0.5, 10),
      parameter('fadeInSeconds', '每顆淡入', 's', 0, 20, 0.25, 3),
      parameter('repeat', '循環', '0 亮完保持／1 循環', 0, 1, 1, 0),
      parameter('holdSeconds', '全亮停留', 's', 0, 60, 0.5, 5),
      parameter('fadeOutSeconds', '淡出時間', 's', 0, 20, 0.25, 3),
      parameter('endDarkSeconds', '全暗時間', 's', 0, 20, 0.25, 1),
    ],
  },
  {
    id: 'pole-rise', label: '每根柱子由下往上',
    description: '每根柱子上的球燈由下往上逐顆快速亮起、再慢慢淡出。柱子之間可以錯開（柱子錯開 0 = 全部一起），順序依場地配置的柱子順序或每輪隨機。每輪時間到就重新開始；柱子多、錯開大時要把每輪時間加長，最後幾根才來得及亮。球燈歸屬最近的柱子；沒有場地配置時每顆自成一根（依編號）。',
    parameters: [
      parameter('stepSeconds', '每顆間隔', 's', 0, 5, 0.05, 0.2),
      parameter('attackSeconds', '亮起時間', 's', 0, 2, 0.05, 0.1),
      parameter('decaySeconds', '淡出時間', 's', 0.1, 20, 0.1, 2.5),
      parameter('staggerSeconds', '柱子錯開', 's', 0, 10, 0.05, 0.3),
      parameter('order', '錯開順序', '0 依柱子順序／1 隨機', 0, 1, 1, 0),
      parameter('periodSeconds', '每輪時間', 's', 1, 120, 0.5, 8),
    ],
  },
  {
    id: 'base-wave', label: '基本亮度＋正弦波',
    description: '每顆先亮基本亮度，再疊一道沿高度／x／y／編號前進的正弦波，把同一個顏色加亮。波動幅度 0 = 只有基本亮度；1 = 波峰到全亮。',
    parameters: [
      parameter('baseLevel', '基本亮度', '', 0, 1, 0.05, 0.3),
      parameter('depth', '波動幅度', '', 0, 1, 0.05, 0.7),
      parameter('speedHz', '頻率', 'Hz', 0.05, 4, 0.05, 0.3),
      parameter('waveCount', '波數', '', 0.5, 6, 0.5, 1),
      parameter('waveAxis', '前進方向', '0 高度／1 x／2 y／3 編號', 0, 3, 1, 0),
      direction,
    ],
  },
  {
    id: 'horizontal-flow', label: '水平流動',
    description: '顏色在水平方向流動：漸層的顏色沿俯視方向（0° = 沿長牆、90° = 往後牆）首尾相接地流過所有燈，不看漸層方向；亮度起伏讓一道道亮帶跟著一起流，純色時就只看得到亮帶在流。加速倍數大於 1 時，從效果（或段落）開始在加速時間內由流速平順加快到流速 × 加速倍數，之後維持。方向以場地長、寬各自 0–1 計。',
    parameters: [
      parameter('speedHz', '流速', '圈/秒', 0.02, 2, 0.02, 0.2),
      parameter('waveCount', '色帶數', '', 0.5, 6, 0.5, 1),
      parameter('directionDegrees', '流動方向', '°', 0, 360, 5, 0),
      parameter('depth', '亮度起伏', '', 0, 1, 0.05, 0.5),
      parameter('acceleration', '加速倍數', '倍（1 = 不加速）', 1, 10, 0.25, 1),
      parameter('rampSeconds', '加速時間', 's', 0.5, 120, 0.5, 10),
    ],
  },
  {
    id: 'wind-flow', label: '風流（風力原型）', description: WIND_FLOW_FIXTURE_DESCRIPTION,
    // 每顆只有一個顏色，亮點比例預設比轉燈高。 / One colour per fixture, so a higher default sparkle density.
    parameters: windFlowParameters(
      { key: 'heightSpan', label: '高度錯開', unit: '（0 = 不看高度）', min: 0, max: 1, step: 0.05, defaultValue: 0 }, 0.5,
    ),
  },
]

/** 雨天：每顆各自隨機相位閃亮後衰減。 / Rain: each fixture flashes at its own random phase, then decays. */
export const FIXTURE_RAIN = { hz: 0.7, flash: 0.25, floor: 0.2 } as const
/** 多雲：依 x 錯開，最左到最右相差幾個 cloud_step。 / Cloudy: x spread in cloud steps. */
export const CLOUD_SPREAD_STEPS = 8

const BLACK: RgbColor = { r: 0, g: 0, b: 0 }

export function getFixtureManifest(effectId: FixtureEffectId): FixtureManifestEntry {
  const entry = FIXTURE_MANIFEST.find((item) => item.id === effectId)
  if (!entry) throw new Error(`unknown fixture effect ${effectId}`)
  return entry
}

export function createDefaultFixturePreset(effectId: FixtureEffectId): FixturePreset {
  return {
    schemaVersion: FIXTURE_SCHEMA_VERSION,
    effectId,
    parameters: Object.fromEntries(getFixtureManifest(effectId).parameters.map((item) => [item.key, item.defaultValue])),
    color: effectId === 'wind-flow'
      ? { mode: 'gradient', axis: 'x', colors: WIND_FLOW_COLORS.map((color) => ({ ...color })) }
      : { mode: 'solid', color: { r: 1, g: 1, b: 1 } },
  }
}

/** 風流的顏色：依風速在漸層上取；亮點帶內偏白（同轉燈）。 / Wind colour from the gradient, whiter inside the sparkle band. */
function windFlowColor(colors: readonly RgbColor[], s: { colorPosition: number; mix: number }): RgbColor {
  const base = sampleGradient(colors, s.colorPosition, false)
  if (s.mix <= 0) return base
  const amount = 0.5 * s.mix
  return { r: base.r + (1 - base.r) * amount, g: base.g + (1 - base.g) * amount, b: base.b + (1 - base.b) * amount }
}

/** 風流：每顆在場地中的平面位置取樣。 / wind-flow: sampled at the fixture's plan position. */
let lastWindContext: FixtureContext | null = null
let lastWindPreset: FixturePreset | null = null
let lastWindSample: ReturnType<typeof sampleWindBand> | null = null

function windFlowSample(preset: FixturePreset, context: FixtureContext) {
  // 亮度與顏色各問一次（同一個 context 物件）：第二次用上一次的結果。 / Brightness then colour: reuse the first.
  if (lastWindContext === context && lastWindPreset === preset && lastWindSample !== null) return lastWindSample
  lastWindContext = context
  lastWindPreset = preset
  lastWindSample = computeWindFlowSample(preset, context)
  return lastWindSample
}

function computeWindFlowSample(preset: FixturePreset, context: FixtureContext) {
  const p = preset.parameters
  // 較高的燈先亮（同轉燈由上往下）；亮點的隨機依編號，和轉燈的 LED 錯開。 / Higher fixtures lead; sparkles seeded by the index.
  return sampleWindBand(context.controllerFrame, context.controllerFps, p, context.normalizedX, context.normalizedY,
    (p.heightSpan ?? 0) * (1 - context.normalizedHeight), 900000 + context.fixtureIndex)
}

function frames(seconds: number, fps: number): number {
  return Math.max(1, Math.round(seconds * fps))
}

/** 可為 0 的整數影格。 / Whole frames, zero allowed. */
function framesOrZero(seconds: number, fps: number): number {
  return Math.max(0, Math.round(seconds * fps))
}

/** 由低往高點亮：各段影格；循環時一輪 = 蔓延 + 淡入 + 停留 + 淡出 + 全暗。 / fixture-rise stages in frames. */
export function fixtureRiseTiming(p: Readonly<Record<string, number>>, fps: number) {
  const rise = framesOrZero(p.riseSeconds, fps)
  const fadeIn = framesOrZero(p.fadeInSeconds, fps)
  const fadeStart = rise + fadeIn + framesOrZero(p.holdSeconds, fps)
  const fadeOut = framesOrZero(p.fadeOutSeconds, fps)
  return { rise, fadeIn, fadeStart, fadeOut, cycle: Math.max(1, fadeStart + fadeOut + framesOrZero(p.endDarkSeconds, fps)) }
}

/** 煙火蔓延：(蔓延, 每顆持續, 全暗, 一輪) 影格。 / fixture-firework: (spread, burst, gap, cycle) frames. */
export function fixtureFireworkTiming(p: Readonly<Record<string, number>>, fps: number) {
  const spread = framesOrZero(p.spreadSeconds, fps)
  const burst = frames(p.burstSeconds, fps)
  const gap = framesOrZero(p.gapSeconds, fps)
  return { spread, burst, gap, cycle: spread + burst + gap }
}

function cycle(value: number): number {
  return value - Math.floor(value)
}

function smoothstep(value: number): number {
  const t = clamp01(value)
  return t * t * (3 - 2 * t)
}

function mod(value: number, divisor: number): number {
  return ((value % divisor) + divisor) % divisor
}

/** Frames for one natural cycle, or null when the effect has no boundary. */
export function getFixtureCompletion(preset: FixturePreset, fps: number, fixtureCount: number): number | null {
  const p = preset.parameters
  switch (preset.effectId) {
    case 'fixture-breathing':
      return Math.max(1, Math.ceil(fps / p.breatheHz))
    case 'height-sweep':
    case 'space-wave':
      return Math.max(1, Math.ceil(fps / p.speedHz))
    case 'fixture-chase':
      return frames(p.stepSeconds, fps) * Math.max(1, fixtureCount)
    case 'fixture-firework':
      return fixtureFireworkTiming(p, fps).cycle
    case 'fixture-rise':
      return p.repeat === 1 ? fixtureRiseTiming(p, fps).cycle : null
    case 'pole-rise':
      return frames(p.periodSeconds, fps)
    case 'base-wave':
      return Math.max(1, Math.ceil(fps / p.speedHz))
    case 'horizontal-flow':
      // 加速時以一次加速為一輪。 / With acceleration, one ramp is one round.
      return p.acceleration > 1 ? frames(p.rampSeconds, fps) : Math.max(1, Math.ceil(fps / p.speedHz))
    default:
      return null
  }
}

export function fixtureBrightness(preset: FixturePreset, context: FixtureContext): number {
  const p = preset.parameters
  const frame = context.controllerFrame
  const time = context.controllerFps > 0 ? frame / context.controllerFps : 0
  switch (preset.effectId) {
    case 'off':
      return 0
    case 'fixture-solid':
      return 1
    case 'fixture-breathing':
      return p.minLevel + (1 - p.minLevel) * (0.5 - 0.5 * Math.cos(2 * Math.PI * p.breatheHz * time))
    case 'height-sweep': {
      const position = p.direction === 0 ? context.normalizedHeight : 1 - context.normalizedHeight
      const half = p.width / 2
      const centre = -half + cycle(p.speedHz * time) * (1 + p.width)
      return smoothstep(1 - Math.abs(position - centre) / half)
    }
    case 'space-wave': {
      const position = p.direction === 0 ? context.normalizedX : 1 - context.normalizedX
      return 0.5 + 0.5 * Math.cos(2 * Math.PI * (p.waveCount * position - p.speedHz * time))
    }
    case 'fixture-twinkle': {
      const hold = frames(p.holdSeconds, context.controllerFps)
      const local = frame + Math.floor(hash01(0, context.fixtureIndex, 7) * hold)
      const cell = Math.floor(local / hold)
      const within = local - cell * hold
      if (hash01(cell, context.fixtureIndex, 11) >= p.density) return 0
      return Math.sin(Math.PI * (within + 0.5) / hold)
    }
    case 'fixture-chase': {
      const count = Math.max(1, context.fixtureCount)
      const head = Math.floor(frame / frames(p.stepSeconds, context.controllerFps)) % count
      const order = p.direction === 0 ? context.fixtureIndex : count - 1 - context.fixtureIndex
      const behind = mod(head - order, count)
      const tail = Math.trunc(p.tailCount)
      return behind <= tail ? 1 - behind / (tail + 1) : 0
    }
    case 'wind-flow': {
      const s = windFlowSample(preset, context)
      return s.band * (1 - s.mix) + s.sparkle * s.mix
    }
    case 'horizontal-flow': {
      // 亮帶跟著顏色一起流：最暗 1 - depth。 / Bright bands flow with the colours; the darkest is 1 - depth.
      const phase = horizontalFlowPhase(p, context)
      return 1 - p.depth * (0.5 - 0.5 * Math.cos(2 * Math.PI * phase))
    }
    case 'base-wave': {
      // 基本亮度＋正弦波：波峰在 base + (1 - base) × depth。 / Base plus a sine: the crest reaches base + (1 - base) × depth.
      const axis = (['height', 'x', 'y', 'index'] as const)[Math.min(3, Math.max(0, Math.round(p.waveAxis)))]
      const along = gradientPosition(axis, context)
      const position = p.direction === 0 ? along : 1 - along
      const wave = 0.5 + 0.5 * Math.cos(2 * Math.PI * (p.waveCount * position - p.speedHz * time))
      return p.baseLevel + (1 - p.baseLevel) * p.depth * wave
    }
    case 'pole-rise': {
      const period = frames(p.periodSeconds, context.controllerFps)
      const round = Math.floor(Math.max(0, frame) / period)
      const local = Math.max(0, frame) - round * period
      const rank = context.poleRank ?? context.fixtureIndex
      const count = context.poleCount ?? context.fixtureCount
      // 錯開順序：依柱子順序，或每輪以固定種子隨機排列。 / Order by pole, or a fixed-seed shuffle per round.
      let order = rank
      if (p.order === 1) {
        const mine = hash01(round, rank, 43)
        order = 0
        for (let other = 0; other < count; other += 1) {
          const theirs = hash01(round, other, 43)
          if (theirs < mine || (theirs === mine && other < rank)) order += 1
        }
      }
      const start = order * framesOrZero(p.staggerSeconds, context.controllerFps)
        + (context.poleStep ?? 0) * framesOrZero(p.stepSeconds, context.controllerFps)
      const elapsed = local - start
      if (elapsed < 0) return 0
      // 快速亮起、慢慢淡出（平方曲線）。 / Fast attack, slow quadratic decay.
      const attack = framesOrZero(p.attackSeconds, context.controllerFps)
      if (elapsed < attack) return (elapsed + 1) / attack
      const remaining = Math.max(0, 1 - (elapsed - attack + 1) / frames(p.decaySeconds, context.controllerFps))
      return remaining * remaining
    }
    case 'fixture-rise': {
      // 依高度晚開始：最低一顆先亮，最高一顆晚 rise 影格；淡入用 smoothstep，比線性柔和。
      // Delay by height: the lowest first, the highest rise frames later; the fade-in uses smoothstep, softer than linear.
      const { rise, fadeIn, fadeStart, fadeOut, cycle: round } = fixtureRiseTiming(p, context.controllerFps)
      const repeat = p.repeat === 1
      const local = repeat ? frame - Math.floor(frame / round) * round : Math.max(0, frame)
      const delay = Math.round(context.normalizedHeight * rise)
      if (local < delay) return 0
      const lit = fadeIn === 0 ? 1 : smoothstep((local - delay + 1) / fadeIn)
      if (!repeat || local < fadeStart) return lit
      return fadeOut === 0 ? 0 : lit * Math.max(0, 1 - (local - fadeStart + 1) / fadeOut)
    }
    case 'fixture-firework': {
      // 依高度晚開始：最低一顆先炸開，最高一顆晚 spread 影格。 / Delay by height: lowest first, highest spread frames later.
      const { spread, burst, cycle: round } = fixtureFireworkTiming(p, context.controllerFps)
      const local = frame - Math.floor(frame / round) * round - Math.round(context.normalizedHeight * spread)
      if (local < 0 || local >= burst) return 0
      // 先全亮（點燃），之後隨機閃爍並以平方漸暗。 / Fully lit when it ignites, then random flicker fading quadratically.
      const flicker = Math.max(1, Math.round(p.flickerFrames))
      if (local < flicker) return 1
      const on = hash01(Math.floor(frame / flicker), context.fixtureIndex, 31) < p.density
      const remaining = 1 - local / burst
      return on ? remaining * remaining : 0
    }
  }
}

function rain(context: FixtureContext): number {
  const time = context.controllerFps > 0 ? context.controllerFrame / context.controllerFps : 0
  const pulse = clamp01(1 - cycle(FIXTURE_RAIN.hz * time + hash01(0, context.fixtureIndex, 23)) / FIXTURE_RAIN.flash)
  return FIXTURE_RAIN.floor + (1 - FIXTURE_RAIN.floor) * pulse
}

/**
 * 流過的圈數：速度在 ramp 秒內由 v0 線性加到 v1（= v0 × 加速倍數），之後維持 v1；圈數是速度的積分，所以不會跳動。
 * Turns travelled: the speed rises linearly from v0 to v1 (= v0 × acceleration) over the ramp, then stays at v1; the
 * turns are its integral, so the flow never jumps.
 */
export function flowDistance(p: Readonly<Record<string, number>>, time: number): number {
  const start = p.speedHz
  const end = p.speedHz * (p.acceleration ?? 1)
  const ramp = p.rampSeconds ?? 10
  if (end === start) return start * time
  if (time <= ramp) return start * time + ((end - start) * time * time) / (2 * ramp)
  return start * ramp + ((end - start) * ramp) / 2 + end * (time - ramp)
}

/** 水平流動的相位（圈）：沿俯視方向的位置 × 色帶數 − 流過的圈數。 / Flow phase in turns: distance along the direction × bands − turns travelled. */
export function horizontalFlowPhase(p: Readonly<Record<string, number>>, context: FixtureContext): number {
  const time = context.controllerFps > 0 ? context.controllerFrame / context.controllerFps : 0
  const angle = (p.directionDegrees * Math.PI) / 180
  const along = context.normalizedX * Math.cos(angle) + context.normalizedY * Math.sin(angle)
  return p.waveCount * along - flowDistance(p, time)
}

function gradientPosition(axis: FixtureAxis, context: FixtureContext): number {
  if (axis === 'height') return context.normalizedHeight
  if (axis === 'x') return context.normalizedX
  if (axis === 'y') return context.normalizedY
  return context.fixtureCount <= 1 ? 0.5 : context.fixtureIndex / (context.fixtureCount - 1)
}

/** One fixture's RGB (0..1): colour × brightness × maxLevel; rain flashes every effect except off. */
export function sampleFixture(preset: FixturePreset, context: FixtureContext, raining = false): RgbColor {
  let brightness = fixtureBrightness(preset, context)
  if (raining && preset.effectId !== 'off') brightness *= rain(context)
  brightness *= preset.maxLevel ?? 1
  if (brightness <= 0) return BLACK
  const color = preset.color
  // 水平流動：漸層沿流動方向首尾相接地取色。 / horizontal-flow: the gradient loops along the flow.
  const base = color.mode === 'solid'
    ? color.color
    : preset.effectId === 'horizontal-flow'
      ? sampleGradient(color.colors, horizontalFlowPhase(preset.parameters, context), true)
      : preset.effectId === 'wind-flow'
        ? windFlowColor(color.colors, windFlowSample(preset, context))
        : sampleGradient(color.colors, gradientPosition(color.axis, context), false)
  return {
    r: clamp01(base.r * brightness),
    g: clamp01(base.g * brightness),
    b: clamp01(base.b * brightness),
  }
}

export type FixturePosition = { x?: number; y?: number; z?: number }

/**
 * (normalized height, x, y) per fixture from layout positions (cm). An axis is normalized within the group
 * when every fixture has it; otherwise it falls back to the fixture order.
 */
export function fixtureGeometry(positions: readonly (FixturePosition | null)[]): [number, number, number][] {
  const count = positions.length
  const fallback = positions.map((_, index) => (count <= 1 ? 0.5 : index / (count - 1)))
  const axes = (['z', 'x', 'y'] as const).map((key) => {
    if (count === 0 || !positions.every((position) => position?.[key] !== undefined)) return fallback
    const values = positions.map((position) => position![key]!)
    const low = Math.min(...values)
    const high = Math.max(...values)
    return values.map((value) => (high === low ? 0.5 : (value - low) / (high - low)))
  })
  return positions.map((_, index) => [axes[0][index], axes[1][index], axes[2][index]])
}

export type WeatherPalette = { mode: 'gradient'; colors: RgbColor[] }

/** Weather palette swaps the colours: gradients keep their own axis, solid colours spread by height. */
export function withFixtureWeatherPalette(preset: FixturePreset, palette: WeatherPalette | null): FixturePreset {
  if (palette === null || preset.color.weatherPalette === false) return preset
  const axis = preset.color.mode === 'gradient' ? preset.color.axis : 'height'
  return { ...preset, color: { mode: 'gradient', axis, colors: palette.colors.map((color) => ({ ...color })) } }
}

/** One group's colours for a frame counted from playback (or step) start; frameStep is the cloudy offset. */
export function renderFixtureFrame(
  preset: FixturePreset,
  geometry: readonly [number, number, number][],
  frame: number,
  options: {
    fps?: number; raining?: boolean; palette?: WeatherPalette | null; frameStep?: number; cycleFrames?: number | null
    /** 每顆的柱子（fixturePoles）。 / Each fixture's pole (fixturePoles). */
    poles?: readonly { rank: number; step: number }[] | null
  } = {},
): RgbColor[] {
  const fps = options.fps ?? 40
  const frameStep = options.frameStep ?? 0
  const effectFrame = options.cycleFrames ? frame % options.cycleFrames : frame
  const active = withFixtureWeatherPalette(preset, options.palette ?? null)
  const poles = options.poles ?? null
  const poleCount = poles ? poles.reduce((most, pole) => Math.max(most, pole.rank + 1), 0) : undefined
  // 風流不套用天氣（色盤、雨、多雲錯開）。 / wind-flow ignores the weather (palette, rain, cloud offsets).
  const wind = preset.effectId === 'wind-flow'
  return geometry.map(([height, x, y], index) => sampleFixture(wind ? preset : active, {
    ...(poles ? { poleRank: poles[index].rank, poleStep: poles[index].step, poleCount } : {}),
    controllerFrame: effectFrame + (wind ? 0 : Math.round(x * CLOUD_SPREAD_STEPS * frameStep)),
    controllerFps: fps,
    fixtureIndex: index,
    fixtureCount: geometry.length,
    normalizedHeight: height,
    normalizedX: x,
    normalizedY: y,
  }, !wind && (options.raining ?? false)))
}
