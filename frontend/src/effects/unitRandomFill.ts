import { EFFECT_SCHEMA_VERSION, type EffectModule } from './contract'
import { pillarFillStart, pillarFillTiming } from './pillarFill'
import { randomRanks } from './randomLeds'

// 預設比依柱子點亮慢，而且前後重疊：每 1.5 秒開始一台、每台淡入 4 秒，同時約有 2–3 台在淡入。
// Slower than pillar-fill and overlapping: a unit starts every 1.5 s and fades in over 4 s, so about 2–3 fade in at once.
const DEFAULTS = { stepSeconds: 1.5, acceleration: 1, fadeInSeconds: 4, holdSeconds: 5, fadeOutSeconds: 3, endDarkSeconds: 1 }
// 洗牌種子，與隨機單台（1009）和燈點洗牌分開。 / Shuffle seed, apart from unit-random (1009) and the LED shuffles.
export const UNIT_RANDOM_FILL_SEED = 2027

const timing = (parameters: Readonly<Record<string, number>>, fps: number, unitCount: number) =>
  pillarFillTiming({ ...DEFAULTS, ...parameters }, fps, unitCount)

/** 第 round 輪中這台第幾個亮起（每輪重新隨機）。 / This unit's place in round `round` (a fresh order each round). */
export function unitRandomFillRank(round: number, unitIndex: number, unitCount: number): number {
  return randomRanks(round, UNIT_RANDOM_FILL_SEED, unitCount)[unitIndex]
}

// 與依柱子逐台點亮相同的時間表（逐台淡入並保持、全部亮後停留、一起淡出、全暗），只是順序每輪隨機。
// pillar-fill's schedule (each unit fades in and stays lit, hold, fade out together, dark) in a random order per round.
export const unitRandomFill: EffectModule = {
  completion: {
    label: '隨機逐台點亮、停留、淡出一輪',
    frames: (parameters, fps, unitCount) => timing(parameters, fps, unitCount).cycle,
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'unit-random-fill',
    label: '隨機逐台點亮',
    description: '從全暗開始，依隨機順序一台接一台緩慢淡入，整台轉燈亮起後保持亮著。每台淡入比每台間隔長時，前一台還在變亮、下一台就開始亮（重疊）；兩者相同時一台亮滿才換下一台。全部亮起後停留，一起淡出、全暗，再以新的隨機順序重新開始。逐漸加速大於 1 時，一開始一台台慢慢亮、越後面越快（總時間不變）。全部轉燈共用同一個時間表，不受天氣的多雲錯開與雨天線條影響。',
    parameters: [
      { key: 'stepSeconds', label: '每台間隔（平均）', unit: 's', min: 0.05, max: 10, step: 0.05, defaultValue: DEFAULTS.stepSeconds },
      { key: 'acceleration', label: '逐漸加速', unit: '倍', min: 1, max: 4, step: 0.25, defaultValue: DEFAULTS.acceleration },
      { key: 'fadeInSeconds', label: '每台淡入', unit: 's', min: 0, max: 10, step: 0.05, defaultValue: DEFAULTS.fadeInSeconds },
      { key: 'holdSeconds', label: '全亮停留', unit: 's', min: 0, max: 30, step: 0.5, defaultValue: DEFAULTS.holdSeconds },
      { key: 'fadeOutSeconds', label: '淡出時間', unit: 's', min: 0, max: 10, step: 0.25, defaultValue: DEFAULTS.fadeOutSeconds },
      { key: 'endDarkSeconds', label: '全暗時間', unit: 's', min: 0, max: 10, step: 0.25, defaultValue: DEFAULTS.endDarkSeconds },
    ],
  },
  ignoresWeatherTiming: true,

  sample(context, parameters) {
    const units = Math.max(1, context.unitCount)
    const schedule = timing(parameters, context.controllerFps, units)
    const { fadeIn, fadeStart, fadeOut, cycle } = schedule
    const frame = Math.max(0, context.controllerFrame)
    const round = Math.floor(frame / cycle)
    const elapsed = frame - round * cycle
    const rank = unitRandomFillRank(round, Math.min(units - 1, Math.max(0, context.unitIndex)), units)
    const start = pillarFillStart(schedule, rank, units)
    if (elapsed < start) return { r: 0, g: 0, b: 0 }
    const lit = fadeIn === 0 ? 1 : Math.min(1, (elapsed - start + 1) / fadeIn)
    const value = elapsed < fadeStart ? lit
      : fadeOut === 0 ? 0 : lit * Math.max(0, 1 - (elapsed - fadeStart + 1) / fadeOut)
    return { r: value, g: value, b: value }
  },
}
