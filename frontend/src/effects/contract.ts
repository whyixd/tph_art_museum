export const EFFECT_SCHEMA_VERSION = 2 as const

export type EffectId =
  | 'spectrum-cycle'
  | 'center-out-sine'
  | 'moving-rings'
  | 'sequential-center-fill'
  | 'periodic-leds'
  | 'random-leds'
  | 'world-upper-half'
  | 'reverse-tide'
  | 'wide-rings'
  | 'comet-tail'
  | 'stardust-cloud'
  | 'color-breathing'
  | 'sector-smear'
  | 'twenty-beat'
  | 'sine-swell'
  | 'random-fill'
  | 'full-on'
  | 'unit-chase'
  | 'unit-random'
  | 'roulette-ring'
  | 'two-tone-trail'
  | 'firework-rise'
  | 'pillar-fill'
  | 'color-alternate'
  | 'wave-split'
  | 'wind-flow'

export type RgbColor = {
  r: number
  g: number
  b: number
}

/** 'unit' gives each unit one solid color sampled across the units. */
export type GradientAxis = 'strip' | 'radius' | 'unit'

/**
 * Switches the gradient to another axis partway through: after atSeconds (counted from the effect start, or the
 * sequence step start) it blends to `to` over blendSeconds. repeat alternates back and forth, holding each axis
 * for atSeconds.
 */
export type AxisChange = {
  to: GradientAxis
  atSeconds: number
  blendSeconds: number
  repeat: boolean
}

/** weatherPalette: false keeps these colors while weather is active; omitted means the weather palette replaces them. */
export type EffectColor =
  | { mode: 'solid'; color: RgbColor; weatherPalette?: false }
  | { mode: 'gradient'; colors: RgbColor[]; axis: GradientAxis; axisChange?: AxisChange; weatherPalette?: false }
  /**
   * 多色：每個燈點（或每台）顯示清單裡的一個顏色，照順序或隨機；每過 stepSeconds 換一次。轉燈的「燈點」是同一圈
   * 的兩顆 LED（燈條兩半對稱），轉起來是一圈一色。
   * multi: each point (or each unit) shows one colour of the list, in order or at random, changing every
   * stepSeconds. A spinner "point" is the two LEDs of one ring (the strip halves mirror), so a ring has one colour.
   */
  | {
    mode: 'multi'; colors: RgbColor[]; order: 'sequence' | 'random'; per: 'point' | 'unit'
    stepSeconds: number; fadeSeconds: number; weatherPalette?: false
  }

export type EffectParameterDefinition = {
  key: string
  label: string
  unit: string
  min: number
  max: number
  step: number
  defaultValue: number
}

export type EffectManifestEntry = {
  schemaVersion: typeof EFFECT_SCHEMA_VERSION
  id: EffectId
  label: string
  description: string
  parameters: readonly EffectParameterDefinition[]
}

export type UnitVariationDistribution = 'linear' | 'skip' | 'random'

/** Gives each unit its own fixed value of one parameter, spread between from and to. */
export type UnitVariation = {
  parameter: string
  from: number
  to: number
  distribution: UnitVariationDistribution
}

export type EffectPreset = {
  schemaVersion: typeof EFFECT_SCHEMA_VERSION
  effectId: EffectId
  parameters: Record<string, number>
  color: EffectColor
  unitVariation?: UnitVariation
}

export type EffectSampleContext = {
  controllerFrame: number
  globalControllerFrame: number
  controllerFps: number
  ledIndex: number
  ledCount: number
  unitIndex: number
  unitCount: number
  normalizedStripPosition: number
  normalizedRadius: number
  unitRelativeY: number
  unitPhaseProjection: number
  /**
   * 依柱子的順序（0 起；pillarRanks）：柱子依場地配置順序、每根柱子上由低到高。沒有時用 unitIndex。
   * Order by pillar (from 0; pillarRanks): pillars in layout order, low to high on each. Falls back to unitIndex.
   */
  unitRank?: number
  /**
   * 轉燈中心在場地中的座標（cm，場地配置；缺的軸為 undefined）。 / The spinner centre in the room (cm, from the layout).
   */
  unitPosition?: { x?: number; y?: number; z?: number }
  /**
   * 轉燈在全部轉燈範圍內的平面位置（長、寬各自 0–1，同球燈 fixtureGeometry；缺座標時依編號）。中心擴散的水平流動用。
   * The spinner's plan position within all spinners (length and width each 0–1, as fixtureGeometry; by unit order
   * when positions are missing). Used by center-out-sine's horizontal flow.
   */
  unitFlowPosition?: { x: number; y: number }
}

