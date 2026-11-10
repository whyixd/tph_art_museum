import { describe, expect, it } from 'vitest'
import {
  addBeat, barOf, computePeaks, finishBeatMap, isDownbeat, medianTempo, moveBeat, nearestBeat, removeBeat, setBeatsPerBar,
  setDownbeat, startAtBeat,
} from '../src/simulation/beatEdit'
import type { BeatMap } from '../src/simulation/show'

const MAP: BeatMap = {
  id: 'song', title: 'Song', file: 'song.wav', durationSeconds: 4, tempo: 60, beatsPerBar: 4, firstDownbeat: 1,
  beats: [0.5, 1.5, 2.5, 3.5],
}

describe('beat corrections', () => {
  it('adds, removes and moves beats', () => {
    expect(addBeat(MAP, 1)).toEqual({ map: { ...MAP, beats: [0.5, 1, 1.5, 2.5, 3.5] }, index: 1 })
    expect(addBeat(MAP, 1.51)).toBeNull()
    expect(addBeat(MAP, 4.5)).toBeNull()
    expect(removeBeat(MAP, 0).beats).toEqual([1.5, 2.5, 3.5])
    expect(moveBeat(MAP, 1, 1.7).beats).toEqual([0.5, 1.7, 2.5, 3.5])
    // 不能越過鄰拍。 / Kept between the neighbours.
    expect(moveBeat(MAP, 1, 9).beats[1]).toBeCloseTo(2.48)
    expect(moveBeat(MAP, 0, -1).beats[0]).toBe(0)
    expect(MAP.beats).toEqual([0.5, 1.5, 2.5, 3.5])
  })

  it('finds the nearest beat', () => {
    expect(nearestBeat(MAP.beats, 0)).toBe(0)
    expect(nearestBeat(MAP.beats, 1.9)).toBe(1)
    expect(nearestBeat(MAP.beats, 2.1)).toBe(2)
    expect(nearestBeat(MAP.beats, 9)).toBe(3)
    expect(nearestBeat([], 1)).toBeNull()
  })

  it('places bars from the downbeat', () => {
    expect(isDownbeat(MAP, 1)).toBe(true)
    expect(isDownbeat(MAP, 0)).toBe(false)
    expect(barOf(MAP, 0)).toBe(0)
    expect(barOf(MAP, 1)).toBe(1)
    expect(startAtBeat(MAP, 1)).toEqual({ bar: 1 })
    expect(startAtBeat(MAP, 3)).toEqual({ bar: 1, beat: 3 })
    expect(startAtBeat(MAP, 0)).toEqual({ bar: 0, beat: 4 })
    expect(setDownbeat(MAP, 6).firstDownbeat).toBe(2)
    expect(setBeatsPerBar(MAP, 3)).toMatchObject({ beatsPerBar: 3, firstDownbeat: 1 })
  })

  it('recomputes the tempo and peaks', () => {
    expect(medianTempo([0, 0.5, 1, 1.6])).toBe(120)
    expect(finishBeatMap(MAP)).toMatchObject({ tempo: 60, source: '前端手動修正（M14）' })
    const peaks = computePeaks([new Float32Array([0, 0.5, -0.8, 0.1]), new Float32Array([0.9, 0, 0, 0])], 4, 2)
    expect(peaks.perSecond).toBe(2)
    expect(Array.from(peaks.values).map((value) => Math.round(value * 10) / 10)).toEqual([0.9, 0.8])
  })
})
