import { describe, expect, it } from 'vitest'
import { createEffectContext, multiColorAt, sampleEffect } from '../src/effects'
import vectors from '../../backend/fixtures/multi_color_v1.json'
import { buildMultiColorVectors, COLORS, FPS, LED_COUNT, UNIT_COUNT, spinnerPresets } from './multiColorVectors'

describe('multi colour mode', () => {
  const fields = { colors: COLORS, order: 'sequence' as const, stepSeconds: 0.5, fadeSeconds: 0 }

  it('gives point n colour n and shifts by one every step', () => {
    expect([0, 1, 4, 5, 6].map((point) => multiColorAt(fields, point, point, 0, FPS))).toEqual(
      [COLORS[0], COLORS[1], COLORS[4], COLORS[0], COLORS[1]])
    expect(multiColorAt(fields, 0, 0, 19, FPS)).toEqual(COLORS[0])
    expect(multiColorAt(fields, 0, 0, 20, FPS)).toEqual(COLORS[1])
    expect(multiColorAt({ ...fields, stepSeconds: 0 }, 2, 2, 99999, FPS)).toEqual(COLORS[2])
  })

  it('crossfades from the previous colour', () => {
    const faded = multiColorAt({ ...fields, fadeSeconds: 0.25 }, 0, 0, 25, FPS)
    expect(faded.r).toBeCloseTo(0.5)
    expect(faded.g).toBeCloseTo(0.5)
    expect(multiColorAt({ ...fields, fadeSeconds: 0.25 }, 0, 0, 30, FPS)).toEqual(COLORS[1])
  })

  it('draws random colours from the list, again every step', () => {
    const random = { ...fields, order: 'random' as const }
    const picks = Array.from({ length: 200 }, (_, key) => multiColorAt(random, 0, key, 0, FPS))
    for (const pick of picks) expect(COLORS).toContainEqual(pick)
    expect(new Set(picks.map((pick) => COLORS.indexOf(pick))).size).toBe(COLORS.length)
    expect(picks.some((pick, key) => pick !== multiColorAt(random, 0, key, 20, FPS))).toBe(true)
    expect(multiColorAt(random, 0, 7, 0, FPS)).toBe(multiColorAt(random, 0, 7, 19, FPS))
  })

  it('colours both LEDs of a spinner ring alike', () => {
    for (const preset of spinnerPresets.slice(0, 5)) {
      const at = (ledIndex: number) => sampleEffect(preset, createEffectContext(45, FPS, ledIndex, LED_COUNT, 3, UNIT_COUNT))
      expect(at(5)).toEqual(at(LED_COUNT - 1 - 5))
    }
  })

  it('matches the vectors shared with the backend', () => {
    expect(JSON.parse(JSON.stringify(buildMultiColorVectors()))).toEqual(vectors)
  })
})
