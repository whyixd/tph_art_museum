import { useState } from 'react'
import { getEffectCompletion, getEffectManifest } from '../effects'
import { getFixtureCompletion, getFixtureManifest } from '../fixtures'
import type { CompiledSequence, FixtureCounts, PlaybackRule, SequenceClock, SequenceEnding, SequenceStep, TransitionKind } from '../simulation/sequence'
import { GROUP_LABELS } from './FixtureEditor'

/** One cycle of the step's `clock` group, or null when it has no boundary (or no preset). */
function clockCompletion(step: SequenceStep, clock: SequenceClock, fps: number, unitCount: number, counts: FixtureCounts) {
  if (clock === 'spinner') return getEffectCompletion(step.preset, fps, unitCount)
  const preset = step.fixtures?.[clock]
  const frames = preset ? getFixtureCompletion(preset, fps, Math.max(1, counts[clock])) : null
  return frames === null || !preset ? null : { label: getFixtureManifest(preset.effectId).label + '一個週期', frames }
}

type SequenceEditorProps = {
  enabled: boolean
  connected: boolean
  playbackMode: 'sequence' | 'single'
  busy: boolean
  error: string | null
  dirty: boolean
  playingStepId: number | null
  onSave: () => void
  onReload: () => void
  steps: SequenceStep[]
  ending: SequenceEnding
  selectedId: number
  schedule: CompiledSequence
  currentStepIndex: number
  complete: boolean
  fps: number
  unitCount: number
  fixtureCounts: FixtureCounts
  onEnabled: (enabled: boolean) => void
  onSelect: (id: number) => void
  onPlayFrom: (id: number) => void
  onPreviewTransition: (id: number) => void
  onAdd: () => void
  onDuplicate: () => void
  onRemove: () => void
  onMove: (direction: -1 | 1) => void
  onPlayback: (playback: PlaybackRule) => void
  onEnding: (ending: SequenceEnding) => void
  onTransition: (kind: TransitionKind, seconds: number) => void
}

type EditableNumberInputProps = {
  value: number
  min: number
  max: number
  step: number
  integer?: boolean
  onValue: (value: number) => void
}

export function EditableNumberInput({
  value, min, max, step, integer = false, onValue,
}: EditableNumberInputProps) {
  const [draft, setDraft] = useState<string | null>(null)
  const parse = (text: string) => {
    if (text.trim() === '') return null
    const number = Number(text)
    return Number.isFinite(number) ? number : null
  }
  const normalize = (number: number) => {
    const clamped = Math.min(max, Math.max(min, number))
    return integer ? Math.floor(clamped) : clamped
  }

  return (
    <input type="number" min={min} max={max} step={step}
      value={draft ?? value}
      onChange={(event) => {
        const text = event.target.value
        setDraft(text)
        const number = parse(text)
        if (number !== null && number >= min && number <= max) {
          const next = normalize(number)
          if (next !== value) onValue(next)
        }
      }}
      onBlur={() => {
        if (draft !== null) {
          const number = parse(draft)
          if (number !== null) {
            const next = normalize(number)
            if (next !== value) onValue(next)
          }
          setDraft(null)
        }
      }} />
  )
}

