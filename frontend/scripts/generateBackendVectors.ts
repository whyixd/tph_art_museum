/** Generate read-only reference vectors from the actual frontend effect functions. */
import { createHash } from 'node:crypto'
import { compileSequence, createSequenceStep, layerUnitWeight, resolveSequenceFrame } from '../src/simulation/sequence'
import { writeFileSync } from 'node:fs'
import { exampleSiteLayout } from '../src/site/layout'
import { fixtureGeometry } from '../src/fixtures'
import { createDefaultEffectPreset, createEffectContext, getEffectCompletion, getVariableParameters, presetForUnit, sampleEffect, type EffectPreset } from '../src/effects'

const ids = [
  'spectrum-cycle',
  'center-out-sine',
  'moving-rings',
  'sequential-center-fill',
  'periodic-leds',
  'random-leds',
  'world-upper-half',
  'reverse-tide',
  'wide-rings',
  'comet-tail',
  'stardust-cloud',
  'color-breathing',
  'sector-smear',
  'twenty-beat',
  'sine-swell',
  'random-fill',
  'full-on',
  'unit-chase',
  'unit-random',
  'roulette-ring',
  'two-tone-trail',
  'firework-rise',
  'pillar-fill',
  'color-alternate',
  'wave-split',
] as const

const gradient8 = [
  { r: 0, g: 0.1, b: 1 },
  { r: 0.15, g: 0.8, b: 0.3 },
  { r: 1, g: 0.1, b: 0.2 },
  { r: 0.8, g: 0.9, b: 0 },
  { r: 0.25, g: 0.2, b: 0.7 },
  { r: 1, g: 0.6, b: 0.3 },
  { r: 0.4, g: 1, b: 0.8 },
  { r: 0.7, g: 0.1, b: 0.9 },
]

const presets: EffectPreset[] = ids.flatMap((id) => {
  const base = createDefaultEffectPreset(id)
  return [
    base,
    { ...base, color: { mode: 'solid', color: { r: 0.2, g: 0.75, b: 0.1 } } },
    { ...base, color: { mode: 'gradient', axis: 'strip', colors: [
      { r: 1, g: 0.1, b: 0 }, { r: 0, g: 0.2, b: 1 },
    ] } },
    { ...base, color: { mode: 'gradient', axis: 'radius', colors: gradient8 } },
  ]
})

const contexts = [
  { controllerFrame: 0, globalControllerFrame: 0, ledIndex: 0, unitIndex: 0, unitRelativeY: -1, unitPhaseProjection: 0.49 },
  { controllerFrame: 0, globalControllerFrame: 0, ledIndex: 77, unitIndex: 0, unitRelativeY: 0, unitPhaseProjection: 0.51 },
  { controllerFrame: 1, globalControllerFrame: 1, ledIndex: 78, unitIndex: 8, unitRelativeY: 1, unitPhaseProjection: -0.51 },
  { controllerFrame: 3, globalControllerFrame: 3, ledIndex: 155, unitIndex: 0, unitRelativeY: 1, unitPhaseProjection: -1 },
  { controllerFrame: 4, globalControllerFrame: 4, ledIndex: 1, unitIndex: 8, unitRelativeY: -1, unitPhaseProjection: 0 },
  { controllerFrame: 19, globalControllerFrame: 19, ledIndex: 154, unitIndex: 8, unitRelativeY: 0, unitPhaseProjection: 0.5 },
  { controllerFrame: 20, globalControllerFrame: 800, ledIndex: 77, unitIndex: 8, unitRelativeY: 1, unitPhaseProjection: 1 },
  { controllerFrame: 100, globalControllerFrame: 100, ledIndex: 78, unitIndex: 0, unitRelativeY: -1, unitPhaseProjection: -0.49 },
  { controllerFrame: 160, globalControllerFrame: 160, ledIndex: 33, unitIndex: 5, unitRelativeY: 0.01, unitPhaseProjection: 1 },
]

const samples = presets.flatMap((preset, presetIndex) => contexts.map((context) => ({
  presetIndex,
  context,
  expected: sampleEffect(
    preset,
    createEffectContext(
      context.controllerFrame,
      40,
      context.ledIndex,
      156,
      context.unitIndex,
      9,
      context.globalControllerFrame,
      context.unitRelativeY,
      context.unitPhaseProjection,
    ),
  ),
})))

