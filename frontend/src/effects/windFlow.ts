import { clamp01, EFFECT_SCHEMA_VERSION, mixRgb, scaleRgb, type EffectModule, type EffectSampleContext, type RgbColor } from './contract'
import { sampleGradient } from './color'
import { hash01 } from './rouletteRing'
import { advanceWind, alongWind, getWindPreview, windKey, windStrength } from '../wind/windPreview'

const WHITE: RgbColor = { r: 1, g: 1, b: 1 }

/** 轉燈風流的參數（原型）。 / Spinner wind-flow parameters (prototype). */
const PARAMETERS = [
  { key: 'speedHz', label: '基準流速', unit: '圈/秒', min: 0.02, max: 1, step: 0.02, defaultValue: 0.15 },
  { key: 'minRate', label: '無風倍率', unit: '倍', min: 0, max: 2, step: 0.1, defaultValue: 0.6 },
  { key: 'maxRate', label: '強風倍率', unit: '倍', min: 1, max: 8, step: 0.25, defaultValue: 3 },
  { key: 'calmMs', label: '無風門檻', unit: 'm/s', min: 0, max: 5, step: 0.5, defaultValue: 1 },
  { key: 'fullMs', label: '最大風速', unit: 'm/s', min: 3, max: 25, step: 0.5, defaultValue: 12 },
  { key: 'curve', label: '風速曲線', unit: '（>1 小風變化小）', min: 0.5, max: 3, step: 0.1, defaultValue: 1 },
  { key: 'waveCount', label: '場地亮帶數', unit: '', min: 0.5, max: 4, step: 0.5, defaultValue: 1 },
  { key: 'sphereSpan', label: '球面上下錯開', unit: '（0 = 整顆一起）', min: 0, max: 1, step: 0.05, defaultValue: 0.25 },
  { key: 'bandSoftness', label: '亮帶寬度', unit: '（越大越柔和）', min: 0.1, max: 1, step: 0.05, defaultValue: 0.5 },
  { key: 'minLevel', label: '最低亮度', unit: '', min: 0, max: 0.9, step: 0.05, defaultValue: 0.2 },
  { key: 'colorSpread', label: '波峰色偏', unit: '', min: 0, max: 0.5, step: 0.05, defaultValue: 0.15 },
  { key: 'sparkleBelowMs', label: '亮點門檻 X', unit: 'm/s（達到時亮點帶掃過）', min: 0, max: 15, step: 0.5, defaultValue: 3 },
  { key: 'sweepSeconds', label: '亮點帶掃過時間（門檻風速）', unit: 's', min: 0.3, max: 30, step: 0.1, defaultValue: 2 },
  { key: 'sweepFastSeconds', label: '亮點帶掃過時間（最大風速）', unit: 's', min: 0.2, max: 30, step: 0.1, defaultValue: 0.6 },
  { key: 'gapSeconds', label: '亮點帶間隔', unit: 's', min: 0, max: 60, step: 0.5, defaultValue: 6 },
  { key: 'bandWidth', label: '亮點帶寬度', unit: '（場地 0–1）', min: 0.05, max: 1, step: 0.05, defaultValue: 0.3 },
  { key: 'density', label: '亮點比例', unit: '', min: 0.02, max: 1, step: 0.02, defaultValue: 0.15 },
  { key: 'twinkleFrames', label: '亮點閃爍長度', unit: '影格', min: 1, max: 40, step: 1, defaultValue: 6 },
] as const

export const WIND_FLOW_SPINNER_KEYS = PARAMETERS.map((parameter) => parameter.key)

type WindSpinnerSample = {
  /** sine 波的亮度。 / Sine brightness. */
  sine: number
  /** 隨機亮點的亮度。 / Sparkle brightness. */
  sparkle: number
  /** 0 = 純 sine、1 = 純亮點。 / 0: sine only, 1: sparkles only. */
  mix: number
  colorPosition: number
}

