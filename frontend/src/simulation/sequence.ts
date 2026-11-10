import { getEffectCompletion, type EffectPreset } from '../effects'
import { getFixtureCompletion, type FixturePreset } from '../fixtures'

/**
 * unit-fade-black: units fade out one after another (1→9), then the next step fades in on all units.
 * unit-breathe-black: the same, but each unit breathes once (dims and comes back) before fading out.
 */
export type TransitionKind = 'cut' | 'fade-black' | 'crossfade' | 'unit-fade-black' | 'unit-breathe-black'
export type SequenceEnding = { kind: 'hold' | 'loop' } | { kind: 'fade-black'; seconds: number }
/** Which group's cycle a count step counts (backend Sequence v2); the simulator still counts the spinner. */
export type SequenceClock = 'spinner' | 'ball' | 'wash'
export type PlaybackRule =
  | { kind: 'time'; seconds: number }
  | { kind: 'count'; count: number; clock?: SequenceClock }

/**
 * 段落中某幾台轉燈改用別的效果（燈台編號從 1 起；同一段裡一台只能屬於一個覆寫）。
 * Some spinners of a step use another effect (unit numbers from 1; a unit belongs to at most one override per step).
 */
export type UnitOverride = { units: number[]; preset: EffectPreset }

/** Ball / wall-washer presets of a step (backend Sequence v2); omitted groups are dark. Edited in M7. */
export type StepFixtures = { ball?: FixturePreset; wash?: FixturePreset }

export type SequenceStep = {
  id: number
  /** The spinner preset (Sequence v2 `spinner`). */
  preset: EffectPreset
  playback: PlaybackRule
  transition: { kind: TransitionKind; seconds: number }
  fixtures?: StepFixtures
  /** 演出段落的轉燈覆寫（串接目前不用）。 / Spinner overrides (show sections; sequences do not use them yet). */
  overrides?: UnitOverride[]
}

export type SequenceDocument = {
  schemaVersion: 1
  ending: SequenceEnding
  steps: SequenceStep[]
}

/** Frames each group wraps per repeat; only the clock group of a count step wraps (as the backend). */
export type GroupCycles = { spinner: number | null; ball: number | null; wash: number | null }

export type CompiledStep = SequenceStep & {
  index: number
  startFrame: number
  durationFrames: number
  /** The clock group's cycle (count steps), for repeat counting. */
  cycleFrames: number | null
  groupCycles: GroupCycles
  transitionFrames: number
}

/** Fixture counts for count steps clocked by fixture-chase (its cycle depends on the count). */
export type FixtureCounts = { ball: number; wash: number }

export type CompiledSequence = {
  steps: CompiledStep[]
  totalFrames: number
  ending: SequenceEnding
  endFadeFrames: number
  loopTransitionFrames: number
}

export type SequenceLayer = {
  preset: EffectPreset
  /** The step's ball / wash presets (absent = dark). */
  fixtures?: StepFixtures
  /** 這段的轉燈覆寫。 / The step's spinner overrides. */
  overrides?: UnitOverride[]
  groupCycles: GroupCycles
  stepIndex: number
  localFrame: number
  // Frames since the step started (unwrapped) and the step repeat length, for count-based wrapping.
  elapsedFrame: number
  cycleFrames: number | null
  sourceFrame: number
  weight: number
  /** Outgoing layer of unit-fade-black / unit-breathe-black: frames into the fade-out half and that half's length. */
  unitFade?: UnitFade
}

export type SequenceFrame = {
  stepIndex: number
  complete: boolean
  layers: SequenceLayer[]
}

export function defaultPlaybackRule(preset: EffectPreset): PlaybackRule {
  return getEffectCompletion(preset, 40, 9)
    ? { kind: 'count', count: 1 }
    : { kind: 'time', seconds: 8 }
}

export function createSequenceStep(id: number, preset: EffectPreset): SequenceStep {
  return {
    id,
    preset,
    playback: defaultPlaybackRule(preset),
    transition: { kind: 'cut', seconds: 0.5 },
  }
}

