import { EFFECT_SCHEMA_VERSION, clamp01, type EffectModule } from './contract'
import { RAIN_LINES, rainStreak } from './rain'

const DEFAULTS = { holdSeconds: 1, fadeOutSeconds: 0 }

/**
 * 全亮的亮度：淡出時間 0 時一直全亮；否則全亮 holdSeconds 後在 fadeOutSeconds 內線性淡出，之後保持全暗（不循環）。
 * Full-on brightness: always 1 with no fade; otherwise full for holdSeconds, then a linear fade over fadeOutSeconds,
 * then dark (no loop). Both stages are whole frames.
 */
export function fullOnBrightness(controllerFrame: number, fps: number, parameters: Readonly<Record<string, number>>): number {
  const fade = Math.max(0, Math.round((parameters.fadeOutSeconds ?? DEFAULTS.fadeOutSeconds) * fps))
  if (fade === 0) return 1
  const hold = Math.max(0, Math.round((parameters.holdSeconds ?? DEFAULTS.holdSeconds) * fps))
  const frame = Math.max(0, controllerFrame)
  return frame < hold ? 1 : clamp01(1 - (frame - hold + 1) / fade)
}

export const fullOn: EffectModule = {
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'full-on',
    label: '全亮',
    description: '所有燈點以色彩設定的顏色全亮；預設為白色純色。淡出時間大於 0 時，全亮「淡出前全亮」秒後在淡出時間內淡出到全暗並保持全暗（從效果或段落開始算，不循環）。',
    parameters: [
      { key: 'holdSeconds', label: '淡出前全亮', unit: 's', min: 0, max: 60, step: 0.5, defaultValue: DEFAULTS.holdSeconds },
      { key: 'fadeOutSeconds', label: '淡出時間（0 = 不淡出）', unit: 's', min: 0, max: 30, step: 0.25, defaultValue: DEFAULTS.fadeOutSeconds },
    ],
  },
  sample(context, parameters) {
    const value = fullOnBrightness(context.controllerFrame, context.controllerFps, parameters)
    return { r: value, g: value, b: value }
  },
  // 雨天：滿版雨絲，跟著淡出。 / Rain: streaks across the whole disc, fading with it.
  rain: (context, _parameters, base) => base() * rainStreak(context, RAIN_LINES.sparseDensity),
}