function sample(context: EffectSampleContext, p: Readonly<Record<string, number>>): WindSpinnerSample {
  const wind = getWindPreview()
  const { turns, sweep, sweepDeg, played } = advanceWind(windKey('spinner', p), context.controllerFrame, context.controllerFps, p)
  const order = context.unitCount <= 1 ? 0.5 : context.unitIndex / (context.unitCount - 1)
  const x = context.unitFlowPosition?.x ?? order
  const y = context.unitFlowPosition?.y ?? order
  // 柔和的亮帶沿風吹往的方向流過全場：相位 = 轉燈沿風向的位置 × 亮帶數 − 流過的圈數；球面上由上到下再錯開
  // sphereSpan，所以亮帶經過每台時是由上往下掃過，不是整顆一起亮。
  // A soft band travels across the site toward where the wind blows: phase = the spinner's place along the wind ×
  // bands − turns; the sphere adds sphereSpan from top to bottom, so the band wipes down each spinner as it passes.
  const height = (1 - Math.cos(Math.PI * context.normalizedRadius)) / 2
  const cycle = (p.waveCount ?? 1) * alongWind(x, y, wind.flowDeg) + (p.sphereSpan ?? 0.25) * height - turns
  // 離亮帶中心越遠越暗（高斯形）。 / A Gaussian falloff from the band centre.
  const distance = Math.abs(cycle - Math.round(cycle)) * 2
  const softness = p.bandSoftness ?? 0.5
  const crest = Math.exp(-((distance / softness) ** 2) * 2)
  const minLevel = p.minLevel ?? 0.2
  const sine = minLevel + (1 - minLevel) * crest
  const colorPosition = clamp01(windStrength(wind.colorSpeedMs, p) + (p.colorSpread ?? 0.15) * (crest - 0.5))
  if (sweep === null) return { sine, sparkle: 0, mix: 0, colorPosition }
  // 亮點帶（風速達到門檻時）：前緣由最上風處前一個帶寬掃到最下風處後一個帶寬；帶內平順地由亮帶換成亮點。
  // 方向用這道亮點帶開始時鎖定的風向，途中風向改變也朝原方向掃到底。
  // Sparkle band (wind at or above the threshold): from one band width upwind of the site to one past the far end,
  // along the direction locked at its start; inside, the soft band fades to sparkles.
  const along = alongWind(x, y, sweepDeg)
  const width = p.bandWidth ?? 0.3
  const centre = -width / 2 + sweep * (1 + width)
  const inside = clamp01(1 - Math.abs(along - centre) / (width / 2))
  const mix = inside * inside * (3 - 2 * inside)
  if (mix <= 0) return { sine, sparkle: 0, mix: 0, colorPosition }
  // 每顆 LED 各自錯開的閃爍（依播放時間，不看影格）：每 twinkleFrames 一格，依比例亮起，格內以半個正弦亮起再熄。
  // Each LED twinkles on its own offset (by played time, not the frame): one cell per twinkleFrames, lit by density.
  const length = Math.max(1, Math.round(p.twinkleFrames ?? 6))
  const id = context.unitIndex * 1024 + context.ledIndex
  const local = Math.floor(played * 40) + Math.floor(hash01(0, id, 67) * length)
  const cell = Math.floor(local / length)
  const within = local - cell * length
  const sparkle = hash01(cell, id, 71) < (p.density ?? 0.15) ? Math.sin((Math.PI * (within + 0.5)) / length) : 0
  return { sine, sparkle, mix, colorPosition }
}

function brightness(s: WindSpinnerSample): number {
  return s.sine * (1 - s.mix) + s.sparkle * s.mix
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
    description: '【風力效果原型，只在前端預覽】柔和的亮帶沿風吹往的方向流過全場的轉燈，經過每台時由上往下掃過（「球面上下錯開」設 0 = 整顆一起亮），'
      + '風越大流得越快（基準流速 × 無風倍率 → 強風倍率）。風速達到「亮點門檻 X」時，另有一道隨機亮點帶沿風向'
      + '週期性掃過全場（風越大掃得越快：門檻風速時花「掃過時間（門檻風速）」、最大風速時花「掃過時間（最大風速）」；之後隔「間隔」再來一道），經過的轉燈變成隨機閃爍的亮點，離開後恢復亮帶；'
      + '每道開始時鎖定當下的風向，一定掃到底。低於門檻時只有亮帶。效果依實際時間進行，不受串接段落重複、重設時間影響。'
      + '漸層代表風速：最左邊 = 無風、最右邊 = 最大風速。風速、風向用「預覽風力」調整。',
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
    const sineColor = scaleRgb(sampleGradient(colors, s.colorPosition, false), s.sine)
    if (s.mix <= 0) return sineColor
    // 亮點用同一個風速顏色的白一點版本，在 sine 上看得出來。 / Sparkles use a whiter tint of the wind colour.
    const sparkleColor = scaleRgb(mixRgb(sampleGradient(colors, s.colorPosition, false), WHITE, 0.5), s.sparkle)
    return mixRgb(sineColor, sparkleColor, s.mix)
  },
  ignoresWeatherTiming: true,
}