export function compileSequence(
  steps: readonly SequenceStep[],
  fps: number,
  unitCount: number,
  ending: SequenceEnding = { kind: 'hold' },
  fixtureCounts: FixtureCounts = { ball: 1, wash: 1 },
): CompiledSequence {
  if (!Number.isFinite(fps) || fps <= 0) throw new Error('controller FPS must be positive')
  let startFrame = 0
  const compiled = steps.map((step, index): CompiledStep => {
    // 以 clock 群組的週期計次（預設轉燈），與後端相同。 / Count by the clock group's cycle (spinner by default), as the backend.
    const clock = step.playback.kind === 'count' ? step.playback.clock ?? 'spinner' : null
    const clockPreset = clock === 'ball' || clock === 'wash' ? step.fixtures?.[clock] : undefined
    const completion = clock === 'ball' || clock === 'wash'
      ? clockPreset ? getFixtureCompletion(clockPreset, fps, Math.max(1, fixtureCounts[clock])) : null
      : getEffectCompletion(step.preset, fps, unitCount)?.frames ?? null
    if (step.playback.kind === 'count' && !completion) {
      throw new Error('播放次數需要完成邊界')
    }
    const cycleFrames = step.playback.kind === 'count' ? completion! : null
    const groupCycles: GroupCycles = {
      spinner: clock === 'spinner' ? cycleFrames : null,
      ball: clock === 'ball' ? cycleFrames : null,
      wash: clock === 'wash' ? cycleFrames : null,
    }
    const durationFrames = step.playback.kind === 'count'
      ? cycleFrames! * Math.max(1, Math.floor(step.playback.count))
      : Math.max(1, Math.round(step.playback.seconds * fps))
    const previous = steps[index - 1]
    const transitionFrames = previous?.transition.kind === 'cut'
      ? 0
      : Math.min(durationFrames, Math.max(0, Math.round((previous?.transition.seconds ?? 0) * fps)))
    const result = {
      ...step,
      index,
      startFrame,
      durationFrames,
      cycleFrames,
      groupCycles,
      transitionFrames,
    }
    startFrame += durationFrames
    return result
  })
  const last = steps.at(-1)
  const loopTransitionFrames = ending.kind === 'loop' && last?.transition.kind !== 'cut'
    ? Math.min(compiled[0]?.durationFrames ?? 0, Math.max(0, Math.round((last?.transition.seconds ?? 0) * fps)))
    : 0
  const endFadeFrames = ending.kind === 'fade-black'
    ? Math.max(1, Math.round(ending.seconds * fps))
    : 0
  return { steps: compiled, totalFrames: startFrame, ending, endFadeFrames, loopTransitionFrames }
}

function localFrame(step: CompiledStep, frame: number): number {
  const cycle = step.groupCycles.spinner
  return cycle === null ? frame : frame % cycle
}

function stepLayer(step: CompiledStep): Pick<SequenceLayer, 'preset' | 'fixtures' | 'overrides' | 'groupCycles' | 'stepIndex' | 'cycleFrames'> {
  return {
    preset: step.preset,
    ...(step.fixtures ? { fixtures: step.fixtures } : {}),
    ...(step.overrides?.length ? { overrides: step.overrides } : {}),
    groupCycles: step.groupCycles,
    stepIndex: step.index,
    cycleFrames: step.groupCycles.spinner,
  }
}

export function resolveSequenceFrame(
  sequence: CompiledSequence,
  globalFrame: number,
): SequenceFrame | null {
  if (sequence.steps.length === 0) return null
  const frame = Math.max(0, Math.floor(globalFrame))
  const loop = sequence.ending.kind === 'loop'
  const cycleStart = loop ? Math.floor(frame / sequence.totalFrames) * sequence.totalFrames : 0
  const cycleFrame = loop ? frame - cycleStart : frame
  const complete = !loop && frame >= sequence.totalFrames
  if (complete) {
    const last = sequence.steps.at(-1)!
    const weight = sequence.ending.kind === 'fade-black'
      ? Math.max(0, 1 - (frame - sequence.totalFrames + 1) / sequence.endFadeFrames)
      : 1
    return {
      stepIndex: last.index,
      complete: true,
      layers: [{
        ...stepLayer(last),
        localFrame: localFrame(last, last.durationFrames - 1),
        elapsedFrame: last.durationFrames - 1,
        sourceFrame: sequence.totalFrames - 1,
        weight,
      }],
    }
  }

  const stepIndex = sequence.steps.findIndex((step) => cycleFrame < step.startFrame + step.durationFrames)
  const step = sequence.steps[stepIndex]
  const elapsed = cycleFrame - step.startFrame
  const current: SequenceLayer = {
    ...stepLayer(step),
    localFrame: localFrame(step, elapsed),
    elapsedFrame: elapsed,
    sourceFrame: cycleStart + step.startFrame + elapsed,
    weight: 1,
  }
  const transitionFrames = stepIndex === 0
    ? cycleStart > 0 ? sequence.loopTransitionFrames : 0
    : step.transitionFrames
  if (elapsed >= transitionFrames) {
    return { stepIndex, complete: false, layers: [current] }
  }

  const previous = sequence.steps[stepIndex === 0 ? sequence.steps.length - 1 : stepIndex - 1]
  const midpoint = Math.floor((transitionFrames - 1) / 2)
  if (previous.transition.kind === 'crossfade') {
    const progress = (elapsed + 1) / (transitionFrames + 1)
    current.weight = progress
  } else {
    current.weight = elapsed > midpoint
      ? (elapsed - midpoint) / (transitionFrames - 1 - midpoint)
      : 0
  }
  const unitFade = previous.transition.kind === 'unit-fade-black' || previous.transition.kind === 'unit-breathe-black'
  const outgoingWeight = previous.transition.kind === 'crossfade'
    ? 1 - current.weight
    : unitFade
      ? elapsed <= midpoint ? 1 : 0
      : elapsed < midpoint
        ? 1 - (elapsed + 1) / (midpoint + 1)
        : 0
  const outgoing: SequenceLayer = {
    ...stepLayer(previous),
    localFrame: localFrame(previous, previous.durationFrames - 1),
    elapsedFrame: previous.durationFrames - 1,
    sourceFrame: cycleStart + previous.startFrame + previous.durationFrames - 1
      - (stepIndex === 0 ? sequence.totalFrames : 0),
    weight: outgoingWeight,
    ...(unitFade ? {
      unitFade: { elapsed, frames: midpoint + 1, ...(previous.transition.kind === 'unit-breathe-black' ? { breathe: true as const } : {}) },
    } : {}),
  }
  return { stepIndex, complete: false, layers: [outgoing, current] }
}

