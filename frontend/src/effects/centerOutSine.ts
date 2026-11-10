import {
  EFFECT_SCHEMA_VERSION,
  effectTimeSeconds,
  mixRgb,
  scaleRgb,
  type EffectModule,
  type EffectSampleContext,
} from './contract'
import { sampleGradient } from './color'
import { rainRing } from './rain'

const TAU = Math.PI * 2
const cycle = (value: number) => value - Math.floor(value)
const WAVE_COUNT = 1.5
const CENTER_COLOR = { r: 0.2, g: 0.92, b: 1 }
const EDGE_COLOR = { r: 0.66, g: 0.28, b: 1 }
/** 水平流動的預設值（同球燈「水平流動」）。 / Horizontal-flow defaults (as the ball effect). */
const FLOW = { speedHz: 0.2, bands: 1, directionDegrees: 0, depth: 0.5, acceleration: 1, rampSeconds: 10 }

export const flowEnabled = (parameters: Readonly<Record<string, number>>) => (parameters.horizontalFlow ?? 0) >= 0.5

/**
 * 流過的圈數（同球燈 flowDistance）：速度在加速時間內由流速線性加到流速 × 加速倍數，之後維持；圈數是速度的積分。
 * Turns travelled (as the ball flowDistance): the speed rises linearly from v0 to v0 × acceleration over the ramp,
 * then holds; the turns are its integral, so the flow never jumps.
 */
export function centerOutFlowDistance(parameters: Readonly<Record<string, number>>, time: number): number {
  const start = parameters.flowSpeedHz ?? FLOW.speedHz
  const end = start * (parameters.flowAcceleration ?? FLOW.acceleration)
  const ramp = parameters.flowRampSeconds ?? FLOW.rampSeconds
  if (end === start) return start * time
  if (time <= ramp) return start * time + ((end - start) * time * time) / (2 * ramp)
  return start * ramp + ((end - start) * ramp) / 2 + end * (time - ramp)
}

/**
 * 水平流動的相位（圈）：轉燈在場地中的位置（長、寬各自 0–1）沿俯視方向 × 色帶數 − 流過的圈數。
 * Flow phase in turns: the spinner's place in the room (length and width each 0–1) along the plan direction × bands
 * − turns travelled.
 */
