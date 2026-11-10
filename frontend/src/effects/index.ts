import { centerOutSine } from './centerOutSine'
import { colorAlternate } from './colorAlternate'
import { fireworkRise } from './fireworkRise'
import { fullOn } from './fullOn'
import { axisChangeWeight, defaultEffectColor, sampleGradient } from './color'
import {
  EFFECT_SCHEMA_VERSION,
  mixRgb,
  scaleRgb,
  type EffectId,
  type EffectModule,
  type EffectPreset,
  type EffectSampleContext,
  type GradientAxis,
  type RgbColor,
} from './contract'
import { movingRings } from './movingRings'
import { periodicLeds } from './periodicLeds'
import { pillarFill } from './pillarFill'
import { reverseTide, wideRings, cometTail, stardustCloud, colorBreathing, sectorSmear, twentyBeat } from './phaseFreeEffects'
import { randomFill } from './randomFill'
import { randomLeds } from './randomLeds'
import { rouletteRing } from './rouletteRing'
import { sequentialCenterFill } from './sequentialCenterFill'
import { sineSwell } from './sineSwell'
import { spectrumCycle } from './spectrumCycle'
import { unitChase } from './unitChase'
import { unitRandom } from './unitRandom'
import { twoToneTrail } from './twoToneTrail'
import { unitVariationValue } from './unitVariation'
import { waveSplit } from './waveSplit'
import { worldUpperHalf } from './worldUpperHalf'

const EFFECTS: Record<EffectId, EffectModule> = {
  'spectrum-cycle': spectrumCycle,
  'center-out-sine': centerOutSine,
  'moving-rings': movingRings,
  'sequential-center-fill': sequentialCenterFill,
  'periodic-leds': periodicLeds,
  'random-leds': randomLeds,
  'world-upper-half': worldUpperHalf,
  'reverse-tide': reverseTide,
  'wide-rings': wideRings,
  'comet-tail': cometTail,
  'stardust-cloud': stardustCloud,
  'color-breathing': colorBreathing,
  'sector-smear': sectorSmear,
  'twenty-beat': twentyBeat,
  'sine-swell': sineSwell,
  'random-fill': randomFill,
  'full-on': fullOn,
  'unit-chase': unitChase,
  'unit-random': unitRandom,
  'roulette-ring': rouletteRing,
  'two-tone-trail': twoToneTrail,
  'firework-rise': fireworkRise,
  'pillar-fill': pillarFill,
  'color-alternate': colorAlternate,
  'wave-split': waveSplit,
}

export const EFFECT_MANIFEST = Object.values(EFFECTS).map(
  (effect) => effect.manifest,
)

export function getEffectManifest(effectId: EffectId) {
  return EFFECTS[effectId].manifest
}

// 逐組填滿、逐台巡行、隨機單台與輪盤收束靠九台共用時間表決定輪到哪一台，各台參數不同會錯亂，所以不開放。
// sequential-center-fill, unit-chase, unit-random and roulette-ring share one schedule across units, so per-unit values would break them.
const FIXED_PARAMETERS: Partial<Record<EffectId, readonly string[]>> = {
  'sequential-center-fill': ['expansionSeconds', 'holdSeconds'],
  'unit-chase': ['stepSeconds', 'fadeSeconds', 'gapSeconds', 'holdSeconds', 'endDarkSeconds'],
  'unit-random': ['fadeInSeconds', 'holdSeconds', 'fadeOutSeconds', 'gapSeconds'],
  'roulette-ring': [
    'sweeps', 'startStepFrames', 'endStepSeconds', 'holdSeconds', 'dimSeconds',
    'spreadStepSeconds', 'ringHoldSeconds', 'glitchSeconds', 'glitchSpreadSeconds', 'flickerFrames', 'endDarkSeconds',
  ],
  'random-leds': ['durationMode'],
  'wave-split': ['levelMeters', 'amplitudeMeters', 'wavelengthMeters', 'speedMps', 'directionDegrees', 'chop', 'foamCm', 'softnessCm'],
  'pillar-fill': ['stepSeconds', 'acceleration', 'fadeInSeconds', 'holdSeconds', 'fadeOutSeconds', 'endDarkSeconds'],
  // 錯落順序靠九台共用的整輪長度。 / The stagger order relies on one shared round length.
  'random-fill': ['staggerSeconds'],
  // 水平流動要全部轉燈一起流，不開放各台不同。 / The horizontal flow moves all spinners together, so no per-unit values.
  'center-out-sine': [
    'horizontalFlow', 'flowSpeedHz', 'flowBands', 'flowDirectionDegrees', 'flowDepth', 'flowAcceleration', 'flowRampSeconds',
  ],
}

export function ignoresWeatherTiming(effectId: EffectId): boolean {
  return EFFECTS[effectId].ignoresWeatherTiming === true
}

/** Parameters that may differ per unit. */
export function getVariableParameters(effectId: EffectId) {
  const fixed = FIXED_PARAMETERS[effectId] ?? []
  return getEffectManifest(effectId).parameters.filter((parameter) => !fixed.includes(parameter.key))
}

const unitPresetCache = new WeakMap<EffectPreset, Map<string, EffectPreset>>()

