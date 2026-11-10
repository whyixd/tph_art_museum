import { getTrackAudio } from '../backendApi'
import type { BeatMap } from './show'

// 模擬預覽用的瀏覽器音樂播放（Web Audio）：整首解碼後播放，模擬時鐘直接取音樂時間。不做延遲補償。
// Browser music playback for the simulator preview (Web Audio): the whole track is decoded, and the simulator clock
// takes the music time directly. No latency compensation.

/** 內建節拍器沒有音檔，依拍點在瀏覽器合成（與後端相同：小節第一拍 1760 Hz、其他 880 Hz）。 */
function clickBuffer(context: BaseAudioContext, beatMap: BeatMap): AudioBuffer {
  const rate = context.sampleRate
  const buffer = context.createBuffer(1, Math.ceil(beatMap.durationSeconds * rate), rate)
  const data = buffer.getChannelData(0)
  const length = Math.floor(0.02 * rate)
  beatMap.beats.forEach((beat, index) => {
    const accent = (index - beatMap.firstDownbeat) % beatMap.beatsPerBar === 0
    const [frequency, amplitude] = accent ? [1760, 0.5] : [880, 0.35]
    const start = Math.round(beat * rate)
    for (let n = 0; n < length && start + n < data.length; n += 1) {
      data[start + n] = amplitude * Math.exp(-n / (0.004 * rate)) * Math.sin(2 * Math.PI * frequency * n / rate)
    }
  })
  return buffer
}

export type TrackLoad = 'ready' | 'missing'

export class TrackPlayer {
  private context: AudioContext | null = null
  private buffers = new Map<string, AudioBuffer>()
  private source: AudioBufferSourceNode | null = null
  private playing: { trackId: string; offset: number; startedAt: number; loop: boolean; duration: number } | null = null

  private audioContext(): AudioContext {
    this.context ??= new AudioContext()
    return this.context
  }

  buffer(trackId: string): AudioBuffer | null {
    return this.buffers.get(trackId) ?? null
  }

  isLoaded(trackId: string): boolean {
    return this.buffers.has(trackId)
  }

  /** 下載並解碼曲目；音檔不在主機上時回 'missing'（預覽改為無聲）。 / Fetch and decode; 'missing' when the WAV is not on the host. */
  async load(beatMap: BeatMap): Promise<TrackLoad> {
    if (this.buffers.has(beatMap.id)) return 'ready'
    const context = this.audioContext()
    if (beatMap.file === null) {
      this.buffers.set(beatMap.id, clickBuffer(context, beatMap))
      return 'ready'
    }
    const data = await getTrackAudio(beatMap.id)
    if (data === null) return 'missing'
    this.buffers.set(beatMap.id, await context.decodeAudioData(data))
    return 'ready'
  }

  /** 從某秒開始播放（需先 load）；要在使用者點擊內呼叫，瀏覽器才允許出聲。 / Call from a click so the browser allows sound. */
  play(trackId: string, fromSeconds: number, loop: boolean): boolean {
    const buffer = this.buffers.get(trackId)
    if (!buffer) return false
    this.stop()
    const context = this.audioContext()
    void context.resume()
    const source = context.createBufferSource()
    source.buffer = buffer
    source.loop = loop
    source.connect(context.destination)
    const offset = Math.min(Math.max(0, fromSeconds), buffer.duration)
    source.start(0, offset)
    this.source = source
    this.playing = { trackId, offset, startedAt: context.currentTime, loop, duration: buffer.duration }
    source.onended = () => {
      if (this.source === source) {
        this.source = null
        this.playing = null
      }
    }
    return true
  }

  stop(): void {
    const source = this.source
    this.source = null
    this.playing = null
    if (source) {
      source.onended = null
      try { source.stop() } catch { /* already stopped */ }
      source.disconnect()
    }
  }

  /** 目前播到的秒數（循環時不折回，持續累加）；沒在播放回 null。 / Seconds played (unwrapped when looping); null when idle. */
  position(): number | null {
    if (!this.playing || !this.context) return null
    return this.playing.offset + (this.context.currentTime - this.playing.startedAt)
  }

  playingTrack(): string | null {
    return this.playing?.trackId ?? null
  }
}
