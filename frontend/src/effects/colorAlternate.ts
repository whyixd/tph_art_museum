import { EFFECT_SCHEMA_VERSION, type EffectModule } from './contract'

const DEFAULT_INTERVAL = 8

/** 第幾個間隔（0 起）；間隔以影格計，最短 1 影格 = Art-Net 一次更新。 / Interval index; whole frames, at least one Art-Net update. */
export function alternateStep(controllerFrame: number, parameters: Readonly<Record<string, number>>): number {
  const interval = Math.max(1, Math.round(parameters.intervalFrames ?? DEFAULT_INTERVAL))
  return Math.floor(Math.max(0, controllerFrame) / interval)
}

export const colorAlternate: EffectModule = {
  completion: {
    label: '兩個顏色各亮一次',
    frames: (parameters) => 2 * Math.max(1, Math.round(parameters.intervalFrames ?? DEFAULT_INTERVAL)),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'color-alternate',
    label: '雙色交替',
    description: '整台轉燈在兩個顏色之間直接切換（不漸變）。間隔以影格計：1 影格是 Art-Net 一次更新（40 FPS 時 25 毫秒），是能做到的最短間隔。漸層時用第一色與最後一色交替（不看方向）；純色時是該色與全暗交替（頻閃）。',
    parameters: [
      { key: 'intervalFrames', label: '交替間隔', unit: '影格（40 FPS 時 1 格 = 25 ms）', min: 1, max: 200, step: 1, defaultValue: DEFAULT_INTERVAL },
    ],
  },
  sample(context, parameters) {
    // 純色：亮、暗交替。 / Solid: on, off.
    const value = alternateStep(context.controllerFrame, parameters) % 2 === 0 ? 1 : 0
    return { r: value, g: value, b: value }
  },
  // 漸層：第一色、最後一色交替。 / Gradient: the first and last colours alternate.
  gradientColor: (context, parameters, colors) =>
    alternateStep(context.controllerFrame, parameters) % 2 === 0 ? colors[0] : colors[colors.length - 1],
}