/** The preset one unit plays: its own value of the varied parameter, without unitVariation. */
export function presetForUnit(preset: EffectPreset, unitIndex: number, unitCount: number): EffectPreset {
  const variation = preset.unitVariation
  if (!variation) return preset
  let cache = unitPresetCache.get(preset)
  if (!cache) {
    cache = new Map()
    unitPresetCache.set(preset, cache)
  }
  const key = `${unitIndex}/${unitCount}`
  const cached = cache.get(key)
  if (cached) return cached
  const { unitVariation: _, ...base } = preset
  const definition = getVariableParameters(preset.effectId).find((parameter) => parameter.key === variation.parameter)
  const result = definition
    ? { ...base, parameters: { ...base.parameters, [definition.key]: unitVariationValue(definition, variation, unitIndex, unitCount) } }
    : base
  cache.set(key, result)
  return result
}

// 各台週期不同時取最長的，讓每台至少播完指定次數。 / With per-unit cycles, the longest one lets every unit finish its count.
export function getEffectCompletion(preset: EffectPreset, fps: number, unitCount: number) {
  const completion = EFFECTS[preset.effectId].completion
  if (!completion) return null
  const units = preset.unitVariation
    ? Array.from({ length: Math.max(1, unitCount) }, (_, index) => presetForUnit(preset, index, unitCount))
    : [preset]
  return {
    label: completion.label,
    frames: Math.max(...units.map((unit) => Math.max(1, Math.ceil(completion.frames(unit.parameters, fps, unitCount))))),
  }
}

/**
 * 依每條燈條顆數夾住預設值（後端 validate_preset 的限制）：129 顆時 periodic-leds 的預設 12 亮／12 間隔放不下。
 * Clamp parameters to the strip length (backend validate_preset limits): with 129 LEDs the periodic-leds default
 * of 12 lit / 12 gap does not fit.
 */
export function fitParametersToLedCount(effectId: EffectId, parameters: Record<string, number>, ledCount: number): Record<string, number> {
  if (effectId === 'random-leds') return { ...parameters, litCount: Math.min(parameters.litCount, ledCount) }
  if (effectId !== 'periodic-leds') return parameters
  const litCount = Math.max(1, Math.min(parameters.litCount, ledCount))
  const darkGap = litCount > 1 ? Math.min(parameters.darkGap, Math.floor((ledCount - 1) / (litCount - 1)) - 1) : parameters.darkGap
  return { ...parameters, litCount, darkGap: Math.max(0, darkGap) }
}

/** Default preset; with `ledCount`, parameters are clamped to fit that strip length. */
export function createDefaultEffectPreset(effectId: EffectId, ledCount?: number): EffectPreset {
  const defaults = Object.fromEntries(
    getEffectManifest(effectId).parameters.map((parameter) => [
      parameter.key,
      parameter.defaultValue,
    ]),
  )
  const parameters = ledCount === undefined ? defaults : fitParametersToLedCount(effectId, defaults, ledCount)

  return {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    effectId,
    parameters,
    color: defaultEffectColor(effectId),
  }
}

export const DEFAULT_EFFECT_PRESET = createDefaultEffectPreset(
  'center-out-sine',
)

// elapsedFrame 只用於中途切換方向（預設為共同 frame）；raining 時改用效果的雨天線條版本。
// elapsedFrame only drives the axis change (default: shared frame); raining switches to the effect's rain lines.
export function sampleEffect(
  preset: EffectPreset,
  context: EffectSampleContext,
  elapsedFrame = context.globalControllerFrame,
  raining = false,
): RgbColor {
  const effect = EFFECTS[preset.effectId]
  const base = () => {
    const brightness = effect.brightness?.(context, preset.color.mode, preset.parameters)
    if (brightness !== undefined) return brightness
    const raw = effect.sample(context, preset.parameters)
    return Math.max(raw.r, raw.g, raw.b)
  }
  const brightness = raining && effect.rain ? effect.rain(context, preset.parameters, base) : base()
  if (preset.color.mode === 'solid') return scaleRgb(preset.color.color, brightness)
  const { colors, axis, axisChange } = preset.color
  if (effect.gradientColor && !(raining && effect.rain)) {
    const direct = effect.gradientColor(context, preset.parameters, colors)
    if (direct !== null) return direct
  }
  const color = (gradientAxis: GradientAxis) => gradientAxis === 'unit'
    ? sampleGradient(
      colors,
      effect.unitGradientPosition?.(context, preset.parameters)
        ?? (context.unitCount <= 1 ? 0 : context.unitIndex / (context.unitCount - 1)),
      false,
    )
    : sampleGradient(
      colors,
      effect.gradientPosition?.(context, gradientAxis, colors.length, preset.parameters)
        ?? (gradientAxis === 'radius' ? context.normalizedRadius : context.normalizedStripPosition),
      effect.loopGradient ?? false,
    )
  const weight = axisChange ? axisChangeWeight(axisChange, elapsedFrame, context.controllerFps) : 0
  const mixed = weight <= 0 ? color(axis)
    : weight >= 1 ? color(axisChange!.to)
    : mixRgb(color(axis), color(axisChange!.to), weight)
  return scaleRgb(mixed, brightness)
}

export type {
  AxisChange,
  EffectColor,
  EffectId,
  EffectPreset,
  EffectSampleContext,
  GradientAxis,
  RgbColor,
  UnitVariation,
  UnitVariationDistribution,
} from './contract'
export { axisChangeWeight, hexToRgb, rgbToHex, withWeatherPalette } from './color'
export {
  createEffectContext,
  EFFECT_SCHEMA_VERSION,
  globalEffectTimeSeconds,
} from './contract'