export function SequenceEditor({
  enabled, connected, playbackMode, busy, error, dirty, playingStepId, onSave, onReload,
  steps, ending, selectedId, schedule, currentStepIndex, complete, fps, unitCount, fixtureCounts,
  onEnabled, onSelect, onPlayFrom, onPreviewTransition, onAdd, onDuplicate, onRemove, onMove, onPlayback, onEnding, onTransition,
}: SequenceEditorProps) {
  const selectedIndex = Math.max(0, steps.findIndex((step) => step.id === selectedId))
  const selected = steps[selectedIndex]
  const clock: SequenceClock = selected.playback.kind === 'count' ? selected.playback.clock ?? 'spinner' : 'spinner'
  const completion = clockCompletion(selected, clock, fps, unitCount, fixtureCounts)
  const playbackChoice = selected.playback.kind === 'time' ? 'time' : clock
  const choose = (choice: 'time' | SequenceClock) => {
    if (choice === 'time') {
      onPlayback({ kind: 'time', seconds: Math.max(1 / fps, Number((durationFrames / fps).toFixed(2)) || 8) })
    } else {
      const count = selected.playback.kind === 'count' ? selected.playback.count : 1
      onPlayback(choice === 'spinner' ? { kind: 'count', count } : { kind: 'count', count, clock: choice })
    }
  }
  const durationFrames = schedule.steps[selectedIndex]?.durationFrames ?? 0
  return (
    <div className="sequence-controls">
      <span className="control-label">效果播放</span>
      <div className="segmented-control" aria-label="效果播放模式">
        <button type="button" className={!enabled ? 'active' : 'secondary'}
          aria-pressed={!enabled} onClick={() => onEnabled(false)}>單一效果</button>
        <button type="button" className={enabled ? 'active' : 'secondary'}
          aria-pressed={enabled} onClick={() => onEnabled(true)}>串接播放</button>
      </div>
      {enabled && (
        <>
          <p className="sequence-status" role="status">
            {complete
              ? ending.kind === 'fade-black' ? '播放完成 · 淡出至黑' : '播放完成 · 維持最後影格'
              : '正在播放第 ' + (currentStepIndex + 1) + ' / ' + steps.length + ' 段 · ' + getEffectManifest(steps[currentStepIndex].preset.effectId).label}
            <span>{ending.kind === 'loop' ? '每輪' : '段落'}約 {(schedule.totalFrames / fps).toFixed(2)} 秒
              {ending.kind === 'fade-black' ? ' · 淡出 ' + ending.seconds + ' 秒' : ''}</span>
          </p>
          <div className="sequence-storage">
            <p role="status" className="control-note">
              {busy ? '串接載入／儲存中…' : connected
                ? dirty ? '有尚未儲存的串接變更。'
                  : playbackMode === 'single' ? '串接已儲存；後端目前播放單一效果，按儲存可切回串接。'
                    : '已從後端載入，串接已儲存。'
                : '尚未連線後端；目前編輯只保留在此頁面。'}
            </p>
            {error && <p className="backend-error" role="alert">{error}</p>}
            <div className="button-row">
              <button type="button" className="secondary" onClick={onReload} disabled={busy}>重新讀取</button>
              <button type="button" onClick={onSave} disabled={busy}>儲存串接</button>
            </div>
          </div>
          <div className="sequence-ending">
            <span className="control-label">串接結束後</span>
            <div className="segmented-control" aria-label="串接結束方式">
              <button type="button" className={ending.kind === 'hold' ? 'active' : 'secondary'}
                aria-pressed={ending.kind === 'hold'}
                onClick={() => onEnding({ kind: 'hold' })}>維持最後畫面</button>
              <button type="button" className={ending.kind === 'loop' ? 'active' : 'secondary'}
                aria-pressed={ending.kind === 'loop'}
                onClick={() => onEnding({ kind: 'loop' })}>循環播放</button>
              <button type="button" className={ending.kind === 'fade-black' ? 'active' : 'secondary'}
                aria-pressed={ending.kind === 'fade-black'}
                onClick={() => { if (ending.kind !== 'fade-black') onEnding({ kind: 'fade-black', seconds: 1 }) }}>淡出至黑</button>
            </div>
            {ending.kind === 'fade-black' && (
              <label className="sequence-field">
                淡出時間（秒）
                <EditableNumberInput value={ending.seconds} min={1 / fps} max={10} step={0.1}
                  onValue={(seconds) => onEnding({ kind: 'fade-black', seconds })} />
              </label>
            )}
          </div>
          <ol className="sequence-list">
            {steps.map((step, index) => (
              <li key={step.id}>
                <button type="button"
                  className={step.id === selectedId ? 'active' : 'secondary'}
                  aria-pressed={step.id === selectedId}
                  onClick={() => onSelect(step.id)}>
                  <span>{index + 1}. {getEffectManifest(step.preset.effectId).label}
                    {step.id === playingStepId && <em className="playing-badge">後端播放中</em>}</span>
                  {(step.fixtures?.ball || step.fixtures?.wash) && (
                    <small className="step-fixtures">
                      {(['ball', 'wash'] as const).filter((group) => step.fixtures?.[group]).map((group) => (
                        GROUP_LABELS[group] + '：' + getFixtureManifest(step.fixtures![group]!.effectId).label
                      )).join(' · ')}
                    </small>
                  )}
                  <small>{step.playback.kind === 'count'
                    ? '播放 ' + step.playback.count + ' 次' + (step.playback.clock && step.playback.clock !== 'spinner'
                      ? '（依' + GROUP_LABELS[step.playback.clock] + '）' : '')
                    : '播放 ' + step.playback.seconds + ' 秒'}</small>
                </button>
              </li>
            ))}
          </ol>
          <div className="button-row sequence-actions">
            <button type="button" onClick={onAdd} disabled={steps.length >= 64}>新增段落</button>
            <button type="button" className="secondary" onClick={onDuplicate}>複製</button>
            <button type="button" className="secondary" onClick={onRemove}
              disabled={steps.length <= 1}>移除</button>
            <button type="button" className="secondary" onClick={() => onMove(-1)}
              disabled={selectedIndex === 0} aria-label="段落上移">↑</button>
            <button type="button" className="secondary" onClick={() => onMove(1)}
              disabled={selectedIndex === steps.length - 1} aria-label="段落下移">↓</button>
          </div>
          <div className="sequence-detail">
            <strong>第 {selectedIndex + 1} 段設定</strong>
            <div className="button-row sequence-seek">
              <button type="button" className="secondary" onClick={() => onPlayFrom(selected.id)}>從這段播放</button>
              <button type="button" className="secondary" onClick={() => onPreviewTransition(selected.id)}>
                {selectedIndex < steps.length - 1 || ending.kind === 'loop' ? '預覽銜接' : '預覽結尾'}
              </button>
            </div>
            <label className="sequence-field">
              播放方式
              <select value={playbackChoice} onChange={(event) => choose(event.target.value as 'time' | SequenceClock)}>
                <option value="time">依時間</option>
                {(['spinner', 'ball', 'wash'] as const).map((group) => (
                  <option key={group} value={group} disabled={!clockCompletion(selected, group, fps, unitCount, fixtureCounts)}>
                    依{GROUP_LABELS[group]}次數
                  </option>
                ))}
              </select>
            </label>
            <p className="control-note">
              {selected.playback.kind === 'time'
                ? '依時間切換；有週期的群組（轉燈、球燈或洗牆燈）也可改依次數。'
                : completion
                  ? '完成邊界（' + GROUP_LABELS[clock] + '）：' + completion.label + '。目前每次約 ' + (completion.frames / fps).toFixed(2)
                    + ' 秒；只有這個群組每次重播，其他群組在這段內連續播放。'
                  : '這個群組沒有週期邊界，請改依時間。'}
            </p>
            {selected.playback.kind === 'count' ? (
              <label className="sequence-field">
                播放次數
                <EditableNumberInput key={selected.id + '-playback-count'}
                  value={selected.playback.count} min={1} max={100} step={1} integer
                  onValue={(count) => onPlayback(clock === 'spinner' ? { kind: 'count', count } : { kind: 'count', count, clock })} />
              </label>
            ) : (
              <label className="sequence-field">
                播放時間（秒）
                <EditableNumberInput key={selected.id + '-playback-time'}
                  value={selected.playback.seconds} min={1 / fps} max={3600} step={0.1}
                  onValue={(seconds) => onPlayback({ kind: 'time', seconds })} />
              </label>
            )}
            <p className="sequence-duration">這段共 {durationFrames} 影格，約 {(durationFrames / fps).toFixed(2)} 秒。</p>
            {(selectedIndex < steps.length - 1 || ending.kind === 'loop') && (
              <>
                <label className="sequence-field">
                  {selectedIndex === steps.length - 1 ? '接回第一段' : '接到下一段'}
                  <select value={selected.transition.kind}
                    onChange={(event) => onTransition(event.target.value as TransitionKind, selected.transition.seconds)}>
                    <option value="cut">直接切換</option>
                    <option value="fade-black">淡出至黑再淡入</option>
                    <option value="crossfade">交叉淡化</option>
                    <option value="unit-fade-black">逐台淡出至黑再淡入</option>
                    <option value="unit-breathe-black">逐台呼吸後淡出至黑再淡入</option>
                  </select>
                </label>
                {selected.transition.kind !== 'cut' && (
                  <label className="sequence-field">
                    銜接時間（秒）
                    <EditableNumberInput key={selected.id + '-transition-' + selected.transition.kind}
                      value={selected.transition.seconds} min={1 / fps} max={10} step={0.1}
                      onValue={(seconds) => onTransition(selected.transition.kind, seconds)} />
                  </label>
                )}
              </>
            )}
          </div>
          <p className="control-note">
            銜接在下一段開始時播放；循環時最後一段接回第一段。未儲存的編輯只供模擬；按「儲存串接」才會同步到後端。
          </p>
        </>
      )}
    </div>
  )
}
