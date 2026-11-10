import { describe, expect, it } from 'vitest'
import {
  FIXTURE_MANIFEST,
  createDefaultFixturePreset,
  fixtureGeometry,
  getFixtureCompletion,
  renderFixtureFrame,
} from '../src/fixtures'

describe('fixture effects', () => {
  it('builds defaults from the manifest', () => {
    for (const entry of FIXTURE_MANIFEST) {
      const preset = createDefaultFixturePreset(entry.id)
      expect(Object.keys(preset.parameters)).toEqual(entry.parameters.map((item) => item.key))
      for (const item of entry.parameters) {
        expect(item.defaultValue).toBeGreaterThanOrEqual(item.min)
        expect(item.defaultValue).toBeLessThanOrEqual(item.max)
      }
    }
  })

  it('normalizes layout axes and falls back to fixture order', () => {
    const geometry = fixtureGeometry([{ z: 100, x: 0 }, { z: 300 }, { z: 200, x: 10 }])
    expect(geometry.map((row) => row[0])).toEqual([0, 1, 0.5])
    expect(geometry.map((row) => row[1])).toEqual([0, 0.5, 1])
    expect(fixtureGeometry([null])).toEqual([[0.5, 0.5, 0.5]])
  })

  it('sweeps from bottom to top', () => {
    const preset = createDefaultFixturePreset('height-sweep')
    const geometry = fixtureGeometry([{ z: 0 }, { z: 500 }, { z: 1000 }])
    const peak = (frame: number) => {
      const colors = renderFixtureFrame(preset, geometry, frame)
      return colors.indexOf(colors.reduce((best, color) => (color.r > best.r ? color : best)))
    }
    expect(getFixtureCompletion(preset, 40, 3)).toBe(160)
    expect([20, 80, 140].map(peak)).toEqual([0, 1, 2])
  })

  it('chases in chain order with a fading tail', () => {
    const preset = createDefaultFixturePreset('fixture-chase')
    const geometry = fixtureGeometry([null, null, null, null, null])
    const levels = renderFixtureFrame(preset, geometry, 12 * 3).map((color) => color.r)  // head on fixture 3
    ;[0, 1 / 3, 2 / 3, 1, 0].forEach((level, index) => expect(levels[index]).toBeCloseTo(level, 12))
    expect(getFixtureCompletion(preset, 40, 5)).toBe(12 * 5)
  })

  it('bursts like fireworks from the lowest ball to the highest', () => {
    const preset = createDefaultFixturePreset('fixture-firework')
    const geometry = fixtureGeometry([{ z: 0 }, { z: 500 }, { z: 1000 }])
    const levels = (frame: number) => renderFixtureFrame(preset, geometry, frame).map((color) => color.r)
    // Spread 2 s (80 frames), burst 2.5 s, dark 0.5 s: 5 s per round.
    expect(getFixtureCompletion(preset, 40, 3)).toBe(200)
    expect(levels(0)).toEqual([1, 0, 0])
    expect(levels(40)[1]).toBe(1)
    expect(levels(40)[2]).toBe(0)
    expect([0, 0.36]).toContainEqual(Number(levels(40)[0].toFixed(2)))
    expect(levels(80)[2]).toBe(1)
    expect(levels(190)).toEqual([0, 0, 0])
    expect(levels(200)).toEqual([1, 0, 0])
  })
})
