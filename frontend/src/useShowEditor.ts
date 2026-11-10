import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  deleteShow, getAudioState, getBeatMap, getShows, playShow, revertBeatMap, saveBeatMap, saveShow, stopShow,
  type AudioTrack, type ShowsState,
} from './backendApi'
import { computePeaks, finishBeatMap, type Peaks } from './simulation/beatEdit'
import { createDefaultEffectPreset } from './effects'
import type { StepFixtures } from './simulation/sequence'
import {
  barBeatAt, compileShow, showFrameAt, startSeconds, type BeatMap, type CompiledShow, type ShowDocument, type ShowEnding,
  type ShowSection,
} from './simulation/show'
import { TrackPlayer } from './simulation/trackPlayer'

/** 段落中與效果編輯共用的部分（同串接的 step）。 / The part of a section the effect editors change (as sequence steps). */
export type PresetHolder = Pick<ShowSection, 'preset' | 'fixtures'>

export type PreviewAudio = 'idle' | 'loading' | 'playing' | 'silent'

/** 一個不重複的演出 id（取自曲目 id）。 / A unique show id derived from the track id. */
function uniqueId(base: string, taken: readonly string[]): string {
  const root = base.replace(/[^a-z0-9-]/g, '-').replace(/^-+/, '').slice(0, 36) || 'show'
  if (!taken.includes(root)) return root
  let index = 2
  while (taken.includes(`${root}-${index}`)) index += 1
  return `${root}-${index}`
}

function sortedSections(sections: ShowSection[], beatMap: BeatMap | undefined): ShowSection[] {
  if (!beatMap) return sections
  const keyed = sections.map((section, index) => {
    let seconds: number
    try { seconds = startSeconds(section.start, beatMap) } catch { seconds = Number.POSITIVE_INFINITY }
    return { section, seconds: index === 0 ? -1 : seconds, index }
  })
  keyed.sort((a, b) => a.seconds - b.seconds || a.index - b.index)
  return keyed.map((entry) => entry.section)
}

/**
 * @param onEdited 演出改變後重新預覽（從某段開始）。 / Restart the preview after an edit (from a section).
 * @param onSeek 時間軸點選跳轉時，模擬時鐘跳到這一秒。 / Move the simulator clock when the timeline seeks.
 */
