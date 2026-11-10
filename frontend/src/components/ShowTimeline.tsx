import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import { getEffectManifest } from '../effects'
import {
  addBeat, barOf, isDownbeat, moveBeat, nearestBeat, removeBeat, setBeatsPerBar, setDownbeat, startAtBeat,
} from '../simulation/beatEdit'
import { barBeatAt } from '../simulation/show'
import type { ShowEditorState } from '../useShowEditor'
import { formatClock } from './ShowEditor'
import { EditableNumberInput } from './SequenceEditor'

// 演出時間軸（M14）：波形、拍點、段落與播放位置。段落模式拖曳段落開頭（吸附拍點）；拍點模式修正拍點。
// Show timeline (M14): waveform, beats, sections and the playhead. Section mode drags section starts (snapping to
// beats); beat mode corrects the beats.

const RULER = 20
const LANE = 30
const WAVE = 96
const HEIGHT = RULER + LANE + WAVE
const HIT = 6
const SECTION_COLORS = ['#2f6f8f', '#7a4f9a', '#3f7f5a', '#9a6a2f', '#8f3f5a', '#4f5f9a']

type Mode = 'sections' | 'beats'
type Drag =
  | { kind: 'section'; id: number; x: number; time: number; beat: number | null; moved: boolean }
  | { kind: 'beat'; index: number; x: number; time: number; moved: boolean }

type ShowTimelineProps = {
  editor: ShowEditorState
  /** 音樂沒在播放時的位置（模擬時鐘）。 / The position while no music plays (the simulator clock). */
  fallbackSeconds: number | null
}

function timeStep(pixelsPerSecond: number): number {
  return [0.5, 1, 2, 5, 10, 15, 30, 60].find((step) => step * pixelsPerSecond >= 70) ?? 120
}

