import { useState, type ReactNode } from 'react'
import { getEffectManifest } from '../effects'
import { getFixtureManifest } from '../fixtures'
import type { TransitionKind } from '../simulation/sequence'
import { barBeatAt, describeStart, startSeconds, type ShowSection, type ShowTransition } from '../simulation/show'
import type { ShowEditorState } from '../useShowEditor'
import { GROUP_LABELS } from './FixtureEditor'
import { EditableNumberInput } from './SequenceEditor'
import { SHOW_DELAY_MAX_SECONDS, type ShowMotorOptions } from '../backendApi'

const MOTOR_OPTIONS_KEY = 'tph.showMotorOptions'

function readMotorOptions(): ShowMotorOptions {
  try {
    const stored = JSON.parse(window.localStorage.getItem(MOTOR_OPTIONS_KEY) ?? 'null')
    if (stored && typeof stored.startMotors === 'boolean' && Number.isFinite(stored.delaySeconds)) {
      return { startMotors: stored.startMotors, delaySeconds: Math.min(SHOW_DELAY_MAX_SECONDS, Math.max(0, stored.delaySeconds)) }
    }
  } catch { /* 讀不到就用預設 */ }
  return { startMotors: false, delaySeconds: 0 }
}

const TRANSITION_LABELS: Record<TransitionKind, string> = {
  cut: '直接切換', crossfade: '交叉淡化', 'fade-black': '淡出至黑再淡入', 'unit-fade-black': '逐台淡出至黑再淡入',
  'unit-breathe-black': '逐台呼吸後淡出至黑再淡入',
}

