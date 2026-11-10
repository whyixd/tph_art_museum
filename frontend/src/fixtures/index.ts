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
    color: { mode: 'solid', color: { r: 1, g: 1, b: 1 } },
  }
}

function frames(seconds: number, fps: number): number {
  return Math.max(1, Math.round(seconds * fps))
}

/** 可為 0 的整數影格。 / Whole frames, zero allowed. */
function framesOrZero(seconds: number, fps: number): number {
  return Math.max(0, Math.round(seconds * fps))
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
  const base = color.mode === 'solid'
    ? color.color
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
  options: { fps?: number; raining?: boolean; palette?: WeatherPalette | null; frameStep?: number; cycleFrames?: number | null } = {},
): RgbColor[] {
  const fps = options.fps ?? 40
  const frameStep = options.frameStep ?? 0
  const effectFrame = options.cycleFrames ? frame % options.cycleFrames : frame
  const active = withFixtureWeatherPalette(preset, options.palette ?? null)
  return geometry.map(([height, x, y], index) => sampleFixture(active, {
    controllerFrame: effectFrame + Math.round(x * CLOUD_SPREAD_STEPS * frameStep),
    controllerFps: fps,
    fixtureIndex: index,
    fixtureCount: geometry.length,
    normalizedHeight: height,
    normalizedX: x,
    normalizedY: y,
  }, options.raining ?? false))
}
