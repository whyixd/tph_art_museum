import { createDefaultEffectPreset, createEffectContext, sampleEffect, type EffectColor, type EffectPreset } from '../src/effects'
import { createDefaultFixturePreset, sampleFixture, type FixtureColor, type FixturePreset } from '../src/fixtures'

// 多色模式的前後端對照向量：backend/tests/test_multi_color.py 讀同一個檔。
// 重新產生：npx vite-node scripts/generateMultiColorVectors.ts
// Multi-colour parity vectors, read by backend/tests/test_multi_color.py as well. Regenerate with the command above.
export const FPS = 40
export const LED_COUNT = 129
export const UNIT_COUNT = 9
export const COLORS = [{ r: 1, g: 0, b: 0 }, { r: 0, g: 1, b: 0.2 }, { r: 0.1, g: 0.2, b: 1 }, { r: 1, g: 0.8, b: 0 }, { r: 1, g: 1, b: 1 }]

type Multi = Extract<EffectColor, { mode: 'multi' }>
const multi = (patch: Partial<Multi>): Multi => (
  { mode: 'multi', colors: COLORS, order: 'sequence', per: 'point', stepSeconds: 0.5, fadeSeconds: 0, ...patch }
)
const spinner = (effectId: EffectPreset['effectId'], color: Multi): EffectPreset => (
  { ...createDefaultEffectPreset(effectId, LED_COUNT), color }
)
const fixture = (effectId: FixturePreset['effectId'], color: FixtureColor): FixturePreset => (
  { ...createDefaultFixturePreset(effectId), color }
)

export const spinnerPresets = [
  spinner('full-on', multi({ stepSeconds: 0 })),
  spinner('full-on', multi({})),
  spinner('full-on', multi({ fadeSeconds: 0.2 })),
  spinner('full-on', multi({ order: 'random', fadeSeconds: 0.5 })),
  spinner('full-on', multi({ order: 'random', stepSeconds: 0 })),
  spinner('full-on', multi({ per: 'unit', stepSeconds: 0.33, fadeSeconds: 0.1 })),
  spinner('full-on', multi({ per: 'unit', order: 'random', colors: COLORS.slice(0, 2) })),
  spinner('center-out-sine', multi({ fadeSeconds: 0.3 })),
  spinner('two-tone-trail', multi({ order: 'random' })),
]
const { per: _per, ...fixtureMulti } = multi({})
const fixturePresets = [
  fixture('fixture-breathing', { ...fixtureMulti, stepSeconds: 0 }),
  fixture('fixture-breathing', { ...fixtureMulti, fadeSeconds: 0.25 }),
  fixture('fixture-chase', { ...fixtureMulti, order: 'random', stepSeconds: 1.2, fadeSeconds: 0.4 }),
]
const FRAMES = [0, 7, 19, 20, 23, 27, 45, 400, 12345]

export function buildMultiColorVectors() {
  const spinnerSamples = spinnerPresets.flatMap((preset, presetIndex) => FRAMES.flatMap((frame) => (
    [0, 1, 5, 64, 127, 128].flatMap((ledIndex) => [0, 3, 8].map((unitIndex) => {
      // 各台錯開：效果影格與共同影格不同，換色只看共同影格。 / Offsets: colour steps follow the shared frame only.
      const context = createEffectContext(frame + unitIndex * 3, FPS, ledIndex, LED_COUNT, unitIndex, UNIT_COUNT, frame)
      return { presetIndex, frame, ledIndex, unitIndex, expected: sampleEffect(preset, context) }
    }))
  )))
  const fixtureSamples = fixturePresets.flatMap((preset, presetIndex) => FRAMES.flatMap((frame) => (
    [0, 1, 6, 36].map((fixtureIndex) => ({
      presetIndex, frame, fixtureIndex,
      expected: sampleFixture(preset, {
        controllerFrame: frame, controllerFps: FPS, fixtureIndex, fixtureCount: 37,
        normalizedHeight: fixtureIndex / 36, normalizedX: 0.5, normalizedY: 0.5,
      }),
    }))
  )))
  return { source: 'frontend/tests/multiColorVectors.ts', controllerFps: FPS, ledCount: LED_COUNT, unitCount: UNIT_COUNT,
    fixtureCount: 37, spinnerPresets, fixturePresets, spinnerSamples, fixtureSamples }
}

