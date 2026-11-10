import { describe, expect, it } from 'vitest'
import { fromBackendShow, toBackendShow } from '../src/backendApi'
import { createDefaultEffectPreset } from '../src/effects'
import { resolveSequenceFrame } from '../src/simulation/sequence'
import {
  barBeatAt, compileShow, showFrameAt, startSeconds, transitionSeconds, type BeatMap, type ShowDocument,
} from '../src/simulation/show'

// 與後端 test_show.py 相同的節拍器向量：120 BPM、4/4、拍點 0.25 + 0.5 k 秒、60 秒。
// The same metronome vectors as backend test_show.py: 120 BPM, 4/4, beats at 0.25 + 0.5 k s, 60 s.
const CLICK: BeatMap = {
  id: 'click-120', title: '節拍器', file: null, durationSeconds: 60, tempo: 120, beatsPerBar: 4, firstDownbeat: 0,
  beats: Array.from({ length: 120 }, (_, index) => 0.25 + index * 0.5),
}

function show(overrides: Partial<ShowDocument> = {}): ShowDocument {
  return {
    schemaVersion: 1, id: 'demo', title: 'Demo', trackId: 'click-120', ending: { kind: 'loop' },
    sections: [
      { id: 1, start: { seconds: 0 }, preset: createDefaultEffectPreset('full-on'), transition: { kind: 'crossfade', beats: 1 } },
      { id: 2, start: { bar: 2 }, preset: createDefaultEffectPreset('center-out-sine'), transition: { kind: 'crossfade', beats: 2 } },
      { id: 3, start: { bar: 3, beat: 3 }, preset: createDefaultEffectPreset('full-on'), transition: { kind: 'cut', seconds: 0 } },
    ],
    ...overrides,
  }
}

describe('show compile (mirrors backend compile_show)', () => {
  it('places sections on bars and beats', () => {
    const compiled = compileShow(show(), CLICK, 40)
    expect(compiled.steps.map((step) => step.startFrame)).toEqual([0, 90, 210])
    expect(compiled.steps.map((step) => step.durationFrames)).toEqual([90, 120, 2400 - 210])
    expect(compiled.totalFrames).toBe(2400)
    expect(compiled.steps[1].transitionFrames).toBe(40)
    expect(compiled.steps.map((step) => step.transition.kind)).toEqual(['crossfade', 'cut', 'crossfade'])
    expect(compiled.loopTransitionFrames).toBe(20)
    expect(compiled.startSeconds).toEqual([0, 2.25, 5.25])
  })

  it('measures transitions on the beat map', () => {
    const uneven = { ...CLICK, beats: [0, 1, 1.5, 2] }
    expect(transitionSeconds({ kind: 'crossfade', beats: 2 }, uneven, 0)).toBeCloseTo(1.5)
    expect(transitionSeconds({ kind: 'crossfade', beats: 0.5 }, uneven, 1.5)).toBeCloseTo(0.25)
    expect(transitionSeconds({ kind: 'crossfade', beats: 2 }, uneven, 2)).toBeCloseTo(1)
  })

  it('rejects positions outside the track or out of order', () => {
    const [first, second, third] = show().sections
    for (const sections of [
      [{ ...first, start: { bar: 1 } }],
      [first, { ...second, start: { bar: 1, beat: 5 } }],
      [first, { ...second, start: { bar: 99 } }],
      [first, { ...second, start: { seconds: 61 } }],
      [first, third, second],
    ]) {
      expect(() => compileShow(show({ sections }), CLICK, 40)).toThrow()
    }
    expect(() => compileShow(show({ trackId: 'other' }), CLICK, 40)).toThrow()
  })

  it('crossfades like a sequence and loops on the music', () => {
    const compiled = compileShow(show(), CLICK, 40)
    const frame = resolveSequenceFrame(compiled, 90 + 19)!
    expect(frame.layers.map((layer) => layer.weight)).toEqual([21 / 41, 20 / 41])
    expect(showFrameAt(compiled, 0.5, 40)).toBe(20)
    expect(showFrameAt(compiled, 60.5, 40)).toBe(2420)
    const once = compileShow(show({ ending: { kind: 'fade-black', seconds: 1 } }), CLICK, 40)
    expect(once.endFadeFrames).toBe(40)
    expect(showFrameAt(once, 60.5, 40)).toBe(2420)
  })

  it('reports bars and beats', () => {
    expect(barBeatAt(CLICK, 0.1)).toBeNull()
    expect(barBeatAt(CLICK, 2.3)).toEqual({ bar: 2, beat: 1 })
    expect(barBeatAt(CLICK, 5.3)).toEqual({ bar: 3, beat: 3 })
    expect(startSeconds({ bar: 3, beat: 3 }, CLICK)).toBe(5.25)
  })

  it('converts sections to and from the backend shape', () => {
    const document = show()
    document.sections[1].fixtures = { ball: { schemaVersion: 1, effectId: 'fixture-solid', parameters: {}, color: { mode: 'solid', color: { r: 1, g: 1, b: 1 } } } as never }
    const backend = toBackendShow(document)
    expect(backend.sections[1]).toHaveProperty('spinner')
    expect(backend.sections[1]).toHaveProperty('ball')
    expect(fromBackendShow(backend)).toEqual(document)
  })
})