export function ShowTimeline({ editor, fallbackSeconds }: ShowTimelineProps) {
  const [mode, setMode] = useState<Mode>('sections')
  const [zoom, setZoom] = useState(60)
  const [follow, setFollow] = useState(true)
  const [selectedBeat, setSelectedBeat] = useState<number | null>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const drag = useRef<Drag | null>(null)
  const [width, setWidth] = useState(800)
  const { draft, beatMap, compiled } = editor
  const peaks = beatMap ? editor.peaks[beatMap.id] : undefined
  const state = useRef({ editor, fallbackSeconds, mode, zoom, follow, selectedBeat, width })
  state.current = { editor, fallbackSeconds, mode, zoom, follow, selectedBeat, width }

  const { loadWaveform } = editor
  useEffect(() => { if (beatMap && !peaks) void loadWaveform(beatMap) }, [beatMap?.id, peaks, loadWaveform])

  useEffect(() => {
    const element = scroll.current
    if (!element) return
    const observer = new ResizeObserver(() => setWidth(element.clientWidth))
    observer.observe(element)
    setWidth(element.clientWidth)
    return () => observer.disconnect()
  }, [])

  const position = () => state.current.editor.audioSeconds() ?? state.current.fallbackSeconds

  // 每個動畫影格重畫（播放位置會動）。 / Redraw every animation frame (the playhead moves).
  useEffect(() => {
    let frame = 0
    const draw = () => {
      frame = requestAnimationFrame(draw)
      const element = canvas.current
      const container = scroll.current
      const { editor: current, mode: currentMode, zoom: pps, follow: following, selectedBeat: chosen, width: viewWidth } = state.current
      const map = current.beatMap
      if (!element || !container || !map) return
      const context = element.getContext('2d')
      if (!context) return
      const ratio = window.devicePixelRatio || 1
      if (element.width !== Math.round(viewWidth * ratio) || element.height !== Math.round(HEIGHT * ratio)) {
        element.width = Math.round(viewWidth * ratio)
        element.height = Math.round(HEIGHT * ratio)
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      const now = position()
      if (following && current.preview === 'playing' && now !== null) {
        const left = container.scrollLeft / pps
        const right = left + viewWidth / pps
        if (now < left || now > right - (viewWidth * 0.1) / pps) container.scrollLeft = Math.max(0, now * pps - viewWidth * 0.2)
      }
      const start = container.scrollLeft / pps
      const x = (seconds: number) => (seconds - start) * pps
      context.clearRect(0, 0, viewWidth, HEIGHT)
      context.fillStyle = '#0b1018'
      context.fillRect(0, 0, viewWidth, HEIGHT)

      // 尺標：時間。 / Ruler: time.
      context.fillStyle = '#121a24'
      context.fillRect(0, 0, viewWidth, RULER)
      context.font = '10px system-ui, sans-serif'
      context.textBaseline = 'middle'
      const step = timeStep(pps)
      for (let tick = Math.floor(start / step) * step; tick < start + viewWidth / pps; tick += step) {
        const position = x(tick)
        context.fillStyle = '#3a4656'
        context.fillRect(position, RULER - 5, 1, 5)
        context.fillStyle = '#7d8fa3'
        // 間隔小於 1 秒時多顯示一位小數。 / One decimal when ticks are under a second apart.
        context.fillText(step < 1 ? formatClock(tick).slice(0, -1) : formatClock(tick).replace(/\.\d+$/, ''), position + 3, 8)
      }

      // 段落。 / Sections.
      const show = current.compiled.show
      const sections = current.draft?.sections ?? []
      if (show) {
        show.steps.forEach((step, index) => {
          const from = x(show.startSeconds[index])
          const to = x(show.startSeconds[index] + step.durationFrames / (show.totalFrames / show.durationSeconds))
          if (to < 0 || from > viewWidth) return
          context.fillStyle = SECTION_COLORS[index % SECTION_COLORS.length]
          context.fillRect(from, RULER + 2, Math.max(1, to - from - 1), LANE - 4)
          if (step.transitionFrames > 0) {
            const fade = (step.transitionFrames / show.totalFrames) * show.durationSeconds * pps
            const gradient = context.createLinearGradient(from, 0, from + fade, 0)
            gradient.addColorStop(0, 'rgba(255,255,255,0.35)')
            gradient.addColorStop(1, 'rgba(255,255,255,0)')
            context.fillStyle = gradient
            context.fillRect(from, RULER + 2, fade, LANE - 4)
          }
          if (sections[index]?.id === current.selectedId) {
            context.strokeStyle = '#65d9f8'
            context.lineWidth = 2
            context.strokeRect(from + 1, RULER + 3, Math.max(1, to - from - 3), LANE - 6)
          }
          context.save()
          context.beginPath()
          context.rect(from, RULER, Math.max(0, to - from - 4), LANE)
          context.clip()
          context.fillStyle = '#e8eef5'
          context.fillText(`${index + 1}. ${getEffectManifest(step.preset.effectId).label}`, Math.max(from, 0) + 5, RULER + LANE / 2)
          context.restore()
        })
      }

      // 波形。 / Waveform.
      const top = RULER + LANE
      const middle = top + WAVE / 2
      const data = current.peaks[map.id]
      if (data && typeof data === 'object') {
        context.fillStyle = '#35566e'
        for (let column = 0; column < viewWidth; column += 1) {
          const from = Math.floor(((start + column / pps) * data.perSecond))
          const to = Math.max(from + 1, Math.floor((start + (column + 1) / pps) * data.perSecond))
          let peak = 0
          for (let index = Math.max(0, from); index < Math.min(data.values.length, to); index += 1) {
            if (data.values[index] > peak) peak = data.values[index]
          }
          const height = Math.min(1, peak) * (WAVE / 2 - 4)
          if (height > 0.3) context.fillRect(column, middle - height, 1, height * 2)
        }
      } else {
        context.fillStyle = '#5d6f82'
        context.fillText(data === 'missing' ? '沒有音檔，無法顯示波形' : '讀取波形中…', 8, middle)
      }

      // 拍點：小節第一拍較亮並標小節號。 / Beats: downbeats brighter, with bar numbers.
      const beats = map.beats
      const gap = beats.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 1
      const showBeats = gap * pps >= 4
      const barLabels = gap * map.beatsPerBar * pps >= 26
      let first = nearestBeat(beats, start) ?? 0
      if (first > 0) first -= 1
      const editing = currentMode === 'beats'
      for (let index = first; index < beats.length; index += 1) {
        const position = x(beats[index])
        if (position > viewWidth) break
        const downbeat = isDownbeat(map, index)
        if (!downbeat && !showBeats) continue
        context.fillStyle = index === chosen && editing ? '#ffb347'
          : downbeat ? (editing ? 'rgba(255,214,102,0.95)' : 'rgba(255,214,102,0.6)')
            : (editing ? 'rgba(170,190,210,0.7)' : 'rgba(170,190,210,0.28)')
        const lineWidth = index === chosen && editing ? 3 : downbeat ? 1.5 : 1
        context.fillRect(position - lineWidth / 2, downbeat ? RULER : top, lineWidth, downbeat ? HEIGHT - RULER : WAVE)
        if (downbeat && barLabels) {
          context.fillStyle = '#ffd666'
          context.fillText(String(barOf(map, index)), position + 3, top + 8)
        }
      }

      // 拖曳中的位置。 / The position being dragged.
      const dragging = drag.current
      if (dragging?.moved) {
        context.strokeStyle = '#ffffff'
        context.setLineDash([4, 3])
        context.beginPath()
        context.moveTo(x(dragging.time) + 0.5, RULER)
        context.lineTo(x(dragging.time) + 0.5, HEIGHT)
        context.stroke()
        context.setLineDash([])
      }

      // 播放位置。 / Playhead.
      if (now !== null) {
        context.fillStyle = '#ff5a5a'
        context.fillRect(x(now) - 1, 0, 2, HEIGHT)
      }
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [])

  if (!draft || !beatMap) return null

  const timeAt = (event: PointerEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    const left = scroll.current?.scrollLeft ?? 0
    return { x: event.clientX - rect.left, y: event.clientY - rect.top, time: (left + event.clientX - rect.left) / zoom }
  }
  const pixelOf = (seconds: number) => seconds * zoom - (scroll.current?.scrollLeft ?? 0)

  const onPointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    const { x, y, time } = timeAt(event)
    event.currentTarget.focus({ preventScroll: true })
    event.currentTarget.setPointerCapture(event.pointerId)
    if (mode === 'sections' && y >= RULER && y < RULER + LANE && compiled.show) {
      const starts = compiled.show.startSeconds
      const boundary = starts.findIndex((seconds, index) => index > 0 && Math.abs(pixelOf(seconds) - x) <= HIT)
      if (boundary > 0) {
        drag.current = { kind: 'section', id: draft.sections[boundary].id, x, time: starts[boundary], beat: null, moved: false }
        editor.setSelectedId(draft.sections[boundary].id)
        return
      }
      const index = starts.reduce((found, seconds, position) => seconds <= time ? position : found, -1)
      if (index >= 0) editor.setSelectedId(draft.sections[index].id)
      return
    }
    if (mode === 'beats') {
      const near = nearestBeat(beatMap.beats, time)
      if (near !== null && Math.abs(pixelOf(beatMap.beats[near]) - x) <= HIT) {
        setSelectedBeat(near)
        drag.current = { kind: 'beat', index: near, x, time: beatMap.beats[near], moved: false }
        return
      }
      setSelectedBeat(null)
    }
    editor.seek(time)
  }

  const onPointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    const current = drag.current
    if (!current) return
    const { x, time } = timeAt(event)
    if (!current.moved && Math.abs(x - current.x) < 3) return
    current.moved = true
    if (current.kind === 'beat') {
      current.time = time
      return
    }
    // 吸附到最近的拍點；按住 Alt（Option）不吸附。 / Snap to the nearest beat; hold Alt (Option) to place freely.
    const near = event.altKey ? null : nearestBeat(beatMap.beats, time)
    current.beat = near
    current.time = near === null ? time : beatMap.beats[near]
  }

  const onPointerUp = () => {
    const current = drag.current
    drag.current = null
    if (!current?.moved) return
    if (current.kind === 'beat') {
      editor.editBeats((map) => moveBeat(map, current.index, current.time))
      return
    }
    const start = current.beat !== null
      ? startAtBeat(beatMap, current.beat)
      : { seconds: Math.round(Math.max(0, current.time) * 1000) / 1000 }
    editor.updateSection(current.id, (section) => ({ ...section, start }))
  }

  const tap = () => {
    const now = editor.audioSeconds()
    if (now === null) return
    const added = addBeat(beatMap, now)
    if (added) {
      editor.editBeats(() => added.map)
      setSelectedBeat(added.index)
    }
  }
  const nudge = (delta: number) => {
    if (selectedBeat === null) return
    editor.editBeats((map) => moveBeat(map, selectedBeat, map.beats[selectedBeat] + delta))
  }
  const remove = () => {
    if (selectedBeat === null) return
    editor.editBeats((map) => removeBeat(map, selectedBeat))
    setSelectedBeat(null)
  }
  const playing = editor.preview === 'playing' || editor.preview === 'silent'
  const togglePlay = () => {
    if (playing) editor.stopPreview()
    else void editor.playPreview(position() ?? 0)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    if (event.key === ' ') {
      event.preventDefault()
      togglePlay()
    } else if (mode === 'beats' && (event.key === 'b' || event.key === 'B')) {
      event.preventDefault()
      tap()
    } else if (mode === 'beats' && (event.key === 'Delete' || event.key === 'Backspace')) {
      event.preventDefault()
      remove()
    } else if (mode === 'beats' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      event.preventDefault()
      nudge((event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 0.05 : 0.005))
    }
  }

  const onDoubleClick = (event: MouseEvent<HTMLCanvasElement>) => {
    if (mode !== 'beats') return
    const rect = event.currentTarget.getBoundingClientRect()
    const added = addBeat(beatMap, ((scroll.current?.scrollLeft ?? 0) + event.clientX - rect.left) / zoom)
    if (added) {
      editor.editBeats(() => added.map)
      setSelectedBeat(added.index)
    }
  }

  const download = () => {
    const blob = new Blob([JSON.stringify(beatMap, null, 1) + '\n'], { type: 'application/json' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `${beatMap.id}.json`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  const now = position()
  const nowBar = now !== null ? barBeatAt(beatMap, now) : null
  const chosen = selectedBeat !== null && selectedBeat < beatMap.beats.length ? selectedBeat : null
  const chosenBar = chosen !== null ? startAtBeat(beatMap, chosen) : null
  const track = editor.tracks.find((item) => item.id === beatMap.id)
  const builtIn = beatMap.file === null

  return (
    <div className="show-timeline">
      <div className="timeline-toolbar">
        <div className="segmented-control" aria-label="時間軸編輯模式">
          <button type="button" className={mode === 'sections' ? 'active' : 'secondary'} aria-pressed={mode === 'sections'}
            onClick={() => setMode('sections')}>段落</button>
          <button type="button" className={mode === 'beats' ? 'active' : 'secondary'} aria-pressed={mode === 'beats'}
            onClick={() => setMode('beats')}>拍點修正</button>
        </div>
        <button type="button" onClick={togglePlay} disabled={!compiled.show || editor.preview === 'loading'}>
          {playing ? '停止' : '從播放位置播放'}
        </button>
        <span className="timeline-position" role="status">
          {now !== null ? formatClock(now) : '—'} / {formatClock(beatMap.durationSeconds)}
          {nowBar ? ` · 第 ${nowBar.bar} 小節第 ${nowBar.beat} 拍` : ''}
        </span>
        <label className="timeline-zoom">
          縮放
          <input type="range" min={10} max={400} step={5} value={zoom} onChange={(event) => {
            const container = scroll.current
            const center = container ? (container.scrollLeft + container.clientWidth / 2) / zoom : 0
            const next = Number(event.target.value)
            setZoom(next)
            requestAnimationFrame(() => { if (container) container.scrollLeft = center * next - container.clientWidth / 2 })
          }} />
        </label>
        <label className="audio-option">
          <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
          跟隨播放
        </label>
      </div>

      <div className="timeline-scroll" ref={scroll}>
        <div style={{ width: Math.ceil(beatMap.durationSeconds * zoom), height: HEIGHT }}>
          <canvas ref={canvas} className="timeline-canvas" tabIndex={0} aria-label="演出時間軸"
            style={{ width, height: HEIGHT }}
            onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
            onPointerCancel={() => { drag.current = null }} onDoubleClick={onDoubleClick} onKeyDown={onKeyDown} />
        </div>
      </div>

      {mode === 'sections' ? (
        <p className="control-note">
          點時間軸跳到該位置；點段落選取（右側「演出」分頁可改細節）；拖曳段落開頭改開始位置，會吸附到拍點（按住 Option 不吸附）。
          空白鍵播放／停止。
        </p>
      ) : (
        <div className="timeline-beats">
          {builtIn ? <p className="control-note">內建節拍器的拍點是精確的，不能修改。</p> : (
            <>
              <div className="button-row">
                <button type="button" className="secondary" onClick={tap} disabled={editor.preview !== 'playing'}>在播放位置加拍（B）</button>
                <button type="button" className="secondary" onClick={remove} disabled={chosen === null}>刪除選取的拍（Delete）</button>
                <button type="button" className="secondary" onClick={() => nudge(-0.005)} disabled={chosen === null}>−5 ms（←）</button>
                <button type="button" className="secondary" onClick={() => nudge(0.005)} disabled={chosen === null}>+5 ms（→）</button>
                <button type="button" className="secondary" disabled={chosen === null}
                  onClick={() => { if (chosen !== null) editor.editBeats((map) => setDownbeat(map, chosen)) }}>設為小節第一拍</button>
                <label className="timeline-bpb">
                  每小節
                  <EditableNumberInput value={beatMap.beatsPerBar} min={1} max={16} step={1} integer
                    onValue={(value) => editor.editBeats((map) => setBeatsPerBar(map, value))} />
                  拍
                </label>
              </div>
              <p className="control-note" role="status">
                {chosen !== null && chosenBar && 'bar' in chosenBar
                  ? `選取：第 ${chosen + 1} 拍 · ${beatMap.beats[chosen].toFixed(3)} 秒 · 第 ${chosenBar.bar} 小節第 ${chosenBar.beat ?? 1} 拍。`
                  : `共 ${beatMap.beats.length} 拍。`}
                點拍點選取、拖曳移動；雙擊空白處新增；播放時按 B 跟著音樂打拍新增；Shift＋←／→ 一次 50 ms。
                以小節定位的段落會跟著修正後的拍點移動。
              </p>
              <div className="button-row">
                <button type="button" disabled={editor.busy || !editor.beatsDirty} onClick={() => { void editor.saveBeats() }}>儲存拍點</button>
                <button type="button" className="secondary" disabled={editor.busy || !editor.beatsDirty} onClick={editor.discardBeats}>放棄修改</button>
                <button type="button" className="secondary" disabled={editor.busy || !track?.edited}
                  onClick={() => { if (window.confirm('刪除修正，回到內建的自動分析拍點？')) void editor.revertBeats() }}>還原內建拍點</button>
                <button type="button" className="secondary" onClick={download}>下載拍點檔</button>
              </div>
              <p className="control-note">
                {editor.beatsDirty ? '拍點有尚未儲存的修改（預覽已套用）。' : track?.edited ? '使用修正後的拍點。' : '使用內建（自動分析）的拍點。'}
                儲存在後端的 preset/beat-edits/；要放回 repo 時下載後覆蓋 backend/tracks/ 的同名檔。
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