export function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${(seconds - minutes * 60).toFixed(2).padStart(5, '0')}`
}

function describeTransition(transition: ShowTransition): string {
  if (transition.kind === 'cut') return TRANSITION_LABELS.cut
  return TRANSITION_LABELS[transition.kind] + ' ' + ('beats' in transition ? `${transition.beats} 拍` : `${transition.seconds} 秒`)
}

type ShowEditorProps = {
  editor: ShowEditorState
  controlMode: 'simulation' | 'backend'
  /** 在模擬畫面預覽演出（效果、顏色分頁改選取段落）。 / Preview the show in the simulator (effect tabs edit the section). */
  enabled: boolean
  onEnabled: (enabled: boolean) => void
  /** 模擬中目前播到第幾段。 / The section the simulator is showing. */
  currentIndex: number | null
  /** 模擬中演出的音樂位置（秒）。 / The simulator's position in the track, in seconds. */
  previewSeconds: number | null
  /** 後端正在播放的演出與段落。 / The show and section the backend is playing. */
  backendPlaying: { showId: string; sectionId: number } | null
  fps: number
  onPlayFrom: (sectionId: number) => void
  /** 選取段落的效果與顏色編輯器（與「效果」「顏色」分頁相同）。 / The selected section's effect and colour editors. */
  sectionEditor: ReactNode
  /** 轉燈台數（覆寫的燈台勾選）。 / Spinner count (override unit checkboxes). */
  unitCount: number
}

export function ShowEditor({ editor, controlMode, enabled, onEnabled, currentIndex, previewSeconds, backendPlaying, fps, onPlayFrom, sectionEditor, unitCount }: ShowEditorProps) {
  const [newTrack, setNewTrack] = useState('')
  const { shows, tracks, draft, beatMap, compiled, selectedId } = editor
  if (shows === undefined) return <p className="control-note">讀取演出中…</p>
  if (shows === null) return <p className="control-note">演出需要較新的後端（/api/shows）。</p>

  const selectedIndex = draft ? Math.max(0, draft.sections.findIndex((section) => section.id === selectedId)) : 0
  const selected: ShowSection | undefined = draft?.sections[selectedIndex]
  const startOf = (section: ShowSection) => {
    if (!beatMap) return null
    try { return startSeconds(section.start, beatMap) } catch { return null }
  }
  const usableTracks = tracks.filter((track) => track.available)
  const trackFor = (id: string) => tracks.find((track) => track.id === id)
  const draftTrack = draft ? trackFor(draft.trackId) : undefined
  const hasBeats = (beatMap?.beats.length ?? 0) >= 2
  const playingOnBackend = shows.playing
  const startingOnBackend = shows.starting
  // 只記在這台瀏覽器。 / Remembered in this browser only.
  const [motorOptions, setMotorOptions] = useState<ShowMotorOptions>(readMotorOptions)
  const changeMotorOptions = (next: ShowMotorOptions) => {
    setMotorOptions(next)
    try { window.localStorage.setItem(MOTOR_OPTIONS_KEY, JSON.stringify(next)) } catch { /* 不能存就只用這一次 */ }
  }

  const setStart = (section: ShowSection, start: ShowSection['start']) => editor.updateSection(section.id, (current) => ({ ...current, start }))
  const setTransition = (section: ShowSection, transition: ShowTransition) =>
    editor.updateSection(section.id, (current) => ({ ...current, transition }))

  return (
    <div className="show-editor">
      <span className="control-label">演出 · 效果配合音樂</span>
      <p className="control-note">
        演出＝一首曲目＋多個段落。每段從指定的小節（或秒數）開始，播到下一段開始；燈光時間跟著音樂走。
      </p>

      <label className="sequence-field">
        編輯演出
        <select value={draft && !editor.savedNew ? draft.id : ''} onChange={(event) => {
          const show = shows.shows.find((item) => item.id === event.target.value)
          if (show) {
            editor.open(show)
            if (controlMode === 'simulation') onEnabled(true)
          }
        }}>
          <option value="" disabled>{shows.shows.length ? '選擇已儲存的演出' : '尚未有演出'}</option>
          {shows.shows.map((show) => (
            <option key={show.id} value={show.id}>
              {show.title}（{trackFor(show.trackId)?.title ?? show.trackId}）{shows.errors[show.id] ? '⚠' : ''}
            </option>
          ))}
        </select>
      </label>
      <div className="button-row show-new">
        <select aria-label="新演出的曲目" value={newTrack} onChange={(event) => setNewTrack(event.target.value)}>
          <option value="">選擇曲目…</option>
          {usableTracks.map((track) => (
            <option key={track.id} value={track.id}>
              {track.title}{track.tempo ? ` · ${Math.round(track.tempo)} BPM` : ''} · {formatClock(track.durationSeconds)}
            </option>
          ))}
        </select>
        <button type="button" className="secondary" disabled={!newTrack} onClick={() => {
          editor.create(newTrack)
          if (controlMode === 'simulation') onEnabled(true)
        }}>新增演出</button>
      </div>

      {draft && (
        <>
          <div className="show-header">
            <label className="sequence-field">
              名稱
              <input value={draft.title} maxLength={60} onChange={(event) => editor.setTitle(event.target.value)} />
            </label>
            {editor.savedNew && (
              <label className="sequence-field">
                代號（英數與 -）
                <input value={draft.id} maxLength={40} onChange={(event) => editor.setId(event.target.value.toLowerCase())} />
              </label>
            )}
            <p className="control-note">
              曲目：{draftTrack?.title ?? draft.trackId}
              {beatMap ? ` · ${beatMap.tempo ? Math.round(beatMap.tempo) + ' BPM · ' : ''}每小節 ${beatMap.beatsPerBar} 拍 · ${beatMap.beats.length} 拍 · ${formatClock(beatMap.durationSeconds)}` : ' · 讀取拍點中…'}
            </p>
            {shows.errors[draft.id] && <p className="backend-error" role="alert">後端：{shows.errors[draft.id]}</p>}
          </div>

          {controlMode === 'simulation' && (
            <div className="show-preview">
              <label className="audio-option">
                <input type="checkbox" checked={enabled} onChange={(event) => onEnabled(event.target.checked)} />
                在模擬畫面預覽這個演出；下方段落設定（或「效果」「顏色」分頁）改的是選取的段落
              </label>
              <div className="button-row">
                <button type="button" disabled={!compiled.show || editor.preview === 'loading'}
                  onClick={() => { onEnabled(true); onPlayFrom(draft.sections[0].id); void editor.playPreview(0) }}>
                  從頭播放（含音樂）
                </button>
                <button type="button" className="secondary" disabled={!compiled.show || !selected || editor.preview === 'loading'}
                  onClick={() => {
                    if (!selected) return
                    onEnabled(true)
                    onPlayFrom(selected.id)
                    void editor.playPreview(startOf(selected) ?? 0)
                  }}>
                  從選取段落播放
                </button>
                <button type="button" className="secondary" disabled={editor.preview !== 'playing' && editor.preview !== 'silent'}
                  onClick={editor.stopPreview}>停止</button>
              </div>
              {enabled && previewSeconds !== null && beatMap && (
                <div className="show-position">
                  <p className="weather-override" role="status">
                    {formatClock(previewSeconds)} / {formatClock(beatMap.durationSeconds)}
                    {' · '}{(() => {
                      const position = barBeatAt(beatMap, previewSeconds)
                      return position ? `第 ${position.bar} 小節第 ${position.beat} 拍` : '第一拍之前'
                    })()}
                    {currentIndex !== null ? ` · 第 ${currentIndex + 1} 段` : ''}
                  </p>
                  <progress className="playback-bar" value={Math.min(previewSeconds, beatMap.durationSeconds)}
                    max={beatMap.durationSeconds} aria-label="演出預覽進度" />
                </div>
              )}
              <p className="control-note" role="status">
                {{
                  idle: '已停止：畫面停在目前位置。按播放（或在時間軸按空白鍵）後畫面才會前進；音樂在這台電腦播放，畫面跟著音樂時間（不含現場延遲）。',
                  loading: '下載並解碼音檔中…（第一次較久）',
                  playing: '音樂播放中，畫面跟隨音樂。按停止後畫面停在當下位置。',
                  silent: '後端沒有這首的音檔，改為無聲播放；按停止結束。',
                }[editor.preview]}
              </p>
            </div>
          )}

          <ol className="sequence-list show-sections">
            {draft.sections.map((section, index) => {
              const at = startOf(section)
              const backendHere = backendPlaying?.showId === draft.id && backendPlaying.sectionId === section.id
              return (
                <li key={section.id}>
                  <button type="button" className={section.id === selectedId ? 'active' : 'secondary'}
                    aria-pressed={section.id === selectedId} onClick={() => editor.setSelectedId(section.id)}>
                    <span>
                      {index + 1}. {at !== null ? formatClock(at) : '—'} · {describeStart(section.start)}
                      {enabled && currentIndex === index && <em className="playing-badge">預覽中</em>}
                      {backendHere && <em className="playing-badge">後端播放中</em>}
                    </span>
                    <small>
                      {getEffectManifest(section.preset.effectId).label}
                      {(['ball', 'wash'] as const).filter((group) => section.fixtures?.[group]).map((group) => (
                        ' · ' + GROUP_LABELS[group] + '：' + getFixtureManifest(section.fixtures![group]!.effectId).label
                      )).join('')}
                    </small>
                    {index > 0 && <small>進場：{describeTransition(section.transition)}</small>}
                  </button>
                </li>
              )
            })}
          </ol>
          <div className="button-row sequence-actions">
            <button type="button" onClick={() => editor.addSection(false)} disabled={draft.sections.length >= 256}>新增段落</button>
            <button type="button" className="secondary" onClick={() => editor.addSection(true)}
              disabled={draft.sections.length >= 256}>複製</button>
            <button type="button" className="secondary" onClick={editor.removeSection}
              disabled={selectedIndex === 0}>移除</button>
          </div>

          {selected && (
            <div className="sequence-detail">
              <strong>第 {selectedIndex + 1} 段設定</strong>
              {selectedIndex === 0 ? (
                <p className="control-note">第一段固定從 0 秒開始。</p>
              ) : (
                <>
                  <label className="sequence-field">
                    開始位置
                    <select value={'seconds' in selected.start ? 'seconds' : 'bar'} onChange={(event) => {
                      const at = startOf(selected) ?? 0
                      if (event.target.value === 'seconds') {
                        setStart(selected, { seconds: Math.round(at * 100) / 100 })
                      } else {
                        setStart(selected, { bar: 1 })
                      }
                    }}>
                      <option value="bar" disabled={!hasBeats}>依小節與拍</option>
                      <option value="seconds">依秒數</option>
                    </select>
                  </label>
                  {'seconds' in selected.start ? (
                    <label className="sequence-field">
                      開始（秒）
                      <EditableNumberInput key={selected.id + '-seconds'} value={selected.start.seconds}
                        min={0} max={beatMap?.durationSeconds ?? 3600} step={0.05}
                        onValue={(seconds) => setStart(selected, { seconds })} />
                    </label>
                  ) : (
                    <div className="show-bar-beat">
                      <label className="sequence-field">
                        第幾小節
                        <EditableNumberInput key={selected.id + '-bar'} value={selected.start.bar} min={0} max={9999} step={1} integer
                          onValue={(bar) => setStart(selected, { ...selected.start, bar })} />
                      </label>
                      <label className="sequence-field">
                        第幾拍
                        <EditableNumberInput key={selected.id + '-beat'} value={'bar' in selected.start ? selected.start.beat ?? 1 : 1}
                          min={1} max={beatMap?.beatsPerBar ?? 16} step={1} integer
                          onValue={(beat) => setStart(selected, beat === 1 ? { bar: 'bar' in selected.start ? selected.start.bar : 1 }
                            : { bar: 'bar' in selected.start ? selected.start.bar : 1, beat })} />
                      </label>
                    </div>
                  )}
                  <p className="sequence-duration">
                    {startOf(selected) !== null ? `從 ${formatClock(startOf(selected)!)} 開始` : '這個位置不在曲子的拍點內'}
                    {compiled.show ? `，長 ${(compiled.show.steps[selectedIndex].durationFrames / fps).toFixed(2)} 秒` : ''}。
                  </p>
                  <label className="sequence-field">
                    進場轉場
                    <select value={selected.transition.kind} onChange={(event) => {
                      const kind = event.target.value as TransitionKind
                      setTransition(selected, 'beats' in selected.transition
                        ? { kind, beats: selected.transition.beats } : { kind, seconds: selected.transition.seconds })
                    }}>
                      {(Object.keys(TRANSITION_LABELS) as TransitionKind[]).map((kind) => (
                        <option key={kind} value={kind}>{TRANSITION_LABELS[kind]}</option>
                      ))}
                    </select>
                  </label>
                  {selected.transition.kind !== 'cut' && (
                    <div className="show-bar-beat">
                      <label className="sequence-field">
                        長度
                        <EditableNumberInput key={selected.id + '-transition-' + ('beats' in selected.transition ? 'beats' : 'seconds')}
                          value={'beats' in selected.transition ? selected.transition.beats : selected.transition.seconds}
                          min={0} max={'beats' in selected.transition ? 32 : 10} step={'beats' in selected.transition ? 0.5 : 0.1}
                          onValue={(value) => setTransition(selected, 'beats' in selected.transition
                            ? { kind: selected.transition.kind, beats: value } : { kind: selected.transition.kind, seconds: value })} />
                      </label>
                      <label className="sequence-field">
                        單位
                        <select value={'beats' in selected.transition ? 'beats' : 'seconds'} onChange={(event) => {
                          const kind = selected.transition.kind
                          setTransition(selected, event.target.value === 'beats' ? { kind, beats: 2 } : { kind, seconds: 1 })
                        }}>
                          <option value="beats" disabled={!hasBeats}>拍</option>
                          <option value="seconds">秒</option>
                        </select>
                      </label>
                    </div>
                  )}
                  <p className="control-note">轉場從這段的開始位置（拍點上）起算。</p>
                </>
              )}
              <strong className="show-section-heading">
                第 {selectedIndex + 1} 段的效果
                {editor.overrideIndex !== null ? ` · 覆寫 ${editor.overrideIndex + 1}` : selected.overrides?.length ? ' · 主效果' : ''}
              </strong>
              {controlMode === 'simulation' && enabled && (
                <OverrideEditor editor={editor} section={selected} unitCount={unitCount} />
              )}
              {controlMode === 'simulation' && enabled ? sectionEditor : (
                <p className="control-note">
                  {controlMode === 'simulation' ? '勾選上方「在模擬畫面預覽這個演出」後，可在這裡直接改這段的效果與顏色。'
                    : '切換到「模擬」才能編輯段落效果。'}
                </p>
              )}
            </div>
          )}

          <div className="sequence-ending">
            <span className="control-label">曲子結束後</span>
            <div className="segmented-control" aria-label="演出結束方式">
              <button type="button" className={draft.ending.kind === 'loop' ? 'active' : 'secondary'}
                onClick={() => editor.setEnding({ kind: 'loop' })}>循環播放</button>
              <button type="button" className={draft.ending.kind === 'fade-black' ? 'active' : 'secondary'}
                onClick={() => editor.setEnding({ kind: 'fade-black', seconds: 2 })}>淡出後回到待機</button>
            </div>
            {draft.ending.kind === 'fade-black' && (
              <label className="sequence-field">
                淡出時間（秒）
                <EditableNumberInput value={draft.ending.seconds} min={0} max={10} step={0.1}
                  onValue={(seconds) => editor.setEnding({ kind: 'fade-black', seconds })} />
              </label>
            )}
          </div>

          {compiled.error && <p className="backend-error" role="alert">{compiled.error}</p>}
          {editor.error && <p className="backend-error" role="alert">{editor.error}</p>}
          <div className="button-row">
            <button type="button" disabled={editor.busy || !editor.dirty || !compiled.show}
              onClick={() => { void editor.save() }}>儲存演出</button>
            <button type="button" className="secondary" disabled={editor.busy}
              onClick={() => {
                if (window.confirm(editor.savedNew ? '放棄這個尚未儲存的演出？' : `刪除演出「${draft.title}」？`)) void editor.remove()
              }}>{editor.savedNew ? '放棄' : '刪除演出'}</button>
          </div>
          <p className="control-note">
            {editor.dirty ? '有尚未儲存的變更；儲存後才會同步到後端。' : '已儲存。'}
            後端正在播放這個演出時，儲存會直接換上新內容，音樂不中斷。
          </p>
        </>
      )}

      <div className="show-backend">
        <span className="control-label">在 Pi 上播放</span>
        {playingOnBackend ? (
          <p className="weather-override" role="status">
            播放中：{shows.shows.find((show) => show.id === playingOnBackend.showId)?.title ?? playingOnBackend.showId}
            {playingOnBackend.silent ? '（沒有音效卡，無聲）' : ''} · {formatClock(playingOnBackend.positionSeconds)} / {formatClock(playingOnBackend.durationSeconds)}
          </p>
        ) : startingOnBackend ? (
          <p className="weather-override" role="status">
            馬達已啟動，{Math.ceil(startingOnBackend.remainingSeconds)} 秒後開始：
            {shows.shows.find((show) => show.id === startingOnBackend.showId)?.title ?? startingOnBackend.showId}
          </p>
        ) : <p className="control-note">目前沒有播放演出；燈光為待機輸出（串接或單一效果）。</p>}
        <label className="blackout-groups">
          <span>
            <input type="checkbox" checked={motorOptions.startMotors}
              onChange={(event) => changeMotorOptions({ ...motorOptions, startMotors: event.target.checked })} />
            {' '}播放時同時啟動全部馬達
          </span>
        </label>
        {motorOptions.startMotors && (
          <>
            <label className="control-field">
              <span>演出延後開始（秒，0 = 同時開始）</span>
              <EditableNumberInput value={motorOptions.delaySeconds} min={0} max={SHOW_DELAY_MAX_SECONDS} step={1}
                onValue={(delaySeconds) => changeMotorOptions({ ...motorOptions, delaySeconds })} />
            </label>
            <p className="control-note">
              按下播放就啟動全部馬達（分區或全部一起，依「後端」分頁的設定），演出延後上面的秒數開始。
              不循環的演出結束、或按〔停止演出〕時馬達一起停；循環的演出、換播別的演出、套用效果時馬達不停。
            </p>
          </>
        )}
        <div className="button-row">
          <button type="button" disabled={editor.busy || !draft || editor.savedNew || editor.dirty}
            onClick={() => { if (draft) void editor.playOnBackend(draft.id, 0, motorOptions) }}>在 Pi 上播放這個演出</button>
          <button type="button" className="secondary" disabled={editor.busy || (!playingOnBackend && !startingOnBackend)}
            onClick={() => { void editor.stopOnBackend() }}>停止演出</button>
        </div>
        <p className="control-note">Pi 從 USB 音效卡播放音樂，燈光跟著音訊時鐘；套用效果、串接或手動播放音樂會停止演出。</p>
      </div>
    </div>
  )
}

/**
 * 轉燈覆寫：段落中某幾台轉燈改用別的效果。選一組覆寫後，效果與顏色分頁改的是那組；勾燈台編號或在 3D 畫面點轉燈加入／移除。
 * Spinner overrides: some spinners of a section play another effect. While one is selected the effect and colour tabs
 * edit it; tick unit numbers or click spinners in the 3D view to add or remove them.
 */
function OverrideEditor({ editor, section, unitCount }: { editor: ShowEditorState; section: ShowSection; unitCount: number }) {
  const overrides = section.overrides ?? []
  const current = editor.overrideIndex
  const ownerOf = (unit: number) => overrides.findIndex((override) => override.units.includes(unit))
  const effectLabel = (effectId: Parameters<typeof getEffectManifest>[0]) => getEffectManifest(effectId).label.replace(/^.* \/ /, '')
  return (
    <div className="show-overrides">
      <div className="show-override-list" role="group" aria-label="轉燈覆寫">
        <button type="button" className={current === null ? 'active' : 'secondary'} aria-pressed={current === null}
          onClick={() => editor.selectOverride(null)}>
          主效果 · {effectLabel(section.preset.effectId)}
        </button>
        {overrides.map((override, index) => (
          <span key={index} className="show-override-item">
            <button type="button" className={current === index ? 'active' : 'secondary'} aria-pressed={current === index}
              onClick={() => editor.selectOverride(index)}>
              覆寫 {index + 1} · {effectLabel(override.preset.effectId)} · {override.units.length ? `轉燈 ${override.units.join('、')}` : '尚未選轉燈'}
            </button>
            <button type="button" className="secondary" aria-label={`刪除覆寫 ${index + 1}`}
              onClick={() => editor.removeOverride(index)}>✕</button>
          </span>
        ))}
        <button type="button" className="secondary" onClick={editor.addOverride}>＋ 新增覆寫</button>
      </div>
      {current !== null && overrides[current] && (
        <>
          <p className="control-note">勾選的轉燈在這段改用覆寫 {current + 1} 的效果；也可以直接在 3D 畫面點轉燈加入／移除（已在別組的會移過來）。沒選轉燈的覆寫不會儲存。</p>
          <div className="override-units" role="group" aria-label={`覆寫 ${current + 1} 的轉燈`}>
            {Array.from({ length: unitCount }, (_, index) => index + 1).map((unit) => {
              const owner = ownerOf(unit)
              return (
                <label key={unit} className={owner >= 0 && owner !== current ? 'override-unit taken' : 'override-unit'}
                  title={owner >= 0 && owner !== current ? `目前在覆寫 ${owner + 1}` : undefined}>
                  <input type="checkbox" checked={owner === current} onChange={() => editor.toggleOverrideUnit(unit)} />
                  <span>{unit}</span>
                </label>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
