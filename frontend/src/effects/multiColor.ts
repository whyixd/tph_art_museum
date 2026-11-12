import { mixRgb, type RgbColor } from './contract'
import { hash01 } from './rouletteRing'

export type MultiOrder = 'sequence' | 'random'

/** 多色模式共用的設定（轉燈與球燈／洗牆燈）。 / Multi-colour settings shared by spinners and DMX fixtures. */
export type MultiColorFields = {
  colors: RgbColor[]
  /** sequence: 第 n 點第 n 色（循環）；random: 每點從清單抽一色。 / nth point takes the nth colour, or a random one. */
  order: MultiOrder
  /** 換色間隔（秒）；0 = 固定不變。 / Seconds between colour changes; 0 keeps them fixed. */
  stepSeconds: number
  /** 換色時的淡入淡出（秒，不超過間隔）；0 = 直接跳。 / Crossfade when changing (at most the step); 0 jumps. */
  fadeSeconds: number
}

export const MULTI_STEP_MAX_SECONDS = 3600
export const MULTI_FADE_MAX_SECONDS = 60
const RANDOM_SALT = 211

/**
 * 多色模式某一點在某一影格的顏色（後端 effects.multi_color_at 逐值相同）。
 * point 決定照順序時取第幾色；key 是隨機時每點自己的種子。每過一個間隔，照順序往前推一格、隨機重新抽。
 * The colour of one point at a frame (identical to the backend's effects.multi_color_at). `point` picks the colour
 * in sequence order; `key` seeds the random pick. Every step the sequence shifts by one and the random pick redraws.
 */
export function multiColorAt(
  multi: MultiColorFields, point: number, key: number, frame: number, fps: number,
): RgbColor {
  const colors = multi.colors
  const count = colors.length
  const stepFrames = multi.stepSeconds > 0 && fps > 0 ? Math.max(1, Math.round(multi.stepSeconds * fps)) : 0
  const now = Math.max(0, Math.floor(frame))
  const step = stepFrames > 0 ? Math.floor(now / stepFrames) : 0
  const pick = (at: number) => colors[multi.order === 'random'
    ? Math.min(count - 1, Math.floor(hash01(at, key, RANDOM_SALT) * count))
    : (point + at) % count]
  const current = pick(step)
  if (step === 0) return current
  const fadeFrames = Math.min(stepFrames, Math.round(multi.fadeSeconds * fps))
  const into = now - step * stepFrames
  return into >= fadeFrames ? current : mixRgb(pick(step - 1), current, into / fadeFrames)
}
