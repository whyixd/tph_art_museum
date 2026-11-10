import { describe, expect, it } from 'vitest'
import { compileSequence, createSequenceStep, resolveSequenceFrame } from '../src/simulation/sequence'
import { createDefaultEffectPreset } from '../src/effects'
import { createDefaultFixturePreset } from '../src/fixtures'
import { EXAMPLE_LAYOUT, exampleSiteLayout, pillarRanks, poleArms, scenePositions, siteLayout, toScene } from '../src/site/layout'
import { buildLoopLeds, effectIndexFor, loopLedPosition, SPINNER_RADIUS } from '../src/site/spinnerGeometry'

describe('museum spinner geometry', () => {
  it('starts at the top and runs once around the circle', () => {
    const top = loopLedPosition(0, 129, 0)
    expect(top[1]).toBeCloseTo(SPINNER_RADIUS)
    const bottomA = loopLedPosition(64, 129, 0)
    const bottomB = loopLedPosition(65, 129, 0)
    expect(bottomA[1]).toBeLessThan(-0.99 * SPINNER_RADIUS)
    expect(bottomB[1]).toBeCloseTo(bottomA[1], 10)      // the bottom sits between LEDs 64 and 65
    expect(bottomA[0]).toBeCloseTo(-bottomB[0], 10)
  })

  it('maps effect pixels like the backend pixel_rotation', () => {
    expect(effectIndexFor(0, 129, 64)).toBe(64)      // top shows the effect centre
    expect(effectIndexFor(64, 129, 64)).toBe(128)    // bottom: both effect ends
    expect(effectIndexFor(65, 129, 64)).toBe(0)
    expect(effectIndexFor(5, 156, 0)).toBe(5)
  })

  it('places three strips 60 degrees apart sharing the same data', () => {
    const leds = buildLoopLeds(129, 64)
    expect(leds).toHaveLength(3 * 129)
    const side = leds.filter((led) => led.index === 32)
    expect(side.map((led) => led.effectIndex)).toEqual([96, 96, 96])
    const angles = side.map((led) => Math.atan2(led.position[2], led.position[0]) * 180 / Math.PI)
    angles.forEach((angle, strip) => expect(angle).toBeCloseTo(strip * 60, 6))
  })
})

describe('site layout', () => {
  it('reads the example layout: 24 spinners and 37 balls', () => {
    const layout = exampleSiteLayout()
    expect(layout.spinners).toHaveLength(24)
    expect(layout.ball).toHaveLength(37)
    expect(layout.spinners[0]).toEqual({ x: 961, y: 118, z: 565 })
    expect(layout.ball[35]).toEqual({ id: 36, position: { x: 3455, y: 349, z: 1030 } })
    expect(layout.poles).toHaveLength(15)
    expect(layout.poles[0]).toEqual({ x: 961, y: 169, z: 1000 })
  })

  it('hangs every light from a nearby pole', () => {
    const layout = exampleSiteLayout()
    const arms = poleArms(layout, 35)
    expect(arms).toHaveLength(24 + 37)              // every spinner and ball reaches a pole within 1.2 m
    const first = arms[0]                            // unit 1 at (961, 118), top 565 + 35 cm; pole at (961, 169)
    expect(first.from).toEqual([9.61, 6, -1.69])
    expect(first.to).toEqual([9.61, 6, -1.18])
    for (const arm of arms) expect(Math.hypot(arm.to[0] - arm.from[0], arm.to[2] - arm.from[2])).toBeLessThanOrEqual(1.2)
  })

  it('converts centimetres to the Y-up scene, back wall towards -Z', () => {
    expect(toScene({ x: 961, y: 118, z: 565 })).toEqual([9.61, 5.65, -1.18])
  })

  it('lines up items without positions along the front', () => {
    const layout = siteLayout(EXAMPLE_LAYOUT, 26, { ball: 37, wash: 2 })
    expect(layout.spinners[25]).toBeNull()
    expect(layout.wash).toEqual([{ id: 1, position: null }, { id: 2, position: null }])
    const points = scenePositions(layout.wash.map((fixture) => fixture.position), 300)
    expect(points).toEqual([[1.5, 3, 1.5], [3, 3, 1.5]])
  })
})

describe('pillar order', () => {
  it('goes pole by pole in layout order, low to high on each pole', () => {
    const layout = exampleSiteLayout()
    // Pole 6 holds units 11, 12 and 14 (before 13 on pole 7); poles 10, 11 and 12 hold 20, then 18 and 19, then 17.
    expect(pillarRanks(layout.spinners, layout.poles)).toEqual(
      [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 12, 14, 15, 19, 17, 18, 16, 20, 21, 22, 23])
  })

  it('puts spinners without a nearby pole last, by unit', () => {
    const poles = [{ x: 0, y: 0, z: 800 }]
    expect(pillarRanks([{ x: 500, y: 0, z: 100 }, { x: 50, y: 0, z: 600 }, null, { x: 0, y: 50, z: 300 }], poles))
      .toEqual([2, 1, 3, 0])
  })
})

describe('sequence clock and fixture layers', () => {
  it('counts a step by the ball cycle and wraps only the clock group', () => {
    const step = createSequenceStep(1, createDefaultEffectPreset('full-on'))
    step.fixtures = { ball: { ...createDefaultFixturePreset('fixture-breathing'), parameters: { breatheHz: 0.2, minLevel: 0.1 } } }
    step.playback = { kind: 'count', count: 2, clock: 'ball' }
    const sequence = compileSequence([step], 40, 24)
    expect(sequence.totalFrames).toBe(400)
    expect(sequence.steps[0].groupCycles).toEqual({ spinner: null, ball: 200, wash: null })
    const layer = resolveSequenceFrame(sequence, 250)!.layers[0]
    expect(layer.fixtures?.ball?.effectId).toBe('fixture-breathing')
    expect(layer.cycleFrames).toBeNull()          // the spinner runs freely
    expect(layer.localFrame).toBe(250)
  })

  it('uses the fixture count for a chase clock', () => {
    const step = createSequenceStep(1, createDefaultEffectPreset('full-on'))
    step.fixtures = { ball: { ...createDefaultFixturePreset('fixture-chase'), parameters: { stepSeconds: 0.25, tailCount: 2, direction: 0 } } }
    step.playback = { kind: 'count', count: 1, clock: 'ball' }
    expect(compileSequence([step], 40, 24, { kind: 'hold' }, { ball: 37, wash: 0 }).totalFrames).toBe(10 * 37)
  })
})