// 逐台淡出：每台淡出一段（前半的 1/3，台數少時較長），開始時間由第 1 台到最後一台平均錯開，最後一台在前半結束時剛好全黑。
// Per-unit fade-out: each unit fades over a third of the first half (longer with fewer units), start times spread
// evenly from the first unit to the last, and the last unit reaches black exactly at the end of the half.
//
// 逐台呼吸後淡出：每台佔前半的 1/2（呼吸要夠長才看得出來），同樣平均錯開；每台前 2/3 呼吸一次（暗到 1 - BREATH_DEPTH
// 再亮回來），後 1/3 淡出。
// Per-unit breathe then fade: each unit takes half of the first half (a breath needs time to read), spread the same
// way; its first 2/3 is one breath (down to 1 - BREATH_DEPTH and back), the last 1/3 fades out.
export type UnitFade = { elapsed: number; frames: number; breathe?: true }
export const BREATH_DEPTH = 0.85

export function unitFadeWeight(fade: UnitFade, unitIndex: number, unitCount: number): number {
  const fadeFrames = fade.frames / Math.min(fade.breathe ? 2 : 3, Math.max(1, unitCount))
  const start = unitCount <= 1 ? 0 : (fade.frames - fadeFrames) * unitIndex / (unitCount - 1)
  const progress = Math.min(1, Math.max(0, (fade.elapsed + 1 - start) / fadeFrames))
  if (!fade.breathe) return 1 - progress
  if (progress < 2 / 3) return 1 - BREATH_DEPTH * (0.5 - 0.5 * Math.cos(2 * Math.PI * progress * 1.5))
  return Math.min(1, (1 - progress) * 3)
}

/** 這台轉燈在這一層用的效果：在覆寫名單內就用覆寫的效果。 / The effect a unit plays in a layer: its override's, if any. */
export function layerPresetForUnit(layer: Pick<SequenceLayer, 'preset' | 'overrides'>, unitIndex: number): EffectPreset {
  const unit = unitIndex + 1
  return layer.overrides?.find((override) => override.units.includes(unit))?.preset ?? layer.preset
}

/** A layer's weight for one unit; only unit-fade-black and unit-breathe-black differ per unit. */
export function layerUnitWeight(layer: SequenceLayer, unitIndex: number, unitCount: number): number {
  return layer.unitFade ? layer.weight * unitFadeWeight(layer.unitFade, unitIndex, unitCount) : layer.weight
}

/** Where the simulator jumps when the preview restarts. */
export type SequenceSeek =
  | { kind: 'start' }
  | { kind: 'step'; id: number }
  | { kind: 'transition'; id: number }
  /** 指定影格（演出時間軸點選跳轉）。 / A given frame (seeking on the show timeline). */
  | { kind: 'frame'; frame: number }

// 預覽銜接從切換前這麼多秒開始。 / Transition previews start this many seconds before the switch.
export const TRANSITION_PREVIEW_LEAD_SECONDS = 2

/**
 * Frame to start the preview at: a step's start, or shortly before the switch out of a step
 * (into the next step, back into the first after the last when looping, or into the ending).
 */
export function sequenceSeekFrame(sequence: CompiledSequence, seek: SequenceSeek, fps: number): number {
  if (seek.kind === 'start') return 0
  if (seek.kind === 'frame') return Math.max(0, seek.frame)
  const step = sequence.steps.find((item) => item.id === seek.id)
  if (!step) return 0
  if (seek.kind === 'step') return step.startFrame
  // 最後一段的切換點是串接結尾：循環時接回第一段（第二輪），否則是結束方式。
  // The last step switches at the sequence end: into the first step's second round when looping, else into the ending.
  const switchFrame = step.startFrame + step.durationFrames
  const lead = Math.min(Math.round(TRANSITION_PREVIEW_LEAD_SECONDS * fps), step.durationFrames)
  return Math.max(0, switchFrame - lead)
}
