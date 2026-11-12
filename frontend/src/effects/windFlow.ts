import { EFFECT_SCHEMA_VERSION, mixRgb, scaleRgb, type EffectModule, type EffectSampleContext, type RgbColor } from './contract'
import { sampleGradient } from './color'
import { sampleWindBand, windFlowParameters, WIND_FLOW_SPINNER_DESCRIPTION, type WindBandSample } from '../wind/windPreview'

const WHITE: RgbColor = { r: 1, g: 1, b: 1 }

/** 轉燈風流的參數（原型）。 / Spinner wind-flow parameters (prototype). */
const PARAMETERS = windFlowParameters(
  { key: 'sphereSpan', label: '球面上下錯開', unit: '（0 = 整顆一起）', min: 0, max: 1, step: 0.05, defaultValue: 0.25 }, 0.15,
)

export const WIND_FLOW_SPINNER_KEYS = PARAMETERS.map((parameter) => parameter.key)

// 模擬器對同一顆 LED 先問亮度、再問顏色（同一個 context 物件）：第二次直接用上一次的結果。
// The simulator asks one LED for its brightness and then its colour (the same context object): reuse the first result.
let lastContext: EffectSampleContext | null = null
let lastParameters: object | null = null
let lastSample: WindBandSample | null = null

function sample(context: EffectSampleContext, p: Readonly<Record<string, number>>): WindBandSample {
  if (lastContext === context && lastParameters === p && lastSample !== null) return lastSample
  lastContext = context
  lastParameters = p
  lastSample = computeSample(context, p)
  return lastSample
}

function computeSample(context: EffectSampleContext, p: Readonly<Record<string, number>>): WindBandSample {
  const order = context.unitCount <= 1 ? 0.5 : context.unitIndex / (context.unitCount - 1)
  // 球面上實際的高度（頂 0 → 底 1）：亮帶經過每台時由上往下掃過。 / Real height on the sphere: the band wipes down.
  const height = (1 - Math.cos(Math.PI * context.normalizedRadius)) / 2
  return sampleWindBand(
    context.controllerFrame, context.controllerFps, p,
    context.unitFlowPosition?.x ?? order, context.unitFlowPosition?.y ?? order,
    (p.sphereSpan ?? 0.25) * height, context.unitIndex * 1024 + context.ledIndex,
  )
}

function brightness(s: WindBandSample): number {
  return s.band * (1 - s.mix) + s.sparkle * s.mix
}

/**
 * 風流（原型）：柔和的亮帶沿風向流過全場，風越大流得越快；風速達到門檻時，一道隨機亮點帶沿風向週期性掃過，
 * 經過的轉燈變成隨機亮點，離開後恢復亮帶。顏色依風速在漸層上取（亮點是同色偏白）。
 * wind-flow prototype: a soft band travels across the site along the wind, faster in stronger wind; at or above the
 * threshold a band of random sparkles also sweeps along the wind, and spinners return to the soft band after it.
 */
export const windFlow: EffectModule = {
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'wind-flow',
    label: '風流（風力原型）',
    description: WIND_FLOW_SPINNER_DESCRIPTION,
    parameters: PARAMETERS.map((parameter) => ({ ...parameter })),
  },
  sample(context, parameters) {
    return scaleRgb(WHITE, brightness(sample(context, parameters)))
  },
  brightness(context, _mode, parameters) {
    return brightness(sample(context, parameters ?? {}))
  },
  gradientColor(context, parameters, colors) {
    const s = sample(context, parameters)
    const sineColor = scaleRgb(sampleGradient(colors, s.colorPosition, false), s.band)
    if (s.mix <= 0) return sineColor
    // 亮點用同一個風速顏色的白一點版本，在 sine 上看得出來。 / Sparkles use a whiter tint of the wind colour.
    const sparkleColor = scaleRgb(mixRgb(sampleGradient(colors, s.colorPosition, false), WHITE, 0.5), s.sparkle)
    return mixRgb(sineColor, sparkleColor, s.mix)
  },
  ignoresWeatherTiming: true,
}
