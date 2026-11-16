import { EFFECT_SCHEMA_VERSION, type EffectModule } from './contract'

/**
 * 快閃淡出（flash-fade）：快速亮起、慢慢淡出、全暗，循環。轉燈與球燈共用（src/fixtures 同一個公式），
 * 後端 effects.flash_fade_level 逐值一致。
 * flash-fade: a quick attack, a slow fade out, then dark, looping. Shared by spinners and balls (src/fixtures);
 * backend effects.flash_fade_level matches value for value.
 */
export const FLASH_FADE_PARAMETERS = [
  { key: 'attackSeconds', label: '亮起時間', unit: 's', min: 0, max: 2, step: 0.05, defaultValue: 0.15 },
  { key: 'decaySeconds', label: '淡出時間', unit: 's', min: 0.1, max: 30, step: 0.1, defaultValue: 3 },
  { key: 'curve', label: '淡出曲線', unit: '0 線性／1 平方（尾巴拉長）', min: 0, max: 1, step: 1, defaultValue: 1 },
  { key: 'gapSeconds', label: '全暗時間', unit: 's', min: 0, max: 30, step: 0.25, defaultValue: 1 },
] as const

export const FLASH_FADE_DESCRIPTION = '快速亮起，再慢慢淡出到全暗，全暗停一下後重新開始。亮起時間 0 是瞬間全亮；淡出曲線 1（平方）一開始掉得快、尾巴拉得長，看起來比線性柔和。全部一起閃（多雲時照天氣錯開）。'

/** (亮起, 淡出, 一輪) 影格；亮起與全暗可為 0。 / (attack, decay, cycle) frames; attack and gap may be 0. */
export function flashFadeTiming(p: Readonly<Record<string, number>>, fps: number) {
  const attack = Math.max(0, Math.round(p.attackSeconds * fps))
  const decay = Math.max(1, Math.round(p.decaySeconds * fps))
  const gap = Math.max(0, Math.round(p.gapSeconds * fps))
  return { attack, decay, cycle: attack + decay + gap }
}

/**
 * 亮度 0..1：亮起時 (t + 1) / attack 線性到 1；淡出第一格正好是 1，之後線性或平方降到最後一格 1/decay（平方時再平方）；之後全暗。
 * Level 0..1: the attack ramps (t + 1) / attack up to 1; the fade starts exactly at 1 and falls linearly (or squared)
 * towards 0 over decay frames; then dark.
 */
export function flashFadeLevel(p: Readonly<Record<string, number>>, frame: number, fps: number): number {
  const { attack, decay, cycle } = flashFadeTiming(p, fps)
  const at = Math.max(0, frame)
  const local = at - Math.floor(at / cycle) * cycle
  if (local < attack) return (local + 1) / attack
  if (local >= attack + decay) return 0
  const remaining = 1 - (local - attack) / decay
  return p.curve === 1 ? remaining * remaining : remaining
}

export const flashFade: EffectModule = {
  completion: {
    label: '亮起、淡出、全暗一次',
    frames: (parameters, fps) => flashFadeTiming(parameters, fps).cycle,
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'flash-fade',
    label: '快閃淡出',
    description: `整台轉燈${FLASH_FADE_DESCRIPTION}漸層照一般方向取色。`,
    parameters: FLASH_FADE_PARAMETERS.map((parameter) => ({ ...parameter })),
  },
  sample(context, parameters) {
    const level = flashFadeLevel(parameters, context.controllerFrame, context.controllerFps)
    return { r: level, g: level, b: level }
  },
}