export function useShowEditor(fps: number, onEdited: (sectionId: number | null) => void, onSeek: (seconds: number) => void) {
  const [shows, setShows] = useState<ShowsState | null | undefined>(undefined)
  const [tracks, setTracks] = useState<AudioTrack[]>([])
  const [beatMaps, setBeatMaps] = useState<Record<string, BeatMap>>({})
  const [draft, setDraft] = useState<ShowDocument | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState(1)
  // 正在編輯的轉燈覆寫（選取段落的第幾組；null = 主效果）。 / The override being edited (index in the selected section).
  const [overrideIndex, setOverrideIndex] = useState<number | null>(null)
  useEffect(() => { setOverrideIndex(null) }, [selectedId])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<PreviewAudio>('idle')
  const [refresh, setRefresh] = useState(0)
  const player = useRef<TrackPlayer | null>(null)
  const nextId = useRef(2)
  // 拍點修正中的草稿（M14）；編譯與預覽用草稿，儲存後才寫到後端。 / Beat corrections in progress; previews use them.
  const [beatDraft, setBeatDraft] = useState<BeatMap | null>(null)
  const [peaks, setPeaks] = useState<Record<string, Peaks | 'loading' | 'missing'>>({})

  const loadBeatMap = useCallback((trackId: string) => {
    if (beatMaps[trackId]) return
    void getBeatMap(trackId).then((map) => setBeatMaps((current) => ({ ...current, [trackId]: map })),
      (failure) => setError(failure instanceof Error ? failure.message : '無法讀取拍點檔'))
  }, [beatMaps])

  useEffect(() => {
    let active = true
    void Promise.all([getShows(), getAudioState()]).then(([state, audio]) => {
      if (!active) return
      setShows(state)
      setTracks(audio?.tracks ?? [])
    }).catch(() => { if (active) setShows(null) })
    return () => { active = false }
  }, [refresh])

  // 播放中的演出需要拍點檔（3D 鏡像）；編輯中的也是。 / The playing and the edited show need their beat maps.
  useEffect(() => {
    if (shows?.playing) loadBeatMap(shows.playing.trackId)
    if (draft) loadBeatMap(draft.trackId)
  }, [shows?.playing?.trackId, draft?.trackId, loadBeatMap])

  const shippedMap = draft ? beatMaps[draft.trackId] : undefined
  useEffect(() => {
    if (shippedMap && beatDraft?.id !== shippedMap.id) setBeatDraft(shippedMap)
  }, [shippedMap, beatDraft?.id])
  const beatMap = beatDraft && beatDraft.id === draft?.trackId ? beatDraft : shippedMap
  const beatsDirty = beatDraft !== null && shippedMap !== undefined && beatDraft.id === shippedMap.id
    && JSON.stringify(beatDraft) !== JSON.stringify(shippedMap)
  const compiled = useMemo((): { show: CompiledShow | null; error: string | null } => {
    if (!draft || !beatMap) return { show: null, error: null }
    try {
      return { show: compileShow(draft, beatMap, fps), error: null }
    } catch (failure) {
      return { show: null, error: failure instanceof Error ? failure.message : String(failure) }
    }
  }, [draft, beatMap, fps])

  const stopPreview = useCallback(() => {
    player.current?.stop()
    setPreview('idle')
  }, [])
  useEffect(() => () => player.current?.stop(), [])

  const open = (show: ShowDocument) => {
    stopPreview()
    setDraft(structuredClone(show))
    setSaved(JSON.stringify(show))
    setSelectedId(show.sections[0].id)
    nextId.current = Math.max(...show.sections.map((section) => section.id)) + 1
    setError(null)
    onEdited(show.sections[0].id)
  }

  const create = (trackId: string) => {
    const track = tracks.find((item) => item.id === trackId)
    stopPreview()
    const show: ShowDocument = {
      schemaVersion: 1,
      id: uniqueId(trackId, shows?.shows.map((item) => item.id) ?? []),
      title: track?.title ?? trackId,
      trackId,
      ending: { kind: 'loop' },
      sections: [{
        id: 1, start: { seconds: 0 }, preset: createDefaultEffectPreset('center-out-sine'),
        transition: { kind: 'cut', seconds: 0 },
      }],
    }
    setDraft(show)
    setSaved(null)
    setSelectedId(1)
    nextId.current = 2
    setError(null)
    onEdited(1)
  }

  const edit = (change: (show: ShowDocument) => ShowDocument, sectionId: number | null = selectedId) => {
    setDraft((current) => current ? change(current) : current)
    onEdited(sectionId)
  }

  const updateSection = (id: number, change: (section: ShowSection) => ShowSection) => {
    edit((show) => ({
      ...show,
      sections: sortedSections(show.sections.map((section) => section.id === id ? change(section) : section), beatMap),
    }), id)
  }

  /**
   * 效果、顏色與燈具編輯器改的是選取段落的 preset／fixtures；正在編輯覆寫時，轉燈效果改的是那組覆寫。
   * The effect editors change the selected section; while an override is selected, the spinner effect goes to it.
   */
  const updateSelectedPresets = (change: (holder: PresetHolder) => PresetHolder) => {
    updateSection(selectedId, (section) => {
      const { fixtures: _old, ...base } = section
      const override = overrideIndex !== null ? section.overrides?.[overrideIndex] : undefined
      const next = change({ preset: override?.preset ?? section.preset, ...(section.fixtures ? { fixtures: section.fixtures } : {}) })
      const fixtures: StepFixtures | undefined = next.fixtures && Object.keys(next.fixtures).length ? next.fixtures : undefined
      const overrides = override
        ? section.overrides!.map((entry, index) => index === overrideIndex ? { ...entry, preset: next.preset } : entry)
        : section.overrides
      return {
        ...base,
        preset: override ? section.preset : next.preset,
        ...(fixtures ? { fixtures } : {}),
        ...(overrides?.length ? { overrides } : {}),
      }
    })
  }

  const selectedSection = draft?.sections.find((section) => section.id === selectedId) ?? draft?.sections[0]
  const editedOverride = overrideIndex !== null ? selectedSection?.overrides?.[overrideIndex] : undefined

  const setOverrides = (change: (overrides: NonNullable<ShowSection['overrides']>) => NonNullable<ShowSection['overrides']>) => {
    updateSection(selectedId, (section) => {
      const { overrides: _old, ...base } = section
      const overrides = change(section.overrides ?? [])
      return overrides.length ? { ...base, overrides } : base
    })
  }

  /** 新增一組覆寫（先用這段的效果），並開始編輯它。 / Add an override (starting from the section's effect) and edit it. */
  const addOverride = () => {
    if (!selectedSection) return
    const index = selectedSection.overrides?.length ?? 0
    setOverrides((overrides) => [...overrides, { units: [], preset: structuredClone(selectedSection.preset) }])
    setOverrideIndex(index)
  }

  const removeOverride = (index: number) => {
    setOverrides((overrides) => overrides.filter((_, position) => position !== index))
    setOverrideIndex(null)
  }

  /** 把一台轉燈加入／移出正在編輯的覆寫；加入時會從其他覆寫移走。 / Toggle a unit in the edited override, moving it from others. */
  const toggleOverrideUnit = (unit: number) => {
    if (overrideIndex === null) return
    setOverrides((overrides) => overrides.map((entry, index) => {
      if (index === overrideIndex) {
        return { ...entry, units: entry.units.includes(unit) ? entry.units.filter((value) => value !== unit) : [...entry.units, unit].sort((a, b) => a - b) }
      }
      return entry.units.includes(unit) && !overrides[overrideIndex].units.includes(unit)
        ? { ...entry, units: entry.units.filter((value) => value !== unit) }
        : entry
    }))
  }

  const addSection = (duplicate: boolean) => {
    if (!draft) return
    const selected = draft.sections.find((section) => section.id === selectedId) ?? draft.sections[0]
    const id = nextId.current++
    // 預設放在選取段落之後 4 小節（沒有拍點就往後 4 秒）。 / Default: 4 bars after the selected section (4 s without beats).
    let start: ShowSection['start'] = { seconds: 0 }
    if (beatMap) {
      const at = startSeconds(selected.start, beatMap)
      // 第一拍之前（弱起）當作第 1 小節。 / Before the first beat (pickup) counts as bar 1.
      const bar = barBeatAt(beatMap, at)?.bar ?? 1
      start = beatMap.beats.length >= beatMap.beatsPerBar
        ? { bar: Math.max(1, bar) + 4 }
        : { seconds: Math.round(Math.min(beatMap.durationSeconds - 0.1, at + 4) * 100) / 100 }
    }
    const base = duplicate ? structuredClone(selected) : {
      preset: structuredClone(selected.preset),
      transition: beatMap && beatMap.beats.length >= 2
        ? { kind: 'crossfade' as const, beats: 2 } : { kind: 'crossfade' as const, seconds: 1 },
    }
    const section: ShowSection = { ...base, id, start }
    edit((show) => ({ ...show, sections: sortedSections([...show.sections, section], beatMap) }), id)
    setSelectedId(id)
  }

  const removeSection = () => {
    if (!draft || draft.sections.length <= 1 || draft.sections[0].id === selectedId) return
    const index = draft.sections.findIndex((section) => section.id === selectedId)
    const next = draft.sections.filter((section) => section.id !== selectedId)
    const nextSelected = next[Math.min(index, next.length - 1)].id
    edit((show) => ({ ...show, sections: next }), nextSelected)
    setSelectedId(nextSelected)
  }

  const run = async (request: () => Promise<ShowsState>) => {
    setBusy(true)
    setError(null)
    try {
      const state = await request()
      setShows(state)
      return state
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '演出操作失敗')
      return null
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!draft) return
    const sent = draft
    const state = await run(() => saveShow(sent))
    const stored = state?.shows.find((show) => show.id === sent.id)
    if (!stored) return
    // 改用後端的標準形式（欄位順序、預設值），之後才比得出有沒有修改；儲存期間又改過就保留編輯。
    // Adopt the backend's canonical form so later dirty checks compare like with like; keep edits made meanwhile.
    setSaved(JSON.stringify(stored))
    setDraft((current) => current === sent ? stored : current)
  }

  const remove = async () => {
    if (!draft) return
    if (saved !== null) {
      const state = await run(() => deleteShow(draft.id))
      if (state === null) return
    }
    stopPreview()
    setDraft(null)
    setSaved(null)
  }

  /** 在瀏覽器播放音樂預覽（從某段開始）；音檔不在主機上時無聲播放。 / Preview with music from a section; silent without the WAV. */
  const playPreview = async (fromSeconds: number) => {
    if (!draft || !beatMap || !compiled.show) return
    player.current ??= new TrackPlayer()
    setPreview('loading')
    try {
      const result = await player.current.load(beatMap)
      if (result === 'missing') {
        setPreview('silent')
        return
      }
      player.current.play(beatMap.id, fromSeconds, compiled.show.loop)
      setPreview('playing')
    } catch (failure) {
      setPreview('idle')
      setError(failure instanceof Error ? failure.message : '無法播放音樂')
    }
  }

  /** 時間軸點選：音樂播放中就從那裡繼續播。 / Timeline seek: keeps the music playing from there. */
  const seek = (seconds: number) => {
    const time = Math.max(0, Math.min(seconds, beatMap?.durationSeconds ?? seconds))
    if (preview === 'playing') void playPreview(time)
    onSeek(time)
  }

  /** 波形：解碼音檔後取峰值（音檔與預覽共用）。 / Waveform: peaks of the decoded track (shared with the preview). */
  const loadWaveform = useCallback(async (map: BeatMap) => {
    if (peaks[map.id]) return
    setPeaks((current) => ({ ...current, [map.id]: 'loading' }))
    player.current ??= new TrackPlayer()
    try {
      const result = await player.current.load(map)
      const buffer = player.current.buffer(map.id)
      if (result === 'missing' || !buffer) {
        setPeaks((current) => ({ ...current, [map.id]: 'missing' }))
        return
      }
      const channels = Array.from({ length: buffer.numberOfChannels }, (_, index) => buffer.getChannelData(index))
      setPeaks((current) => ({ ...current, [map.id]: computePeaks(channels, buffer.sampleRate) }))
    } catch (failure) {
      setPeaks((current) => ({ ...current, [map.id]: 'missing' }))
      setError(failure instanceof Error ? failure.message : '無法讀取音檔')
    }
  }, [peaks])

  const beatRequest = async (request: () => Promise<BeatMap>) => {
    setBusy(true)
    setError(null)
    try {
      const map = await request()
      setBeatMaps((current) => ({ ...current, [map.id]: map }))
      setBeatDraft(map)
      setRefresh((current) => current + 1)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '拍點操作失敗')
    } finally {
      setBusy(false)
    }
  }

  /** 播放位置（秒，循環時折回曲長內）；音樂沒在播放回 null。 / Music position in the track; null when not playing. */
  const audioSeconds = useCallback((): number | null => {
    const seconds = player.current?.position() ?? null
    const show = showRef.current
    if (seconds === null || show === null) return null
    return show.loop ? seconds % show.durationSeconds : seconds
  }, [])

  /** 模擬時鐘跟隨的影格（瀏覽器音樂播放中才有）。 / Frame for the simulator clock while the browser plays the music. */
  const showRef = useRef(compiled.show)
  showRef.current = compiled.show
  const previewFrame = useCallback((): number | null => {
    const seconds = player.current?.position() ?? null
    const show = showRef.current
    if (seconds === null || show === null) return null
    if (!show.loop && seconds >= show.durationSeconds) return null
    return showFrameAt(show, seconds, fps)
  }, [fps])

  // 曲子播完（不循環）後回到閒置。 / Back to idle once a non-looping track ends.
  useEffect(() => {
    if (preview !== 'playing') return
    const timer = window.setInterval(() => {
      if (player.current?.playingTrack() == null) setPreview('idle')
    }, 500)
    return () => window.clearInterval(timer)
  }, [preview])

  const dirty = draft !== null && (saved === null || JSON.stringify(draft) !== saved)

  return {
    shows, tracks, beatMaps, beatMap, loadBeatMap, draft, dirty, selectedId, busy, error, compiled, preview, previewFrame,
    setSelectedId, open, create, edit, updateSection, updateSelectedPresets, addSection, removeSection,
    // 轉燈覆寫 / Spinner overrides
    overrideIndex: editedOverride ? overrideIndex : null,
    editedPreset: editedOverride?.preset ?? selectedSection?.preset ?? null,
    selectOverride: setOverrideIndex, addOverride, removeOverride, toggleOverrideUnit,
    setEnding: (ending: ShowEnding) => edit((show) => ({ ...show, ending }), lastSectionId(draft)),
    setTitle: (title: string) => setDraft((current) => current ? { ...current, title } : current),
    setId: (id: string) => setDraft((current) => current ? { ...current, id } : current),
    save, remove, playPreview, stopPreview, seek, audioSeconds,
    // 拍點修正 / Beat corrections
    beatsDirty, peaks, loadWaveform,
    editBeats: (change: (map: BeatMap) => BeatMap) => setBeatDraft((current) => current ? change(current) : current),
    discardBeats: () => { if (shippedMap) setBeatDraft(shippedMap) },
    saveBeats: () => beatDraft ? beatRequest(() => saveBeatMap(finishBeatMap(beatDraft))) : Promise.resolve(),
    revertBeats: () => draft ? beatRequest(() => revertBeatMap(draft.trackId)) : Promise.resolve(),
    reload: () => setRefresh((current) => current + 1),
    playOnBackend: (showId: string, from = 0) => run(() => playShow(showId, from)),
    stopOnBackend: () => run(stopShow),
    savedNew: saved === null,
  }
}

function lastSectionId(draft: ShowDocument | null): number | null {
  return draft?.sections.at(-1)?.id ?? null
}

export type ShowEditorState = ReturnType<typeof useShowEditor>
