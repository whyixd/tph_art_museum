import type { EffectPreset } from '../effects'
import type { CompiledSequence, CompiledStep, StepFixtures, TransitionKind, UnitOverride } from './sequence'

// 演出（Show，M12）：一首曲目＋依小節／拍／秒定位的段落，編譯成與串接相同的影格表（與後端 show.py 一致）。
// A show (M12): one track plus sections placed by bar / beat / seconds, compiled to the same frame schedule as a
// sequence (mirrors backend show.py).

export type BeatMap = {
  id: string
  title: string
  file: string | null
  durationSeconds: number
  tempo: number | null
  beatsPerBar: number
  firstDownbeat: number
  beats: number[]
  source?: string
}

/** 開始位置：秒數，或第幾小節（從 1 起；0 = 弱起拍）第幾拍（從 1 起）。 / Seconds, or a bar (from 1; 0 = pickup) and beat (from 1). */
export type ShowStart = { seconds: number } | { bar: number; beat?: number }
export type ShowTransition =
  | { kind: TransitionKind; seconds: number }
  | { kind: TransitionKind; beats: number }
export type ShowEnding = { kind: 'loop' } | { kind: 'fade-black'; seconds: number }

/** 前端內部形狀：轉燈 preset 在 `preset`，球燈／洗牆燈在 `fixtures`（同串接）。 / Internal shape, as sequence steps. */
export type ShowSection = {
  id: number
  start: ShowStart
  preset: EffectPreset
  fixtures?: StepFixtures
  /** 某幾台轉燈改用別的效果。 / Spinners that play another effect. */
  overrides?: UnitOverride[]
  /** 進入這一段的轉場。 / The transition into this section. */
  transition: ShowTransition
}

export type ShowDocument = {
  schemaVersion: 1
  id: string
  title: string
  trackId: string
  ending: ShowEnding
  sections: ShowSection[]
}

export type CompiledShow = CompiledSequence & {
  durationSeconds: number
  loop: boolean
  /** 每段的開始秒數。 / Each section's start in seconds. */
  startSeconds: number[]
}

export function beatIndex(beatMap: BeatMap, bar: number, beat = 1): number {
  return beatMap.firstDownbeat + (bar - 1) * beatMap.beatsPerBar + (beat - 1)
}

export function startSeconds(start: ShowStart, beatMap: BeatMap): number {
  if ('seconds' in start) return start.seconds
  const beat = start.beat ?? 1
  if (beat > beatMap.beatsPerBar) throw new Error(`第 ${beat} 拍超過每小節 ${beatMap.beatsPerBar} 拍`)
  const index = beatIndex(beatMap, start.bar, beat)
  if (index < 0 || index >= beatMap.beats.length) throw new Error(`第 ${start.bar} 小節第 ${beat} 拍不在曲子的拍點內`)
  return beatMap.beats[index]
}

function bisectRight(values: readonly number[], target: number): number {
  let low = 0
  let high = values.length
  while (low < high) {
    const middle = (low + high) >> 1
    if (values[middle] <= target) low = middle + 1
    else high = middle
  }
  return low
}

/** 時間對應的拍位置（小數）：拍點之間線性，兩端沿用最近的間隔。 / Fractional beat index; edges extend the nearest interval. */
export function beatPosition(beats: readonly number[], seconds: number): number {
  const index = Math.min(Math.max(bisectRight(beats, seconds) - 1, 0), beats.length - 2)
  return index + (seconds - beats[index]) / (beats[index + 1] - beats[index])
}

export function beatTime(beats: readonly number[], position: number): number {
  const index = Math.min(Math.max(Math.floor(position), 0), beats.length - 2)
  return beats[index] + (position - index) * (beats[index + 1] - beats[index])
}

export function transitionSeconds(transition: ShowTransition, beatMap: BeatMap, at: number): number {
  if ('seconds' in transition) return transition.seconds
  if (beatMap.beats.length < 2) throw new Error('以拍數指定轉場需要有拍點的曲子')
  return beatTime(beatMap.beats, beatPosition(beatMap.beats, at) + transition.beats) - at
}

