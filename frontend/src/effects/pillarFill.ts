import { EFFECT_SCHEMA_VERSION, type EffectModule } from './contract'

const DEFAULTS = { stepSeconds: 0.5, acceleration: 1, fadeInSeconds: 0.3, holdSeconds: 2, fadeOutSeconds: 1, endDarkSeconds: 0.5 }

type Timing = { span: number; acceleration: number; fadeIn: number; fadeStart: number; fadeOut: number; cycle: number }

// 各段取整數影格：第一台到最後一台開始的總長 span = (N - 1) × step（step 是平均間隔）；第 r 台在
// round(span × (r / (N - 1))^(1 / 加速程度)) 開始淡入並保持全亮，加速程度 1 為等間隔，越大一開始越慢、後面越快。
// 最後一台亮滿後停留，全部一起淡出，再全暗。
// Whole frames: the starts span span = (N - 1) × step (step is the average interval); the unit ranked r starts fading in
// at round(span × (r / (N - 1))^(1 / acceleration)) and stays lit. Acceleration 1 is even; higher starts slow and
// speeds up. Once the last is fully lit they hold, fade out together, then stay dark.
export function pillarFillTiming(parameters: Readonly<Record<string, number>>, fps: number, unitCount: number): Timing {
  const frames = (key: keyof typeof DEFAULTS, min: number) =>
    Math.max(min, Math.round((parameters[key] ?? DEFAULTS[key]) * fps))
  const span = (Math.max(1, unitCount) - 1) * frames('stepSeconds', 1)
  const acceleration = Math.max(1, parameters.acceleration ?? DEFAULTS.acceleration)
  const fadeIn = frames('fadeInSeconds', 0)
  const fadeOut = frames('fadeOutSeconds', 0)
  const fadeStart = span + fadeIn + frames('holdSeconds', 0)
  return { span, acceleration, fadeIn, fadeStart, fadeOut, cycle: Math.max(1, fadeStart + fadeOut + frames('endDarkSeconds', 0)) }
}

/** 第 rank 台開始淡入的影格。 / The frame the unit ranked `rank` starts fading in. */
export function pillarFillStart(timing: Timing, rank: number, unitCount: number): number {
  if (unitCount <= 1) return 0
  return Math.round(timing.span * (rank / (unitCount - 1)) ** (1 / timing.acceleration))
}

export const pillarFill: EffectModule = {
  completion: {
    label: '依柱子逐台點亮、停留、淡出一輪',
    frames: (parameters, fps, unitCount) => pillarFillTiming(parameters, fps, unitCount).cycle,
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'pillar-fill',
    label: '依柱子逐台點亮',
    description: '從全暗開始，一根柱子接一根柱子（依場地配置的柱子順序），每根柱子上由低到高逐台點亮整台轉燈並保持亮著；全部亮起後停留，一起淡出、全暗，再重新開始。逐漸加速大於 1 時，一開始一台台慢慢亮、越後面越快（總時間不變）。轉燈歸屬最近的柱子；沒有場地配置時依燈台編號。九台共用同一個時間表，不受天氣的多雲錯開與雨天線條影響。',
    parameters: [
      { key: 'stepSeconds', label: '每台間隔（平均）', unit: 's', min: 0.05, max: 5, step: 0.05, defaultValue: DEFAULTS.stepSeconds },
      { key: 'acceleration', label: '逐漸加速', unit: '倍', min: 1, max: 4, step: 0.25, defaultValue: DEFAULTS.acceleration },
      { key: 'fadeInSeconds', label: '每台淡入', unit: 's', min: 0, max: 5, step: 0.05, defaultValue: DEFAULTS.fadeInSeconds },
      { key: 'holdSeconds', label: '全亮停留', unit: 's', min: 0, max: 30, step: 0.5, defaultValue: DEFAULTS.holdSeconds },
      { key: 'fadeOutSeconds', label: '淡出時間', unit: 's', min: 0, max: 10, step: 0.25, defaultValue: DEFAULTS.fadeOutSeconds },
      { key: 'endDarkSeconds', label: '全暗時間', unit: 's', min: 0, max: 10, step: 0.25, defaultValue: DEFAULTS.endDarkSeconds },
    ],
  },
  ignoresWeatherTiming: true,

  sample(context, parameters) {
    const timing = pillarFillTiming(parameters, context.controllerFps, context.unitCount)
    const { fadeIn, fadeStart, fadeOut, cycle } = timing
    const frame = Math.max(0, context.controllerFrame)
    const elapsed = frame - Math.floor(frame / cycle) * cycle
    const units = Math.max(1, context.unitCount)
    const rank = Math.min(units - 1, Math.max(0, context.unitRank ?? context.unitIndex))
    const start = pillarFillStart(timing, rank, units)
    if (elapsed < start) return { r: 0, g: 0, b: 0 }
    const lit = fadeIn === 0 ? 1 : Math.min(1, (elapsed - start + 1) / fadeIn)
    const value = elapsed < fadeStart ? lit
      : fadeOut === 0 ? 0 : lit * Math.max(0, 1 - (elapsed - fadeStart + 1) / fadeOut)
    return { r: value, g: value, b: value }
  },
}
