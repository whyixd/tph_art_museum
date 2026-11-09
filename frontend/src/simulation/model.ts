import type { Point3 } from '../preview3d/geometry'
import { DEFAULT_LED_COUNT } from '../site/spinnerGeometry'

export const TAU = Math.PI * 2

export type SimulationSettings = {
  rpm: number
  controllerFps: number
  exposureMs: number
  persistenceMs: number
  temporalSamples: number
  ledCount: number
  unitPhaseStepDegrees: number
  unitFrameOffsetStep: number
}

export type TemporalSample = {
  timeSeconds: number
  weight: number
}

export const DEFAULT_SETTINGS: SimulationSettings = {
  rpm: 360,
  controllerFps: 40,
  exposureMs: 25,
  persistenceMs: 45,
  temporalSamples: 32,
  // 每條燈條的顆數；連上後端時改用 /api/state 的 output.pixels。 / LEDs per strip; replaced by the backend's output.pixels.
  ledCount: DEFAULT_LED_COUNT,
  unitPhaseStepDegrees: 40,
  // 與後端相同：沒有天氣時各台不錯開。 / As the backend: no per-unit offset without weather.
  unitFrameOffsetStep: 0,
}

export function rotationsPerSecond(rpm: number): number {
  return rpm / 60
}

export function angleAtTime(
  timeSeconds: number,
  rpm: number,
  initialPhase = 0,
): number {
  return initialPhase + timeSeconds * rotationsPerSecond(rpm) * TAU
}

export function normalizedAngle(angle: number): number {
  return ((angle % TAU) + TAU) % TAU
}

export function controllerFrameAtTime(
  timeSeconds: number,
  fps: number,
  offsetSeconds = 0,
): number {
  if (fps <= 0) return 0
  return Math.max(0, Math.floor((timeSeconds + offsetSeconds) * fps))
}

export function degreesPerControllerFrame(rpm: number, fps: number): number {
  if (fps <= 0) return 0
  return (rpm * 6) / fps
}

export function controllerFrameStartTime(
  frame: number,
  fps: number,
  frameOffset = 0,
): number {
  if (fps <= 0) return 0
  return Math.max(0, (frame - frameOffset) / fps)
}

export function simulatedUnknownPhase(unitIndex: number, resetToken: number): number {
  const hash = (
    Math.imul(unitIndex + 1, 0x9e3779b1) ^
    Math.imul(resetToken + 1, 0x85ebca6b)
  ) >>> 0
  return (hash / 0x100000000) * TAU
}

export function unitRelativeHeightAtPhase(
  position: Point3,
  phaseCosine: number,
  phaseSine: number,
  tiltCosine: number,
  tiltSine: number,
  scale: number,
): number {
  const rotatedZ = -phaseSine * position[0] + phaseCosine * position[2]
  return scale * (tiltCosine * position[1] - tiltSine * rotatedZ)
}

export function buildTemporalSamples(
  nowSeconds: number,
  exposureMs: number,
  persistenceMs: number,
  sampleCount: number,
): TemporalSample[] {
  const count = Math.max(1, Math.floor(sampleCount))
  const exposureSeconds = Math.max(0, exposureMs) / 1000
  const persistenceSeconds = Math.max(0, persistenceMs) / 1000
  const historySeconds = exposureSeconds + persistenceSeconds * 4

  if (historySeconds === 0 || count === 1) {
    return [{ timeSeconds: nowSeconds, weight: 1 }]
  }

  const samples = Array.from({ length: count }, (_, index) => {
    const age = historySeconds * (index / (count - 1))
    const persistenceAge = Math.max(0, age - exposureSeconds)
    const weight =
      persistenceAge === 0
        ? 1
        : Math.exp(-persistenceAge / Math.max(persistenceSeconds, 1e-6))

    return {
      timeSeconds: nowSeconds - age,
      weight,
    }
  })

  const weightSum = samples.reduce((sum, sample) => sum + sample.weight, 0)
  return samples.map((sample) => ({
    ...sample,
    weight: sample.weight / weightSum,
  }))
}