const frames = presets.flatMap((preset, presetIndex) =>
  [0, 3, 4, 19, 20, 100, 800, 1600, 2650].flatMap((globalFrame) =>
    [0, 8].map((unitIndex) => {
      const controllerFrame = globalFrame + unitIndex * 2
      const channels = Array.from({ length: 156 }, (_, ledIndex) => {
        const relativeY = (ledIndex - 77.5) / 77.5
        const color = sampleEffect(
          preset,
          createEffectContext(
            controllerFrame, 40, ledIndex, 156, unitIndex, 9, globalFrame, relativeY,
          ),
        )
        return [color.r, color.g, color.b].map((value) =>
          Math.floor(Math.min(1, Math.max(0, value)) * 255 + 0.5))
      }).flat()
      return {
        presetIndex, globalFrame, controllerFrame, unitIndex,
        sha256: createHash('sha256').update(Buffer.from(channels)).digest('hex'),
      }
    }),
  ),
)
// 各燈台不同數值：每個可變參數 × 三種分布，記下九台實際值與週期。
// Per-unit variation: every variable parameter × distribution, with each unit's value and cycle.
const unitVariations = ids.flatMap((id) => getVariableParameters(id).flatMap((parameter) =>
  (['linear', 'skip', 'random'] as const).map((distribution) => {
    const preset: EffectPreset = {
      ...createDefaultEffectPreset(id),
      unitVariation: { parameter: parameter.key, from: parameter.max, to: parameter.min + parameter.step * 0.4, distribution },
    }
    return {
      preset,
      values: Array.from({ length: 9 }, (_, unitIndex) => presetForUnit(preset, unitIndex, 9).parameters[parameter.key]),
      completionFrames: getEffectCompletion(preset, 40, 9)?.frames ?? null,
    }
  })))

// 逐台淡出轉場：整段轉場（含循環接回）每格九台的兩層權重。
// Unit-by-unit fade: both layers' weight per unit for every frame of the transition, including the loop back.
const fadeSteps = [createSequenceStep(1, createDefaultEffectPreset('full-on')), createSequenceStep(2, createDefaultEffectPreset('full-on'))]
fadeSteps[0].playback = { kind: 'time', seconds: 1 }
fadeSteps[0].transition = { kind: 'unit-fade-black', seconds: 1.3 }
fadeSteps[1].playback = { kind: 'time', seconds: 2 }
// 接回第一段用逐台呼吸後淡出。 / The loop back uses unit-breathe-black.
fadeSteps[1].transition = { kind: 'unit-breathe-black', seconds: 0.5 }
const fadeSequence = { schemaVersion: 1 as const, ending: { kind: 'loop' as const }, steps: fadeSteps }
const fadeSchedule = compileSequence(fadeSteps, 40, 9, fadeSequence.ending)
const unitFade = {
  sequence: fadeSequence,
  frames: [...Array.from({ length: 56 }, (_, index) => 38 + index), ...Array.from({ length: 24 }, (_, index) => 118 + index)]
    .map((frame) => ({
      frame,
      weights: resolveSequenceFrame(fadeSchedule, frame)!.layers.map((layer) => (
        Array.from({ length: 9 }, (_, unit) => layerUnitWeight(layer, unit, 9))
      )),
    })),
}

// 中途切換方向：不同效果、方向、交替與否，在切換前、過渡中、切換後取樣。
// Gradient axis change: several effects, axes and repeat modes, sampled before, during and after the switch.
const axisChangePresets: EffectPreset[] = ([
  ['full-on', 'strip', { to: 'unit', atSeconds: 1, blendSeconds: 0.5, repeat: false }],
  ['sine-swell', 'unit', { to: 'radius', atSeconds: 0.5, blendSeconds: 0.3, repeat: true }],
  ['spectrum-cycle', 'radius', { to: 'strip', atSeconds: 1.5, blendSeconds: 1.1, repeat: true }],
  ['color-breathing', 'strip', { to: 'radius', atSeconds: 0.75, blendSeconds: 0, repeat: false }],
  ['twenty-beat', 'radius', { to: 'unit', atSeconds: 0.5, blendSeconds: 0, repeat: true }],
  ['random-fill', 'unit', { to: 'strip', atSeconds: 1, blendSeconds: 1, repeat: false }],
] as const).map(([id, axis, axisChange]) => ({
  ...createDefaultEffectPreset(id),
  color: { mode: 'gradient', axis, axisChange: { ...axisChange }, colors: gradient8.slice(0, 5) },
}))
const axisChanges = axisChangePresets.map((preset) => ({
  preset,
  samples: [0, 19, 20, 30, 39, 40, 52, 60, 61, 79, 100, 123, 400].flatMap((elapsedFrame) =>
    [[0, 0], [40, 3], [77, 8], [155, 5]].map(([ledIndex, unitIndex]) => ({
      elapsedFrame, ledIndex, unitIndex,
      expected: sampleEffect(
        preset,
        createEffectContext(elapsedFrame + unitIndex * 2, 40, ledIndex, 156, unitIndex, 9, elapsedFrame, 0.3),
        elapsedFrame,
      ),
    }))),
}))