/** 與後端 compile_show 相同的影格表；位置不合法時丟出錯誤（中文訊息）。 / Same schedule as compile_show; throws on bad positions. */
export function compileShow(show: ShowDocument, beatMap: BeatMap, fps: number): CompiledShow {
  if (show.trackId !== beatMap.id) throw new Error('拍點檔不屬於這首曲目')
  if (show.sections.length === 0) throw new Error('演出至少要有一段')
  const duration = beatMap.durationSeconds
  const totalFrames = Math.max(1, Math.ceil(duration * fps))
  const starts = show.sections.map((section) => startSeconds(section.start, beatMap))
  if (starts[0] !== 0) throw new Error('第一段必須從 0 秒開始')
  const frames = starts.map((seconds) => Math.round(seconds * fps))
  for (let index = 1; index < frames.length; index += 1) {
    if (starts[index] >= duration || frames[index] >= totalFrames) {
      throw new Error(`第 ${index + 1} 段的開始（${starts[index].toFixed(2)} 秒）超過曲長 ${duration.toFixed(2)} 秒`)
    }
    if (frames[index] <= frames[index - 1]) throw new Error(`第 ${index + 1} 段必須在第 ${index} 段之後`)
  }
  const loop = show.ending.kind === 'loop'
  const steps = show.sections.map((section, index): CompiledStep => {
    const end = index + 1 < frames.length ? frames[index + 1] : totalFrames
    const durationFrames = end - frames[index]
    const incoming = index > 0 && section.transition.kind !== 'cut'
      ? Math.min(durationFrames, Math.max(0, Math.round(transitionSeconds(section.transition, beatMap, starts[index]) * fps)))
      : 0
    const following = index + 1 < show.sections.length ? show.sections[index + 1] : loop ? show.sections[0] : null
    return {
      id: section.id,
      preset: section.preset,
      ...(section.fixtures ? { fixtures: section.fixtures } : {}),
      ...(section.overrides?.length ? { overrides: section.overrides } : {}),
      playback: { kind: 'time', seconds: durationFrames / fps },
      // 和串接一樣，轉場種類記在前一段。 / As in a sequence, the transition kind is stored on the outgoing step.
      transition: { kind: following?.transition.kind ?? 'cut', seconds: 0 },
      index,
      startFrame: frames[index],
      durationFrames,
      cycleFrames: null,
      groupCycles: { spinner: null, ball: null, wash: null },
      transitionFrames: incoming,
    }
  })
  const first = show.sections[0].transition
  return {
    steps,
    totalFrames,
    ending: loop ? { kind: 'loop' } : { kind: 'fade-black', seconds: show.ending.kind === 'fade-black' ? show.ending.seconds : 0 },
    endFadeFrames: show.ending.kind === 'fade-black' ? Math.max(1, Math.round(show.ending.seconds * fps)) : 0,
    loopTransitionFrames: loop && first.kind !== 'cut'
      ? Math.min(steps[0].durationFrames, Math.max(0, Math.round(transitionSeconds(first, beatMap, 0) * fps)))
      : 0,
    durationSeconds: duration,
    loop,
    startSeconds: starts,
  }
}

/** 音樂時間對應的影格（小數）；循環時每一輪重新對齊曲長。 / Fractional frame at a music time; loops realign every round. */
export function showFrameAt(show: CompiledShow, seconds: number, fps: number): number {
  const time = Math.max(0, seconds)
  if (!show.loop) return time * fps
  const rounds = Math.floor(time / show.durationSeconds)
  const within = time - rounds * show.durationSeconds
  return rounds * show.totalFrames + Math.min(show.totalFrames - 1e-6, within * fps)
}

/** 某個時間是第幾小節第幾拍（從 1 起；弱起為第 0 小節）。 / Bar and beat at a time (from 1; the pickup is bar 0). */
export function barBeatAt(beatMap: BeatMap, seconds: number): { bar: number; beat: number } | null {
  const index = bisectRight(beatMap.beats, seconds) - 1
  if (index < 0) return null
  const relative = index - beatMap.firstDownbeat
  return {
    bar: Math.floor(relative / beatMap.beatsPerBar) + 1,
    beat: ((relative % beatMap.beatsPerBar) + beatMap.beatsPerBar) % beatMap.beatsPerBar + 1,
  }
}

export function describeStart(start: ShowStart): string {
  return 'seconds' in start ? `${start.seconds.toFixed(2)} 秒` : `第 ${start.bar} 小節${start.beat && start.beat > 1 ? `第 ${start.beat} 拍` : ''}`
}
