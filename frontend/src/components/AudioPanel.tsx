import { useEffect, useRef, useState } from 'react'
import { getAudioState, playAudio, setAudioCalibration, stopAudio, type AudioState } from '../backendApi'

const SLIDER_RANGE_MS = 300
const LIVE_DELAY_MS = 150

function formatTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${(seconds - minutes * 60).toFixed(1).padStart(4, '0')}`
}

// 音樂播放與延遲校準：Pi 從 USB 音效卡播放，燈光跟著音訊時鐘；閃拍時全部燈隨拍點閃白。
// Music playback and latency calibration: the Pi plays on its USB sound card and the lights follow the audio clock;
// with the beat flash on, every light flashes white on the beat.
export function AudioPanel() {
  const [audio, setAudio] = useState<AudioState | null | undefined>(undefined)
  const [trackId, setTrackId] = useState('click-120')
  const [flash, setFlash] = useState(true)
  const [loop, setLoop] = useState(true)
  // 校準值只在載入、儲存、還原時取自後端，避免輪詢蓋掉正在調整的數值。
  // The offset is taken from the backend only on load, save and revert, so polling never overwrites an adjustment.
  const [offset, setOffset] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const liveTimer = useRef<number | null>(null)
  const playing = audio?.playing ?? null

  useEffect(() => {
    let active = true
    const refresh = () => {
      getAudioState().then((state) => {
        if (!active) return
        setAudio(state)
        if (state) setOffset((current) => current ?? state.calibration.offsetMs)
      }, (failure) => { if (active) setError(failure instanceof Error ? failure.message : '無法讀取音訊狀態') })
    }
    refresh()
    const timer = window.setInterval(refresh, playing ? 250 : 5_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [playing !== null])

  useEffect(() => () => { if (liveTimer.current !== null) window.clearTimeout(liveTimer.current) }, [])

  const run = async (request: () => Promise<AudioState>, takeOffset = false) => {
    setBusy(true)
    setError(null)
    try {
      const state = await request()
      setAudio(state)
      if (takeOffset) setOffset(state.calibration.offsetMs)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '音訊操作失敗')
    } finally {
      setBusy(false)
    }
  }

  if (audio === undefined && error === null) return null
  if (audio === null) return <p className="control-note">音樂播放需要較新的後端（/api/audio）。</p>

  const max = audio?.calibration.maxOffsetMs ?? 1000
  const current = offset ?? 0
  const saved = audio?.calibration.savedOffsetMs ?? 0
  const tracks = audio?.tracks ?? []
  const selected = tracks.find((track) => track.id === trackId)
  const playingTrack = playing ? tracks.find((track) => track.id === playing.trackId) : undefined

  // 拖動時即時套用（不存檔），停下 150 ms 後送出。 / Applied live (not saved) 150 ms after the last change.
  const adjust = (value: number) => {
    const next = Math.max(-max, Math.min(max, Math.round(value)))
    setOffset(next)
    if (liveTimer.current !== null) window.clearTimeout(liveTimer.current)
    liveTimer.current = window.setTimeout(() => {
      liveTimer.current = null
      setAudioCalibration(next, false).then(setAudio, (failure) =>
        setError(failure instanceof Error ? failure.message : '無法套用延遲補償'))
    }, LIVE_DELAY_MS)
  }

  let position = '尚未出聲'
  if (playing?.positionSeconds != null) {
    position = `${formatTime(playing.positionSeconds)} / ${formatTime(playing.durationSeconds)}`
    if (playing.beatIndex !== null && playingTrack) {
      const bar = Math.floor(playing.beatIndex / playingTrack.beatsPerBar) + 1
      position += ` · 第 ${playing.beatIndex + 1} 拍（約第 ${bar} 小節）`
    }
  }

  return (
    <div className="audio-panel">
      <span className="control-label">音樂播放 · 延遲校準</span>
      {audio && !audio.available ? (
        <p className="backend-error" role="alert">音訊停用：{audio.error}</p>
      ) : (
        <p className="control-note" role="status">
          輸出裝置：{audio?.device.name ?? '找不到'}
          {audio?.device.requested ? `（設定：「${audio.device.requested}」）` : '（系統預設）'}
          {audio?.bufferMs ? ` · 緩衝 ${audio.bufferMs} ms` : ''}
        </p>
      )}
      {audio?.underflows ? (
        <p className="backend-error" role="status">
          啟動後斷音 {audio.underflows} 次（音效卡緩衝用完）。持續增加時可加大設定的 [audio] buffer_ms。
        </p>
      ) : null}
      <label className="sequence-field">
        曲目
        <select value={trackId} onChange={(event) => setTrackId(event.target.value)}>
          {tracks.map((track) => (
            <option key={track.id} value={track.id} disabled={!track.available}>
              {track.title}
              {track.tempo ? ` · ${Math.round(track.tempo)} BPM` : ''}
              {` · ${formatTime(track.durationSeconds)}`}
              {track.available ? '' : '（音檔不在主機上）'}
            </option>
          ))}
        </select>
      </label>
      <label className="audio-option">
        <input type="checkbox" checked={flash} onChange={(event) => setFlash(event.target.checked)} />
        閃拍：全部轉燈與燈具隨拍點閃白（小節第一拍全亮、其他拍 40%），暫時蓋過目前效果
      </label>
      <label className="audio-option">
        <input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />
        循環播放
      </label>
      <div className="button-row">
        <button type="button" disabled={busy || !audio?.available || !selected?.available}
          onClick={() => { void run(() => playAudio({ trackId, flash, loop })) }}>
          {playing ? '重新播放' : '播放'}
        </button>
        <button type="button" className="secondary" disabled={busy || !playing}
          onClick={() => { void run(stopAudio) }}>停止</button>
      </div>
      {playing && (
        <p className="weather-override" role="status">
          播放中：{playingTrack?.title ?? playing.trackId}{playing.flash ? '（閃拍）' : ''} · {position}
        </p>
      )}

      <label className="control-field">
        <span>
          延遲補償
          <output>{current > 0 ? '+' : ''}{current} ms{current === saved ? '' : `（已儲存 ${saved} ms）`}</output>
        </span>
        <input type="range" min={-SLIDER_RANGE_MS} max={SLIDER_RANGE_MS} step={5}
          value={Math.max(-SLIDER_RANGE_MS, Math.min(SLIDER_RANGE_MS, current))}
          disabled={!audio} onChange={(event) => adjust(Number(event.target.value))} />
      </label>
      <div className="button-row audio-offset-row">
        <button type="button" className="secondary" disabled={!audio} onClick={() => adjust(current - 10)}>−10</button>
        <button type="button" className="secondary" disabled={!audio} onClick={() => adjust(current - 1)}>−1</button>
        <input type="number" min={-max} max={max} step={1} value={current} aria-label="延遲補償（毫秒）"
          onChange={(event) => { if (event.target.value.trim() !== '') adjust(Number(event.target.value)) }} />
        <button type="button" className="secondary" disabled={!audio} onClick={() => adjust(current + 1)}>+1</button>
        <button type="button" className="secondary" disabled={!audio} onClick={() => adjust(current + 10)}>+10</button>
      </div>
      <div className="button-row">
        <button type="button" disabled={busy || !audio || current === saved}
          onClick={() => { void run(() => setAudioCalibration(current, true), true) }}>儲存</button>
        <button type="button" className="secondary" disabled={busy || !audio || current === saved}
          onClick={() => { void run(() => setAudioCalibration(saved, false), true) }}>還原為已儲存</button>
      </div>
      {error && <p className="backend-error" role="alert">{error}</p>}
      <p className="control-note">
        建議用「節拍器」校準：拍點完全精確，不受曲子拍點分析誤差影響。開閃拍後站在現場聽：
        燈比聲音<strong>慢</strong>就往<strong>正</strong>調（燈提早），燈比聲音<strong>快</strong>就往<strong>負</strong>調。
        調整會立即生效，按「儲存」後重新啟動也會保留。音檔放在主機的 /etc/tph-art-museum/audio/。
      </p>
    </div>
  )
}