export type EffectModule = {
  manifest: EffectManifestEntry
  completion?: {
    label: string
    frames: (parameters: Readonly<Record<string, number>>, fps: number, unitCount: number) => number
  }
  sample: (
    context: EffectSampleContext,
    parameters: Readonly<Record<string, number>>,
  ) => RgbColor
  brightness?: (
    context: EffectSampleContext,
    colorMode: EffectColor['mode'],
    parameters?: Readonly<Record<string, number>>,
  ) => number
  gradientPosition?: (
    context: EffectSampleContext,
    axis: 'strip' | 'radius',
    colorCount: number,
    parameters?: Readonly<Record<string, number>>,
  ) => number
  /**
   * 漸層模式時直接給出最終顏色（取代「亮度 × 漸層取色」，不看方向與中途切換）；回傳 null 時照一般的漸層方向。
   * In gradient mode, the final colour directly (replaces brightness × gradient; ignores the axis and axis change);
   * null falls back to the usual axis.
   */
  gradientColor?: (
    context: EffectSampleContext,
    parameters: Readonly<Record<string, number>>,
    colors: readonly RgbColor[],
  ) => RgbColor | null
  /** Overrides the 'unit' axis position (default unitIndex / (unitCount - 1)). */
  unitGradientPosition?: (context: EffectSampleContext, parameters?: Readonly<Record<string, number>>) => number
  loopGradient?: boolean
  /**
   * 雨天亮度：效果趨近線條的版本，base 是晴天亮度；沒有時雨天不變。
   * Rain brightness, the effect's line-like version; base is the dry brightness. Without it rain changes nothing.
   */
  rain?: (context: EffectSampleContext, parameters: Readonly<Record<string, number>>, base: () => number) => number
  /** 不套用天氣的多雲錯開（九台共用時間表）。 / Skips weather cloud offsets (one shared schedule). */
  ignoresWeatherTiming?: boolean
}

export function createEffectContext(
  controllerFrame: number,
  controllerFps: number,
  ledIndex: number,
  ledCount: number,
  unitIndex = 0,
  unitCount = 1,
  globalControllerFrame = controllerFrame,
  unitRelativeY = 0,
  unitPhaseProjection = 1,
): EffectSampleContext {
  const normalizedStripPosition =
    ledCount <= 1 ? 0.5 : ledIndex / (ledCount - 1)

  return {
    controllerFrame,
    globalControllerFrame,
    controllerFps,
    ledIndex,
    ledCount,
    unitIndex,
    unitCount,
    normalizedStripPosition,
    normalizedRadius: Math.abs(normalizedStripPosition * 2 - 1),
    unitRelativeY,
    unitPhaseProjection,
  }
}

export function effectTimeSeconds(context: EffectSampleContext): number {
  if (context.controllerFps <= 0) return 0
  return context.controllerFrame / context.controllerFps
}

export function globalEffectTimeSeconds(context: EffectSampleContext): number {
  if (context.controllerFps <= 0) return 0
  return context.globalControllerFrame / context.controllerFps
}

export function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value))
}

export function mixRgb(
  from: RgbColor,
  to: RgbColor,
  amount: number,
): RgbColor {
  const t = clamp01(amount)
  return {
    r: from.r + (to.r - from.r) * t,
    g: from.g + (to.g - from.g) * t,
    b: from.b + (to.b - from.b) * t,
  }
}

export function scaleRgb(color: RgbColor, brightness: number): RgbColor {
  return {
    r: clamp01(color.r * brightness),
    g: clamp01(color.g * brightness),
    b: clamp01(color.b * brightness),
  }
}
