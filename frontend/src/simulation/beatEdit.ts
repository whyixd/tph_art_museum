import type { BeatMap, ShowStart } from './show'

// 拍點修正（M14）：純函式，回傳新的拍點檔，不改原物件。
// Beat corrections (M14): pure functions returning a new beat map.

/** 兩拍至少相隔（秒）。 / Minimum spacing between beats, in seconds. */
export const MIN_BEAT_GAP = 0.02

function round(seconds: number): number {
  return Math.round(seconds * 10000) / 10000
}

export function nearestBeat(beats: readonly number[], seconds: number): number | null {
  if (beats.length === 0) return null
  let low = 0
  let high = beats.length - 1
  while (low < high) {
    const middle = (low + high) >> 1
    if (beats[middle] < seconds) low = middle + 1
    else high = middle
  }
  return low > 0 && seconds - beats[low - 1] < beats[low] - seconds ? low - 1 : low
}

/** 新增一拍；離現有拍點太近時不加（回傳 null）。 / Add a beat; null when too close to an existing one. */
export function addBeat(map: BeatMap, seconds: number): { map: BeatMap; index: number } | null {
  const time = round(seconds)
  if (time < 0 || time > map.durationSeconds) return null
  const near = nearestBeat(map.beats, time)
  if (near !== null && Math.abs(map.beats[near] - time) < MIN_BEAT_GAP) return null
  const beats = [...map.beats, time].sort((a, b) => a - b)
  return { map: { ...map, beats }, index: beats.indexOf(time) }
}

export function removeBeat(map: BeatMap, index: number): BeatMap {
  return { ...map, beats: map.beats.filter((_, position) => position !== index) }
}

/** 移動一拍，夾在前後拍之間。 / Move a beat, kept between its neighbours. */
export function moveBeat(map: BeatMap, index: number, seconds: number): BeatMap {
  const low = index > 0 ? map.beats[index - 1] + MIN_BEAT_GAP : 0
  const high = index + 1 < map.beats.length ? map.beats[index + 1] - MIN_BEAT_GAP : map.durationSeconds
  if (low > high) return map
  const beats = [...map.beats]
  beats[index] = round(Math.min(high, Math.max(low, seconds)))
  return { ...map, beats }
}

/** 把某一拍設為小節第一拍（之後每 beatsPerBar 拍一小節）。 / Make a beat a downbeat (bars every beatsPerBar beats). */
export function setDownbeat(map: BeatMap, index: number): BeatMap {
  return { ...map, firstDownbeat: ((index % map.beatsPerBar) + map.beatsPerBar) % map.beatsPerBar }
}

export function setBeatsPerBar(map: BeatMap, beatsPerBar: number): BeatMap {
  const value = Math.min(16, Math.max(1, Math.round(beatsPerBar)))
  return { ...map, beatsPerBar: value, firstDownbeat: map.firstDownbeat % value }
}

export function isDownbeat(map: BeatMap, index: number): boolean {
  return ((index - map.firstDownbeat) % map.beatsPerBar + map.beatsPerBar) % map.beatsPerBar === 0
}

/** 小節編號（從 1 起；弱起為 0）。 / Bar number (from 1; the pickup is 0). */
export function barOf(map: BeatMap, index: number): number {
  return Math.floor((index - map.firstDownbeat) / map.beatsPerBar) + 1
}

/** 某一拍對應的段落開始位置。 / The section start at a beat. */
export function startAtBeat(map: BeatMap, index: number): ShowStart {
  const beat = ((index - map.firstDownbeat) % map.beatsPerBar + map.beatsPerBar) % map.beatsPerBar + 1
  return beat === 1 ? { bar: barOf(map, index) } : { bar: barOf(map, index), beat }
}

/** 拍距中位數換算的速度。 / Tempo from the median beat interval. */
export function medianTempo(beats: readonly number[]): number | null {
  if (beats.length < 2) return null
  const gaps = beats.slice(1).map((beat, index) => beat - beats[index]).sort((a, b) => a - b)
  const middle = gaps.length >> 1
  const gap = gaps.length % 2 ? gaps[middle] : (gaps[middle - 1] + gaps[middle]) / 2
  return Math.round((60 / gap) * 1000) / 1000
}

/** 儲存前整理：重算速度、記錄來源。 / Before saving: recompute the tempo and record the source. */
export function finishBeatMap(map: BeatMap): BeatMap {
  return { ...map, tempo: medianTempo(map.beats), source: '前端手動修正（M14）' }
}

export type Peaks = { perSecond: number; values: Float32Array }

/** 波形峰值：每秒 perSecond 個，取各聲道絕對值的最大值。 / Waveform peaks: max |sample| over channels, perSecond per second. */
export function computePeaks(channels: readonly Float32Array[], sampleRate: number, perSecond = 200): Peaks {
  const length = channels[0]?.length ?? 0
  const size = Math.max(1, Math.round(sampleRate / perSecond))
  const values = new Float32Array(Math.ceil(length / size))
  for (const data of channels) {
    for (let bucket = 0; bucket < values.length; bucket += 1) {
      let peak = values[bucket]
      const end = Math.min(length, (bucket + 1) * size)
      for (let index = bucket * size; index < end; index += 1) {
        const value = Math.abs(data[index])
        if (value > peak) peak = value
      }
      values[bucket] = peak
    }
  }
  return { perSecond: sampleRate / size, values }
}