// 整台效果：兩輪內每一格、九台（或八台）× 幾顆代表燈點的亮度位元組，整段取 sha256。
// Whole-unit effects: brightness bytes for every frame of two cycles, all units × a few LEDs, hashed once.
const UNIT_EFFECT_LEDS = [0, 1, 2, 38, 77, 78, 117, 153, 154, 155]
const unitEffectCases: [EffectPreset, number][] = [
  [createDefaultEffectPreset('unit-chase'), 9],
  [{ ...createDefaultEffectPreset('unit-chase'), parameters: { stepSeconds: 0.3, fadeSeconds: 0.2, gapSeconds: 0, holdSeconds: 0.3, endDarkSeconds: 0 } }, 8],
  [createDefaultEffectPreset('unit-random'), 9],
  [{ ...createDefaultEffectPreset('unit-random'), parameters: { fadeInSeconds: 0.3, holdSeconds: 0.5, fadeOutSeconds: 0.4, gapSeconds: 0 } }, 9],
  [createDefaultEffectPreset('roulette-ring'), 9],
  [{ ...createDefaultEffectPreset('roulette-ring'), parameters: {
    ...createDefaultEffectPreset('roulette-ring').parameters,
    sweeps: 1, startStepFrames: 1, endStepSeconds: 0.3, dimSeconds: 1, spreadStepSeconds: 0,
    glitchSeconds: 0.5, glitchSpreadSeconds: 0.5, flickerFrames: 3,
  } }, 8],
  // 本案 24 台轉燈。 / The museum installation's 24 spinners.
  [createDefaultEffectPreset('unit-chase'), 24],
  [createDefaultEffectPreset('unit-random'), 24],
  [createDefaultEffectPreset('roulette-ring'), 24],
  [createDefaultEffectPreset('sequential-center-fill'), 24],
  [createDefaultEffectPreset('random-fill'), 24],
  // 依柱子點亮：預設與逐漸加速。 / pillar-fill: default and accelerating.
  [createDefaultEffectPreset('pillar-fill'), 24],
  [{ ...createDefaultEffectPreset('pillar-fill'), parameters: { ...createDefaultEffectPreset('pillar-fill').parameters, acceleration: 2.5, fadeInSeconds: 0.15 } }, 24],
  // 隨機填滿錯落開始。 / random-fill with a staggered start.
  [{ ...createDefaultEffectPreset('random-fill'), parameters: {
    fillSeconds: 5, acceleration: 3.5, ledFadeSeconds: 0.25, holdSeconds: 6, fadeOutSeconds: 0.5, staggerSeconds: 2,
  } }, 9],
  // 煙火上升（流星）：預設，以及由上往下、依拖尾變色、全碎裂。 / Meteor: default, and falling with trail colour and full randomness.
  [createDefaultEffectPreset('firework-rise'), 9],
  [{ ...createDefaultEffectPreset('firework-rise'), parameters: {
    ...createDefaultEffectPreset('firework-rise').parameters, direction: 1, trailColor: 1, randomness: 1, gapSeconds: 0,
  }, color: { mode: 'gradient', axis: 'radius', colors: [{ r: 1, g: 1, b: 1 }, { r: 1, g: 0.7, b: 0.1 }, { r: 0.8, g: 0, b: 0 }] } }, 9],
]
const unitEffects = unitEffectCases.map(([preset, unitCount]) => {
  const totalFrames = 2 * getEffectCompletion(preset, 40, unitCount)!.frames
  const bytes: number[] = []
  for (let frame = 0; frame < totalFrames; frame += 1) {
    for (let unitIndex = 0; unitIndex < unitCount; unitIndex += 1) {
      for (const ledIndex of UNIT_EFFECT_LEDS) {
        const color = sampleEffect(preset, createEffectContext(frame, 40, ledIndex, 156, unitIndex, unitCount, frame))
        bytes.push(...[color.r, color.g, color.b].map((value) => Math.floor(Math.min(1, Math.max(0, value)) * 255 + 0.5)))
      }
    }
  }
  return { preset, unitCount, leds: UNIT_EFFECT_LEDS, totalFrames, sha256: createHash('sha256').update(Buffer.from(bytes)).digest('hex') }
})

// 雨天線條：各效果預設值、各 fps，取開頭與較後段的影格、三台、全部 LED 的 RGB 位元組雜湊。
// Rain lines: per effect default and fps, a hash of RGB bytes over early and later frames, three units, every LED.
const RAIN_FRAME_RANGES = [[0, 120], [2000, 2040]] as const
const RAIN_UNITS = [0, 4, 8]
const rainLines = {
  frameRanges: RAIN_FRAME_RANGES,
  units: RAIN_UNITS,
  cases: ids.flatMap((effectId) => [40, 60].map((fps) => {
    const preset = createDefaultEffectPreset(effectId)
    const bytes: number[] = []
    for (const [from, to] of RAIN_FRAME_RANGES) {
      for (let frame = from; frame < to; frame += 1) {
        for (const unitIndex of RAIN_UNITS) {
          for (let ledIndex = 0; ledIndex < 156; ledIndex += 1) {
            const color = sampleEffect(preset, createEffectContext(frame, fps, ledIndex, 156, unitIndex, 9, frame), frame, true)
            bytes.push(...[color.r, color.g, color.b].map((value) => Math.floor(Math.min(1, Math.max(0, value)) * 255 + 0.5)))
          }
        }
      }
    }
    return { effectId, fps, sha256: createHash('sha256').update(Buffer.from(bytes)).digest('hex') }
  })),
}