export function centerOutFlowPhase(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number {
  const order = context.unitCount <= 1 ? 0.5 : context.unitIndex / (context.unitCount - 1)
  const x = context.unitFlowPosition?.x ?? order
  const y = context.unitFlowPosition?.y ?? order
  const angle = ((parameters.flowDirectionDegrees ?? FLOW.directionDegrees) * Math.PI) / 180
  const along = x * Math.cos(angle) + y * Math.sin(angle)
  return (parameters.flowBands ?? FLOW.bands) * along - centerOutFlowDistance(parameters, effectTimeSeconds(context))
}

/** 中心擴散的亮度；開啟水平流動時再乘上流動的亮帶（最暗 1 − 亮度起伏）。 / Brightness, times the flowing bands when on. */
function centerOutBrightness(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number {
  const speedHz = parameters.speedHz ?? 0.35
  const phase = TAU * (
    WAVE_COUNT * context.normalizedRadius -
    speedHz * effectTimeSeconds(context)
  )
  const sineBrightness = 0.5 + 0.5 * Math.cos(phase)
  // 波谷保留最低亮度，不會變成全暗。 / The trough keeps minLevel, never black.
  const minLevel = parameters.minLevel ?? 0.3
  const brightness = minLevel + sineBrightness * (1 - minLevel)
  if (!flowEnabled(parameters)) return brightness
  const depth = parameters.flowDepth ?? FLOW.depth
  return brightness * (1 - depth * (0.5 - 0.5 * Math.cos(TAU * centerOutFlowPhase(context, parameters))))
}

export const centerOutSine: EffectModule = {
  completion: {
    label: '波形完成一個週期（水平流動時：流過一圈或一次加速）',
    frames: (parameters, fps) => {
      if (!flowEnabled(parameters)) return fps / (parameters.speedHz ?? 0.35)
      // 同球燈水平流動：加速時以一次加速為一輪。 / As the ball flow: with acceleration, one ramp is one round.
      if ((parameters.flowAcceleration ?? FLOW.acceleration) > 1) {
        return Math.max(1, Math.round((parameters.flowRampSeconds ?? FLOW.rampSeconds) * fps))
      }
      return fps / (parameters.flowSpeedHz ?? FLOW.speedHz)
    },
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'center-out-sine',
    label: 'Center-out sine / 中心擴散',
    description: '平滑 sine 亮度波由圓心向外傳播。波谷不會全暗，最暗是「最低亮度」（設 0.04 為舊版效果）。'
      + '「水平流動」設為 1 時，顏色改成和球燈「水平流動」一樣在場地中水平流動：漸層的顏色沿俯視方向（0° = 沿長牆、90° = 往後牆）'
      + '首尾相接地流過所有轉燈（每台一色，不看漸層方向），亮度起伏讓亮帶跟著一起流（純色時只看得到亮帶），中心擴散的亮度波照常；'
      + '加速倍數大於 1 時，從效果（或段落）開始在加速時間內由流速平順加快到流速 × 加速倍數，之後維持。方向以轉燈在場地的長、寬各自 0–1 計。',
    parameters: [
      {
        key: 'speedHz',
        label: 'Wave speed',
        unit: 'Hz',
        min: 0.05,
        max: 2,
        step: 0.05,
        defaultValue: 0.35,
      },
      {
        key: 'minLevel',
        label: '最低亮度',
        unit: '',
        min: 0,
        max: 0.9,
        step: 0.05,
        defaultValue: 0.3,
      },
      { key: 'horizontalFlow', label: '水平流動', unit: '0 關／1 開', min: 0, max: 1, step: 1, defaultValue: 0 },
      { key: 'flowSpeedHz', label: '流速', unit: '圈/秒', min: 0.02, max: 2, step: 0.02, defaultValue: FLOW.speedHz },
      { key: 'flowBands', label: '色帶數', unit: '', min: 0.5, max: 6, step: 0.5, defaultValue: FLOW.bands },
      { key: 'flowDirectionDegrees', label: '流動方向', unit: '°', min: 0, max: 360, step: 5, defaultValue: FLOW.directionDegrees },
      { key: 'flowDepth', label: '亮度起伏', unit: '', min: 0, max: 1, step: 0.05, defaultValue: FLOW.depth },
      { key: 'flowAcceleration', label: '加速倍數', unit: '倍（1 = 不加速）', min: 1, max: 10, step: 0.25, defaultValue: FLOW.acceleration },
      { key: 'flowRampSeconds', label: '加速時間', unit: 's', min: 0.5, max: 120, step: 0.5, defaultValue: FLOW.rampSeconds },
    ],
  },

  sample(context, parameters) {
    const color = mixRgb(
      CENTER_COLOR,
      EDGE_COLOR,
      context.normalizedRadius,
    )

    return scaleRgb(color, centerOutBrightness(context, parameters))
  },
  // 水平流動：漸層沿流動方向首尾相接地取色（每台一色），乘上亮度。關閉時照一般的漸層方向。
  // Horizontal flow: the gradient loops along the flow (one colour per spinner), times the brightness. Off: the usual axis.
  gradientColor(context, parameters, colors) {
    if (!flowEnabled(parameters)) return null
    return scaleRgb(sampleGradient(colors, centerOutFlowPhase(context, parameters), true), centerOutBrightness(context, parameters))
  },
  // 雨天：只留波峰細環，由圓心往外滑出。 / Rain: only the crest rings, sliding out from the centre.
  rain(context, parameters) {
    const wave = WAVE_COUNT * context.normalizedRadius - (parameters.speedHz ?? 0.35) * effectTimeSeconds(context)
    return rainRing((cycle(wave + 0.5) - 0.5) / WAVE_COUNT, context.ledCount)
  },
}
