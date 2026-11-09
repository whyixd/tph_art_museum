import { describe, expect, it } from 'vitest'
import {
  angleAtTime,
  buildTemporalSamples,
  controllerFrameAtTime,
  controllerFrameStartTime,
  DEFAULT_SETTINGS,
  degreesPerControllerFrame,
  normalizedAngle,
  simulatedUnknownPhase,
  unitRelativeHeightAtPhase,
} from '../src/simulation/model'

describe('rotor and controller clocks', () => {
  it('rotates 54 degrees during one 40 FPS frame at 360 RPM', () => {
    expect(degreesPerControllerFrame(360, 40)).toBe(54)
  })

  it('keeps the LED frame unchanged inside each 25 ms interval', () => {
    expect(controllerFrameAtTime(0.024999, 40)).toBe(0)
    expect(controllerFrameAtTime(0.025, 40)).toBe(1)
  })

  it('returns to the same nominal phase after 20 controller frames', () => {
    const angleAtStart = normalizedAngle(angleAtTime(0, 360))
    const angleAtTwentyFrames = normalizedAngle(angleAtTime(20 / 40, 360))
    expect(angleAtTwentyFrames).toBeCloseTo(angleAtStart, 10)
  })
})

describe('open-loop top-third sampling', () => {
  it('keeps one Art-Net LED choice while the rotor leaves the top 120-degree sector', () => {
    const localLed = [-1, 0, 0] as const
    const initialPhase = 320 * Math.PI / 180
    const frame = controllerFrameAtTime(0.024, 40)
    const heldAngle = angleAtTime(controllerFrameStartTime(frame, 40), 360, initialPhase)
    const actualAngle = angleAtTime(0.024, 360, initialPhase)
    const heightAt = (angle: number) => unitRelativeHeightAtPhase(
      localLed, Math.cos(angle), Math.sin(angle), 0, 1, 1,
    )

    expect(frame).toBe(0)
    expect(heightAt(heldAngle)).toBeGreaterThan(0.5)
    expect(heightAt(actualAngle)).toBeLessThan(0.5)
    expect(controllerFrameStartTime(controllerFrameAtTime(0.025, 40), 40))
      .toBe(0.025)
  })

  it('assigns different unknown startup phases per unit and reset', () => {
    const first = simulatedUnknownPhase(0, 0)
    expect(first).toBeGreaterThanOrEqual(0)
    expect(first).toBeLessThan(Math.PI * 2)
    expect(simulatedUnknownPhase(0, 0)).toBe(first)
    expect(simulatedUnknownPhase(1, 0)).not.toBe(first)
    expect(simulatedUnknownPhase(0, 1)).not.toBe(first)
  })
})
describe('museum defaults', () => {
  it('uses 129 LEDs per strip and no per-unit offset, as the backend example', () => {
    expect(DEFAULT_SETTINGS.ledCount).toBe(129)
    expect(DEFAULT_SETTINGS.unitFrameOffsetStep).toBe(0)
  })
})

describe('temporal exposure samples', () => {
  it('normalizes exposure and persistence weights', () => {
    const samples = buildTemporalSamples(1, 25, 45, 64)
    const totalWeight = samples.reduce((sum, sample) => sum + sample.weight, 0)
    expect(samples).toHaveLength(64)
    expect(totalWeight).toBeCloseTo(1, 10)
    expect(samples[0].timeSeconds).toBe(1)
    expect(samples.at(-1)!.timeSeconds).toBeLessThan(0.8)
  })

  it('uses one current-time sample when blur is disabled', () => {
    expect(buildTemporalSamples(2, 0, 0, 64)).toEqual([
      { timeSeconds: 2, weight: 1 },
    ])
  })
})
