import {
  EFFECT_SCHEMA_VERSION,
  effectTimeSeconds,
  mixRgb,
  scaleRgb,
  type EffectModule,
} from './contract'
import { rainRing } from './rain'

const TAU = Math.PI * 2
const cycle = (value: number) => value - Math.floor(value)
const WAVE_COUNT = 1.5
const CENTER_COLOR = { r: 0.2, g: 0.92, b: 1 }
const EDGE_COLOR = { r: 0.66, g: 0.28, b: 1 }

export const centerOutSine: EffectModule = {
  completion: {
    label: '波形完成一個週期',
    frames: (parameters, fps) => fps / (parameters.speedHz ?? 0.35),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'center-out-sine',
    label: 'Center-out sine / 中心擴散',
    description: '平滑 sine 亮度波由圓心向外傳播。波谷不會全暗，最暗是「最低亮度」（設 0.04 為舊版效果）。',
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
    ],
  },

  sample(context, parameters) {
    const speedHz = parameters.speedHz ?? 0.35
    const phase = TAU * (
      WAVE_COUNT * context.normalizedRadius -
      speedHz * effectTimeSeconds(context)
    )
    const sineBrightness = 0.5 + 0.5 * Math.cos(phase)
    // 波谷保留最低亮度，不會變成全暗。 / The trough keeps minLevel, never black.
    const minLevel = parameters.minLevel ?? 0.3
    const brightness = minLevel + sineBrightness * (1 - minLevel)
    const color = mixRgb(
      CENTER_COLOR,
      EDGE_COLOR,
      context.normalizedRadius,
    )

    return scaleRgb(color, brightness)
  },
  // 雨天：只留波峰細環，由圓心往外滑出。 / Rain: only the crest rings, sliding out from the centre.
  rain(context, parameters) {
    const wave = WAVE_COUNT * context.normalizedRadius - (parameters.speedHz ?? 0.35) * effectTimeSeconds(context)
    return rainRing((cycle(wave + 0.5) - 0.5) / WAVE_COUNT, context.ledCount)
  },
}