// 海浪分界：範例場地配置的 24 台轉燈座標、幾個影格與燈點（含兩色與純色）。
// wave-split with the example layout's 24 spinner positions, a few frames and LEDs (two colours and solid too).
const waveLayout = exampleSiteLayout()
const wavePresets: EffectPreset[] = [
  createDefaultEffectPreset('wave-split'),
  { ...createDefaultEffectPreset('wave-split'), parameters: { ...createDefaultEffectPreset('wave-split').parameters, directionDegrees: 120, chop: 0, softnessCm: 0, levelMeters: 5.5 },
    color: { mode: 'gradient', axis: 'radius', colors: [{ r: 0.9, g: 0.2, b: 0.1 }, { r: 0, g: 0.6, b: 0.9 }] } },
  { ...createDefaultEffectPreset('wave-split'), color: { mode: 'solid', color: { r: 0.1, g: 0.5, b: 1 } } },
]
const wavePositions = {
  positions: waveLayout.spinners,
  presets: wavePresets,
  samples: wavePresets.flatMap((preset, presetIndex) => [0, 37, 400, 1234].flatMap((frame) =>
    waveLayout.spinners.flatMap((_, unitIndex) => [0, 20, 40, 60, 64, 90, 128].map((ledIndex) => {
      const context = createEffectContext(frame, 40, ledIndex, 129, unitIndex, waveLayout.spinners.length, frame)
      context.unitPosition = waveLayout.spinners[unitIndex] ?? undefined
      return { presetIndex, frame, unitIndex, ledIndex, expected: sampleEffect(preset, context, frame) }
    })))),
}

// 中心擴散的水平流動：範例配置 24 台的平面位置（0–1）、漸層（含加速）、純色與關閉。
// center-out-sine horizontal flow: the example layout's 24 plan positions (0–1), gradient (with acceleration), solid, off.
const flowGeometry = fixtureGeometry(waveLayout.spinners)
const flowBase = createDefaultEffectPreset('center-out-sine')
const flowPresets: EffectPreset[] = [
  { ...flowBase, parameters: { ...flowBase.parameters, horizontalFlow: 1 },
    color: { mode: 'gradient', axis: 'radius', colors: [{ r: 1, g: 0.2, b: 0.1 }, { r: 0.1, g: 0.9, b: 0.3 }, { r: 0.2, g: 0.3, b: 1 }] } },
  { ...flowBase, parameters: { ...flowBase.parameters, horizontalFlow: 1, flowDirectionDegrees: 35, flowBands: 2.5,
    flowAcceleration: 3.5, flowRampSeconds: 6, flowDepth: 0.8, flowSpeedHz: 0.34 } },
  { ...flowBase, parameters: { ...flowBase.parameters, horizontalFlow: 1, flowDirectionDegrees: 200, flowAcceleration: 2 },
    color: { mode: 'solid', color: { r: 0.9, g: 0.4, b: 0.1 } } },
  flowBase,
]
const flowPositions = {
  positions: flowGeometry.map(([, x, y]) => ({ x, y })),
  presets: flowPresets,
  samples: flowPresets.flatMap((preset, presetIndex) => [0, 37, 300, 1234].flatMap((frame) =>
    flowGeometry.flatMap(([, x, y], unitIndex) => [0, 32, 64, 100].map((ledIndex) => {
      const context = createEffectContext(frame, 40, ledIndex, 129, unitIndex, flowGeometry.length, frame)
      context.unitFlowPosition = { x, y }
      return { presetIndex, frame, unitIndex, ledIndex, expected: sampleEffect(preset, context, frame) }
    })))),
  completions: flowPresets.map((preset) => getEffectCompletion(preset, 40, 24)?.frames ?? null),
}

const output = JSON.stringify({
  source: 'frontend/src/effects',
  schemaVersion: 2,
  ledCount: 156,
  controllerFps: 40,
  presets,
  samples,
  frames,
  unitVariations,
  unitFade,
  axisChanges,
  unitEffects,
  rainLines,
  wavePositions,
  flowPositions,
}, null, 2)

if (process.argv.includes('--write')) {
  writeFileSync(new URL('../../backend/fixtures/effects_v2.json', import.meta.url), `${output}\n`)
} else {
  console.log(output)
}
