import { describe, expect, it } from 'vitest'
import {
  FIXTURE_MANIFEST,
  createDefaultFixturePreset,
  fixtureGeometry,
  getFixtureCompletion,
  renderFixtureFrame,
} from '../src/fixtures'
import { exampleSiteLayout, fixturePoles } from '../src/site/layout'

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

  it('keeps a base level and adds a travelling sine of the same colour', () => {
    const preset = createDefaultFixturePreset('base-wave')
    expect(getFixtureCompletion(preset, 40, 3)).toBe(134)
    const geometry = fixtureGeometry([{ z: 0 }, { z: 500 }, { z: 1000 }])
    const levels = (frame: number, target = preset) => renderFixtureFrame(target, geometry, frame).map((color) => color.r)
    // Base 0.3, depth 0.7: the crest reaches 0.3 + 0.7 × 0.7 = 0.79, the trough stays at 0.3.
    const all = Array.from({ length: 134 }, (_, frame) => levels(frame)).flat()
    expect(Math.min(...all)).toBeGreaterThanOrEqual(0.3 - 1e-12)
    expect(Math.max(...all)).toBeLessThanOrEqual(0.79 + 1e-12)
    expect(levels(0)[0]).toBeCloseTo(0.79, 12) // one wave over the height: crest at the bottom at t = 0
    expect(levels(0)[1]).toBeCloseTo(0.3, 12)
    const flat = { ...preset, parameters: { ...preset.parameters, depth: 0 } }
    expect(levels(57, flat)).toEqual([0.3, 0.3, 0.3])
    const red = { ...preset, color: { mode: 'solid' as const, color: { r: 1, g: 0, b: 0 } } }
    expect(renderFixtureFrame(red, geometry, 0).every((color) => color.g === 0 && color.b === 0)).toBe(true)
  })

  it('lights each pole bottom up with a fast attack and slow fade, poles staggered', () => {
    const preset = createDefaultFixturePreset('pole-rise')
    expect(getFixtureCompletion(preset, 40, 6)).toBe(320)
    const geometry = fixtureGeometry([{ z: 100 }, { z: 300 }, { z: 100 }, { z: 300 }])
    const poles = [{ rank: 0, step: 0 }, { rank: 0, step: 1 }, { rank: 1, step: 0 }, { rank: 1, step: 1 }]
    const levels = (frame: number, target = preset) => renderFixtureFrame(target, geometry, frame, { poles }).map((color) => color.r)
    // Step 0.2 s (8 frames), attack 0.1 s (4), pole stagger 0.3 s (12), decay 2.5 s (100).
    expect(levels(0)).toEqual([0.25, 0, 0, 0])
    // Frame 8: the low ball is 5 frames into its decay ((1 - 5/100)^2); the one above it starts.
    expect(levels(8)[0]).toBeCloseTo(0.9025, 12)
    expect(levels(8).slice(1)).toEqual([0.25, 0, 0])
    expect(levels(12)[2]).toBe(0.25)
    expect(levels(60)[0]).toBeGreaterThan(0)
    expect(levels(60)[0]).toBeLessThan(levels(60)[1])
    expect(levels(319)).toEqual([0, 0, 0, 0])
    // Stagger 0: both poles start together.
    const together = { ...preset, parameters: { ...preset.parameters, staggerSeconds: 0 } }
    expect(levels(0, together)).toEqual([0.25, 0, 0.25, 0])
  })

  it('assigns the example layout balls to poles, low to high', () => {
    const layout = exampleSiteLayout()
    const poles = fixturePoles(layout.ball.map((fixture) => fixture.position), layout.poles)
    // Balls 1–4 on the first pole, low to high; 14 poles hold balls.
    expect(poles.slice(0, 4)).toEqual([{ rank: 0, step: 0 }, { rank: 0, step: 1 }, { rank: 0, step: 2 }, { rank: 0, step: 3 }])
    expect(Math.max(...poles.map((pole) => pole.rank)) + 1).toBe(14)
  })

  it('lights the balls slowly from the lowest to the highest and keeps them lit', () => {
    const preset = createDefaultFixturePreset('fixture-rise')
    const geometry = fixtureGeometry([{ z: 1000 }, { z: 0 }, { z: 500 }])
    const levels = (frame: number, target = preset) => renderFixtureFrame(target, geometry, frame).map((color) => color.r)
    // Rise 10 s (400 frames), fade in 3 s (120 frames): the lowest (index 1) starts at 0, the middle at 200, the top at 400.
    expect(getFixtureCompletion(preset, 40, 3)).toBeNull()
    expect(levels(0)[1]).toBeGreaterThan(0)
    expect(levels(0)[1]).toBeLessThan(0.01)
    expect(levels(199)[2]).toBe(0)
    expect(levels(260)[2]).toBeCloseTo(0.5, 1) // halfway through its smoothstep fade-in
    expect(levels(399)[0]).toBe(0)
    expect(levels(519)).toEqual([1, 1, 1])
    expect(levels(40000)).toEqual([1, 1, 1])
    // With repeat they hold, fade out together and start again.
    const looping = { ...preset, parameters: { ...preset.parameters, repeat: 1 } }
    const cycle = (10 + 3 + 5 + 3 + 1) * 40
    expect(getFixtureCompletion(looping, 40, 3)).toBe(cycle)
    expect(levels(cycle - 1, looping)).toEqual([0, 0, 0])
    expect(levels(cycle, looping)[0]).toBe(0)
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
