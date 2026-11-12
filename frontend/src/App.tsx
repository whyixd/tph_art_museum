import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { applyBackendPreset, getBackendPatch, getBackendPlayback, getBackendSequence, getBackendState, getBackendWind, getWeatherState, restartBackendPlayback, saveBackendSequence, setBackendBlackout, setBackendMasterLevel, setBackendMotor, setBackendMotorStagger, setBackendStandby, setBackendWindPresets, setBackendWindTarget, type BackendPatch, type BackendWind, type BackendWindPresets, type BlackoutGroup, type BackendPlayback, type BackendState, type WeatherState } from './backendApi'
import { ControlPanel, type EditGroup } from './components/ControlPanel'
import type { WindGroup, WindPresets } from './components/WindPanel'
import { getWindTarget, setWindTarget, subscribeWindPreview } from './wind/windPreview'
import type { FixtureGroup } from './components/FixtureEditor'
import { createDefaultFixturePreset, type FixturePreset } from './fixtures'
import { EXAMPLE_LAYOUT, siteLayout, type LayoutDocument, type LayoutPosition } from './site/layout'
import { DEFAULT_PIXEL_ROTATION } from './site/spinnerGeometry'
import {
  createDefaultEffectPreset,
  EFFECT_MANIFEST,
  getEffectManifest,
  type EffectColor,
  type EffectId,
  type EffectPreset,
  type UnitVariation,
} from './effects'
import {
  DEFAULT_SETTINGS,
  degreesPerControllerFrame,
  type SimulationSettings,
} from './simulation/model'
import { liveBackendFrame } from './playbackProgress'
import { compileSequence, createSequenceStep, defaultPlaybackRule, resolveSequenceFrame, sequenceSeekFrame, type SequenceSeek, type PlaybackRule, type SequenceDocument, type SequenceEnding, type SequenceStep, type TransitionKind } from './simulation/sequence'
import { SimulationCanvas } from './simulation/scene/SimulationCanvas'
import { compileShow } from './simulation/show'
import { useShowEditor } from './useShowEditor'
import { ShowTimeline } from './components/ShowTimeline'
import type { Diagnostics, FixturePresets, FixtureSelection, GroupVisibility, SceneView } from './simulation/scene/SiteScene'

const VIEW_LABELS: [SceneView, string][] = [['single', '單台'], ['site', '全場'], ['top', '上視']]
const NO_FIXTURES: FixturePresets = { ball: null, wash: null }

/** Layout from the backend patch when it carries positions, else the example layout shipped with the frontend. */
function layoutDocument(patch: BackendPatch | null): LayoutDocument {
  if (!patch) return EXAMPLE_LAYOUT
  const entries = (items: { id: number; position: LayoutPosition | null | undefined }[]) =>
    Object.fromEntries(items.filter((item) => item.position).map((item) => [String(item.id), item.position!]))
  const spinners = entries((patch.spinnerPositions ?? []).map((position, index) => ({ id: index + 1, position })))
  const ball = entries(patch.fixtures.ball)
  const wash = entries(patch.fixtures.wash)
  if (Object.keys(spinners).length + Object.keys(ball).length + Object.keys(wash).length === 0) return EXAMPLE_LAYOUT
  return { schemaVersion: 1, spinners, fixtures: { ball, wash }, poles: patch.poles ?? [] }
}

const INITIAL_DIAGNOSTICS: Diagnostics = {
  angleDegrees: 0,
  controllerFrame: 0,
  globalFrame: 0,
  cyclePhase: 0,
  simulationTime: 0,
}

/** 後端的風流設定 ↔ 前端的 preset。 / Backend wind-flow presets to and from frontend presets. */
function fromBackendWind(presets: BackendWindPresets): WindPresets {
  const fixture = (group: 'ball' | 'wash'): FixturePreset => {
    const { parameters, colors, maxLevel } = presets[group]
    return {
      schemaVersion: 1, effectId: 'wind-flow', parameters, color: { mode: 'gradient', axis: 'x', colors },
      ...(maxLevel !== undefined && maxLevel < 1 ? { maxLevel } : {}),
    }
  }
  return {
    spinner: {
      ...createDefaultEffectPreset('wind-flow'), parameters: presets.spinner.parameters,
      color: { mode: 'gradient', axis: 'radius', colors: presets.spinner.colors },
    },
    ball: fixture('ball'),
    wash: fixture('wash'),
  }
}

function toBackendWind(presets: WindPresets): BackendWindPresets {
  const colors = (preset: WindPresets[WindGroup]) => (preset.color.mode === 'gradient' ? preset.color.colors : [preset.color.color, preset.color.color])
  return {
    spinner: { parameters: presets.spinner.parameters, colors: colors(presets.spinner) },
    ball: { parameters: presets.ball.parameters, colors: colors(presets.ball), maxLevel: presets.ball.maxLevel ?? 1 },
    wash: { parameters: presets.wash.parameters, colors: colors(presets.wash), maxLevel: presets.wash.maxLevel ?? 1 },
  }
}

export function App() {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [effectId, setEffectId] = useState<EffectId>('center-out-sine')
  const [effectPresets, setEffectPresets] = useState(() => Object.fromEntries(
    EFFECT_MANIFEST.map(({ id }) => [id, createDefaultEffectPreset(id, DEFAULT_SETTINGS.ledCount)]),
  ) as Record<EffectId, ReturnType<typeof createDefaultEffectPreset>>)
  const [sequenceEnabled, setSequenceEnabled] = useState(false)
  // 待機：模擬模式下不管單一效果／串接／演出，全場改播風流（風力原型，只在前端）。
  // Standby: in simulation the whole site plays wind-flow, whatever the single effect / sequence / show is (frontend only).
  const [standby, setStandby] = useState(false)
  // 風流（待機效果）三個群組的設定，在「風力」分頁調整。 / wind-flow (standby) presets, edited in the wind tab.
  const [windPresets, setWindPresets] = useState<WindPresets>(() => ({
    spinner: createDefaultEffectPreset('wind-flow', DEFAULT_SETTINGS.ledCount),
    ball: createDefaultFixturePreset('wind-flow'), wash: createDefaultFixturePreset('wind-flow'),
  }))
  const [sequenceSteps, setSequenceSteps] = useState<SequenceStep[]>(() => [
    createSequenceStep(1, createDefaultEffectPreset('center-out-sine')),
    createSequenceStep(2, createDefaultEffectPreset('sequential-center-fill')),
  ])
  const [selectedStepId, setSelectedStepId] = useState(1)
  const [sequenceEnding, setSequenceEnding] = useState<SequenceEnding>({ kind: 'hold' })
  const [savedSequence, setSavedSequence] = useState<string | null>(null)
  const [sequenceConnected, setSequenceConnected] = useState(false)
  const [sequencePlaybackMode, setSequencePlaybackMode] = useState<'sequence' | 'single'>('sequence')
  const [sequenceBusy, setSequenceBusy] = useState(false)
  const [sequenceRefresh, setSequenceRefresh] = useState(0)
  const [sequenceError, setSequenceError] = useState<string | null>(null)
  const sequenceRequest = useRef(0)
  const sequenceEditRevision = useRef(0)
  const sequenceEdited = useRef(false)
  const nextStepId = useRef(3)
  const [sequenceRevision, setSequenceRevision] = useState(0)
  const [diagnostics, setDiagnostics] = useState(INITIAL_DIAGNOSTICS)
  const [paused, setPaused] = useState(false)
  const [resetToken, setResetToken] = useState(0)
  const [view, setView] = useState<SceneView>('site')
  const [selectedUnit, setSelectedUnit] = useState(0)
  const [showStructure, setShowStructure] = useState(true)
  // 演出時間軸可收起，讓小螢幕（iPad mini）把高度留給 3D 畫面。 / The show timeline can be hidden so small screens keep the 3D view.
  const [timelineVisible, setTimelineVisible] = useState(true)
  const [visible, setVisible] = useState<GroupVisibility>({ spinner: true, ball: true, wash: true })
  const [selectedFixture, setSelectedFixture] = useState<FixtureSelection>(null)
  const [editGroup, setEditGroup] = useState<EditGroup>('spinner')
  // 單一效果模式的球燈／洗牆燈 preset；null = 全暗。 / Single-effect ball / wash presets; null = dark.
  const [fixturePresets, setFixturePresets] = useState<FixturePresets>(NO_FIXTURES)
  // 後端的輸出設定與 patch（座標、燈具數）；沒有後端時用範例。 / Backend output and patch; the example without a backend.
  const [siteInfo, setSiteInfo] = useState<{ state: BackendState | null; patch: BackendPatch | null }>({ state: null, patch: null })
  const [controlMode, setControlMode] = useState<'simulation' | 'backend'>('simulation')
  const [backendState, setBackendState] = useState<BackendState | null>(null)
  const [backendBusy, setBackendBusy] = useState(false)
  const [backendWind, setBackendWind] = useState<BackendWind | null>(null)
  const backendWindRef = useRef(backendWind)
  backendWindRef.current = backendWind
  const [backendError, setBackendError] = useState<string | null>(null)
  const [backendRefresh, setBackendRefresh] = useState(0)
  const [weather, setWeather] = useState<WeatherState | null>(null)
  const [playback, setPlayback] = useState<{ state: BackendPlayback | null; receivedAt: number; unsupported: boolean }>(
    { state: null, receivedAt: 0, unsupported: false },
  )
  const [playbackError, setPlaybackError] = useState<string | null>(null)
  const [playbackPoll, setPlaybackPoll] = useState(0)
  const backendRequest = useRef(0)
  const exampleCounts = {
    ball: Object.keys(EXAMPLE_LAYOUT.fixtures?.ball ?? {}).length,
    wash: Object.keys(EXAMPLE_LAYOUT.fixtures?.wash ?? {}).length,
  }
  const fixtureCounts: Record<FixtureGroup, number> = siteInfo.state?.fixtures
    ? { ball: siteInfo.state.fixtures.ball.count, wash: siteInfo.state.fixtures.wash.count }
    : exampleCounts
  const unitCount = siteInfo.state?.output.unitCount ?? Object.keys(EXAMPLE_LAYOUT.spinners ?? {}).length
  const layout = useMemo(
    () => siteLayout(layoutDocument(siteInfo.patch), unitCount, fixtureCounts),
    [siteInfo.patch, unitCount, fixtureCounts.ball, fixtureCounts.wash],
  )
  const pixelRotation = siteInfo.state?.output.pixelRotation ?? DEFAULT_PIXEL_ROTATION
  const sequenceDocument: SequenceDocument = { schemaVersion: 1, ending: sequenceEnding, steps: sequenceSteps }
  const sequenceDirty = savedSequence === null || JSON.stringify(sequenceDocument) !== savedSequence
  const markSequenceEdited = () => {
    sequenceEdited.current = true
    sequenceEditRevision.current += 1
  }
  // 重新預覽時跳到的位置：修改哪一段就從那一段（或它的銜接）開始。
  // Where a preview restart jumps to: the edited step (or its transition) instead of the beginning.
  const seekTarget = useRef<SequenceSeek>({ kind: 'start' })
  const resetSequencePreview = useCallback((seek: SequenceSeek = { kind: 'start' }) => {
    seekTarget.current = seek
    setSequenceRevision((current) => current + 1)
    setDiagnostics(INITIAL_DIAGNOSTICS)
  }, [])

  // 演出（M12）：在模擬中預覽時，效果與顏色分頁改的是選取的段落。 / Show preview: the effect tabs edit the selected section.
  const [showEnabled, setShowEnabled] = useState(false)
  const showEditor = useShowEditor(settings.controllerFps, (sectionId) => {
    resetSequencePreview(sectionId === null ? { kind: 'start' } : { kind: 'step', id: sectionId })
  }, (seconds) => resetSequencePreview({ kind: 'frame', frame: seconds * settings.controllerFps }))
  const selectedSection = showEditor.draft?.sections.find((section) => section.id === showEditor.selectedId)
    ?? showEditor.draft?.sections[0]
  const inShow = controlMode === 'simulation' && showEnabled && selectedSection !== undefined
  const previewShow = inShow ? showEditor.compiled.show : null
  const inSequence = controlMode === 'simulation' && sequenceEnabled && !inShow
  const { stopPreview } = showEditor
  // 暫停畫面或離開模擬時停止瀏覽器音樂。 / Pausing the view or leaving the simulator stops the browser music.
  useEffect(() => { if (paused || controlMode !== 'simulation') stopPreview() }, [paused, controlMode, stopPreview])
  const savedDocument = useMemo(
    () => savedSequence === null ? null : JSON.parse(savedSequence) as SequenceDocument,
    [savedSequence],
  )
  const backendPlayingStepId = playback.state?.active && playback.state.mode === 'sequence' && !playback.state.ended
    ? playback.state.step.id : null
  // 只在編輯器內容與後端已儲存的串接一致時標示播放段落。 / Mark the playing step only when the editor matches the saved sequence.
  const playingStepId = sequenceEnabled && !sequenceDirty ? backendPlayingStepId : null
  const selectedStep = sequenceSteps.find((step) => step.id === selectedStepId) ?? sequenceSteps[0]
  // 演出中正在編輯轉燈覆寫時，效果與顏色分頁顯示那組覆寫。 / While editing an override, the effect tabs show it.
  const effectPreset = inShow ? showEditor.editedPreset ?? selectedSection!.preset : inSequence ? selectedStep.preset : effectPresets[effectId]
  const editingOverride = inShow && showEditor.overrideIndex !== null
  const overrideUnits = editingOverride
    ? (selectedSection!.overrides?.[showEditor.overrideIndex!]?.units ?? []).map((unit) => unit - 1) : null
  const editedFixtures: FixturePresets = inShow
    ? { ball: selectedSection!.fixtures?.ball ?? null, wash: selectedSection!.fixtures?.wash ?? null }
    : inSequence
      ? { ball: selectedStep.fixtures?.ball ?? null, wash: selectedStep.fixtures?.wash ?? null }
      : fixturePresets
  const sequenceSchedule = useMemo(
    () => compileSequence(sequenceSteps, settings.controllerFps, unitCount, sequenceEnding, fixtureCounts),
    [sequenceSteps, settings.controllerFps, unitCount, sequenceEnding, fixtureCounts.ball, fixtureCounts.wash],
  )
  const currentSequenceFrame = resolveSequenceFrame(sequenceSchedule, diagnostics.globalFrame)!

  // 後端控制時 3D 畫面顯示後端實際輸出：播放中的串接或已套用的效果，時間跟隨 /api/playback。
  // In backend mode the 3D view mirrors the live output: the saved sequence or applied preset, timed by /api/playback.
  const inBackend = controlMode === 'backend'
  const liveMode = playback.state?.active ? playback.state.mode : sequencePlaybackMode
  const backendSchedule = useMemo(
    () => savedDocument === null ? null : compileSequence(
      savedDocument.steps, settings.controllerFps, unitCount, savedDocument.ending ?? { kind: 'hold' }, fixtureCounts,
    ),
    [savedDocument, settings.controllerFps, unitCount, fixtureCounts.ball, fixtureCounts.wash],
  )
  // 後端播放演出時，以已儲存的演出與拍點檔編出同一張影格表。 / A backend show is mirrored from the saved show and beat map.
  const liveShowId = playback.state?.active && playback.state.mode === 'show' ? playback.state.show.id : null
  const liveShowTrack = playback.state?.active && playback.state.mode === 'show' ? playback.state.show.trackId : null
  const liveShowDocument = liveShowId ? showEditor.shows?.shows.find((show) => show.id === liveShowId) ?? null : null
  const liveShowBeatMap = liveShowTrack ? showEditor.beatMaps[liveShowTrack] : undefined
  const { loadBeatMap } = showEditor
  useEffect(() => { if (inBackend && liveShowTrack) loadBeatMap(liveShowTrack) }, [inBackend, liveShowTrack, loadBeatMap])
  const backendShowSchedule = useMemo(() => {
    if (!liveShowDocument || !liveShowBeatMap) return null
    try { return compileShow(liveShowDocument, liveShowBeatMap, settings.controllerFps) } catch { return null }
  }, [liveShowDocument, liveShowBeatMap, settings.controllerFps])
  const viewSequence = inBackend
    ? liveMode === 'sequence' ? backendSchedule : liveMode === 'show' ? backendShowSchedule : null
    : previewShow ?? (inSequence ? sequenceSchedule : null)
  // 後端控制：待機與風流設定以後端為準。 / Backend control: standby and the wind-flow presets come from the backend.
  const backendWindPresets = useMemo(() => (backendWind ? fromBackendWind(backendWind.presets) : null), [backendWind])
  const shownWindPresets = inBackend && backendWindPresets ? backendWindPresets : windPresets
  const inStandby = inBackend ? backendWind?.standby === true : standby
  const viewPreset = inStandby ? shownWindPresets.spinner : inBackend && backendState ? backendState.preset : effectPreset
  const backendFixtures: FixturePresets | null = backendState?.fixtures
    ? { ball: backendState.fixtures.ball.preset, wash: backendState.fixtures.wash.preset } : null
  const viewFixtures = inStandby ? shownWindPresets : inBackend ? backendFixtures ?? NO_FIXTURES : fixturePresets
  const playbackRef = useRef(playback)
  playbackRef.current = playback
  const backendFrame = useCallback(() => (
    liveBackendFrame(playbackRef.current.state, performance.now() - playbackRef.current.receivedAt)
  ), [])
  const following = inBackend && liveBackendFrame(playback.state, 0) !== null
  const unitTestColors = inBackend && playback.state?.active ? playback.state.unitColors ?? null : null
  const fixtureTestColors = inBackend && playback.state?.active ? playback.state.fixtureColors ?? null : null
  const displayedPreset = viewSequence
    ? resolveSequenceFrame(viewSequence, diagnostics.globalFrame)!.layers.at(-1)!.preset
    : viewPreset

  // 後端重新開始播放（別處套用、儲存或天氣變化）時，更新 3D 鏡像用的效果與串接，不動編輯中的內容。
  // When the backend restarts playback (applied or saved elsewhere, weather change), refresh what the 3D view mirrors without touching edits.
  const liveRestart = inBackend && playback.state?.active ? playback.state.restart : null
  useEffect(() => {
    if (liveRestart === null) return
    let active = true
    void Promise.all([getBackendState(), getBackendSequence()]).then(([state, data]) => {
      if (!active) return
      setBackendState(state)
      setSavedSequence(JSON.stringify({ ...data.sequence, ending: data.sequence.ending ?? { kind: 'hold' } }))
      setSequencePlaybackMode(data.playbackMode)
    }).catch(() => {})
    showEditor.reload()
    return () => { active = false }
  }, [liveRestart])

  // 場景配置：後端的 LED 數、旋轉、燈具數與座標（沒有後端時用範例）。
  // Site setup: the backend's LED count, rotation, fixture counts and positions (the example without a backend).
  useEffect(() => {
    let active = true
    void Promise.all([getBackendState(), getBackendPatch().catch(() => null)]).then(([state, patch]) => {
      if (!active) return
      setSiteInfo({ state, patch })
      if (state.output.pixels) setSettings((current) => ({ ...current, ledCount: state.output.pixels! }))
      if (state.fixtures) setFixturePresets({ ball: state.fixtures.ball.preset, wash: state.fixtures.wash.preset })
    }).catch(() => {})
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const refresh = () => {
      void getWeatherState().then((state) => { if (active) setWeather(state) }).catch(() => {})
    }
    refresh()
    const timer = window.setInterval(refresh, 5_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  useEffect(() => {
    let active = true
    let timer = 0
    const poll = () => {
      window.clearTimeout(timer)
      if (document.hidden) return
      void getBackendPlayback().then((state) => {
        if (!active) return
        setPlayback({ state, receivedAt: performance.now(), unsupported: state === null })
        setPlaybackError(null)
      }).catch(() => {
        if (active) setPlaybackError('無法取得後端播放進度')
      }).finally(() => {
        if (active) timer = window.setTimeout(poll, 1_000)
      })
    }
    poll()
    document.addEventListener('visibilitychange', poll)
    return () => {
      active = false
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', poll)
    }
  }, [playbackPoll])

  useEffect(() => {
    if (weather?.generation) resetSequencePreview()
  }, [weather?.generation, resetSequencePreview])

  const updateSelectedStep = useCallback((
    change: (step: SequenceStep) => SequenceStep,
    seek: SequenceSeek = { kind: 'step', id: selectedStepId },
  ) => {
    markSequenceEdited()
    setSequenceSteps((current) => current.map((step) => (
      step.id === selectedStepId ? change(step) : step
    )))
    resetSequencePreview(seek)
  }, [selectedStepId, resetSequencePreview])

  useEffect(() => {
    const request = ++sequenceRequest.current
    setSequenceBusy(true)
    setSequenceError(null)
    void getBackendSequence().then((data) => {
      if (request !== sequenceRequest.current) return
      setSequenceConnected(true)
      setSequencePlaybackMode(data.playbackMode)
      const loadedEnding = data.sequence.ending ?? { kind: 'hold' }
      setSavedSequence(JSON.stringify({ ...data.sequence, ending: loadedEnding }))
      setSettings((current) => ({ ...current, controllerFps: data.output.fps, rpm: data.output.rpm }))
      if (!sequenceEdited.current || (savedSequence !== null && JSON.stringify(sequenceDocument) === savedSequence)) {
        setSequenceEnabled(true)
        setSequenceEnding(loadedEnding)
        setSequenceSteps(data.sequence.steps)
        setSelectedStepId(data.sequence.steps[0].id)
        nextStepId.current = Math.max(...data.sequence.steps.map((step) => step.id)) + 1
        resetSequencePreview()
      }
    }).catch((error: unknown) => {
      if (request === sequenceRequest.current) {
        setSequenceConnected(false)
        setSequenceError(error instanceof Error ? error.message : '無法載入後端串接')
      }
    }).finally(() => {
      if (request === sequenceRequest.current) setSequenceBusy(false)
    })
    return () => { sequenceRequest.current += 1 }
  }, [resetSequencePreview, sequenceRefresh])

  const saveSequence = async () => {
    if (sequenceBusy) return
    const request = sequenceRequest.current
    const editRevision = sequenceEditRevision.current
    const document = sequenceDocument
    setSequenceBusy(true)
    setSequenceError(null)
    try {
      const state = await saveBackendSequence(document)
      if (request !== sequenceRequest.current) return
      setSequenceConnected(true)
      setSequencePlaybackMode(state.playbackMode)
      setSavedSequence(JSON.stringify(state.sequence))
      setPlaybackPoll((current) => current + 1)
      if (editRevision === sequenceEditRevision.current) sequenceEdited.current = false
      resetSequencePreview()
    } catch (error) {
      if (request === sequenceRequest.current) {
        setSequenceError(error instanceof Error ? error.message : '儲存串接失敗')
      }
    } finally {
      if (request === sequenceRequest.current) setSequenceBusy(false)
    }
  }

  useEffect(() => {
    if (controlMode !== 'backend') {
      backendRequest.current += 1
      setBackendBusy(false)
      return
    }
    const request = ++backendRequest.current
    setBackendBusy(true)
    setBackendError(null)
    setBackendState(null)
    void getBackendState().then((state) => {
      if (request !== backendRequest.current) return
      setBackendState(state)
      setEffectPresets((current) => ({ ...current, [state.preset.effectId]: state.preset }))
      setEffectId(state.preset.effectId)
      if (state.fixtures) setFixturePresets({ ball: state.fixtures.ball.preset, wash: state.fixtures.wash.preset })
      setSettings((current) => ({ ...current, controllerFps: state.output.fps, rpm: state.output.rpm }))
    }).catch((error: unknown) => {
      if (request === backendRequest.current) {
        setBackendError(error instanceof Error ? error.message : '無法連線後端')
      }
    }).finally(() => {
      if (request === backendRequest.current) setBackendBusy(false)
    })
    return () => { backendRequest.current += 1 }
  }, [controlMode, backendRefresh])

  // 有轉燈在等緩停結束時每秒更新倒數，直到全部送出。 / While starts are held, refresh the countdown every second.
  const motorWaiting = backendState?.motor.waiting?.some((seconds) => seconds !== null) ?? false
  useEffect(() => {
    if (controlMode !== 'backend' || !motorWaiting) return
    const request = backendRequest.current
    const timer = window.setTimeout(() => {
      void getBackendState().then((state) => {
        if (request === backendRequest.current) setBackendState(state)
      }).catch(() => {})
    }, 1_000)
    return () => window.clearTimeout(timer)
  }, [backendState, controlMode, motorWaiting])

  const applyToBackend = useCallback(async (preset: EffectPreset, fixtures: FixturePresets) => {
    if (controlMode !== 'backend' || backendBusy || backendState === null) return
    const request = backendRequest.current
    setBackendBusy(true)
    setBackendError(null)
    try {
      // 舊版後端只收轉燈 preset。 / Older backends take the spinner preset only.
      const state = await applyBackendPreset(backendState.fixtures ? { spinner: preset, ...fixtures } : preset)
      if (request === backendRequest.current) {
        setBackendState(state)
        setSequencePlaybackMode('single')
        setPlaybackPoll((current) => current + 1)
      }
    } catch (error) {
      if (request === backendRequest.current) {
        setBackendError(error instanceof Error ? error.message : '套用失敗')
      }
    } finally {
      if (request === backendRequest.current) setBackendBusy(false)
    }
  }, [backendBusy, backendState, controlMode])

  // enabled 為 'stagger' | 'together' 時是切換啟動方式（分區／全部一起）。 / 'stagger' | 'together' switches the start mode.
  const setMotor = useCallback(async (enabled: boolean | 'stagger' | 'together', units?: number[]) => {
    if (controlMode !== 'backend' || backendBusy || backendState === null) return
    const request = backendRequest.current
    setBackendBusy(true)
    setBackendError(null)
    try {
      const state = typeof enabled === 'boolean' ? await setBackendMotor(enabled, units) : await setBackendMotorStagger(enabled === 'stagger')
      if (request === backendRequest.current) setBackendState(state)
    } catch (error) {
      if (request === backendRequest.current) {
        setBackendError(error instanceof Error ? error.message : '馬達命令送出失敗')
      }
    } finally {
      if (request === backendRequest.current) setBackendBusy(false)
    }
  }, [backendBusy, backendState, controlMode])

  const setBlackout = useCallback(async (enabled: boolean, groups?: BlackoutGroup[]) => {
    if (controlMode !== 'backend' || backendBusy || backendState === null) return
    const request = backendRequest.current
    setBackendBusy(true)
    setBackendError(null)
    try {
      const state = await setBackendBlackout(enabled, groups)
      if (request === backendRequest.current) setBackendState(state)
      // 讓 3D 鏡像立刻反映。 / Refresh the 3D mirror right away.
      setPlaybackPoll((current) => current + 1)
    } catch (error) {
      if (request === backendRequest.current) {
        setBackendError(error instanceof Error ? error.message : '關燈命令送出失敗')
      }
    } finally {
      if (request === backendRequest.current) setBackendBusy(false)
    }
  }, [backendBusy, backendState, controlMode])

  // 後端的待機、風流設定與測試風（舊後端沒有 /api/wind → null）。進入後端控制、重新整理時讀取。
  // The backend's standby, wind-flow presets and test wind (null on old backends); read on entering backend control.
  const windRequest = useRef(0)
  const sentTarget = useRef('')
  const adoptWind = useCallback((wind: BackendWind | null) => {
    setBackendWind(wind)
    if (wind) {
      // 本機的預覽風力跟後端的測試風一致（鏡像用）；記下來，避免又送回去。 / Mirror the backend's test wind locally.
      sentTarget.current = JSON.stringify(wind.target)
      setWindTarget(wind.target)
    }
  }, [])
  useEffect(() => {
    if (controlMode !== 'backend') { setBackendWind(null); return }
    const request = ++windRequest.current
    void getBackendWind().then((wind) => { if (request === windRequest.current) adoptWind(wind) }).catch(() => undefined)
  }, [controlMode, backendRefresh, adoptWind])
  const sendWind = useCallback(async (send: () => Promise<BackendWind>, message: string) => {
    const request = ++windRequest.current
    try {
      const wind = await send()
      if (request !== windRequest.current) return
      adoptWind(wind)
      setBackendError(null)
      setPlaybackPoll((current) => current + 1)
    } catch (error) {
      if (request === windRequest.current) setBackendError(error instanceof Error ? error.message : message)
    }
  }, [adoptWind])
  const changeStandby = useCallback((enabled: boolean) => {
    if (controlMode === 'backend') void sendWind(() => setBackendStandby(enabled), '待機命令送出失敗')
    else setStandby(enabled)
  }, [controlMode, sendWind])
  const changeWindPreset = useCallback(<G extends WindGroup>(group: G, preset: WindPresets[G]) => {
    if (controlMode !== 'backend') { setWindPresets((current) => ({ ...current, [group]: preset })); return }
    if (!backendWindRef.current) return
    const next = { ...fromBackendWind(backendWindRef.current.presets), [group]: preset }
    // 先顯示手上的值（拖滑桿時），後端回應後跟上。 / Show the edited value at once; the backend response follows.
    setBackendWind({ ...backendWindRef.current, presets: toBackendWind(next) })
    void sendWind(() => setBackendWindPresets(toBackendWind(next)), '風流設定送出失敗')
  }, [controlMode, sendWind])
  // 後端控制時，「測試風」面板的變更送到後端。 / In backend control the test-wind panel writes to the backend.
  useEffect(() => {
    if (controlMode !== 'backend') return undefined
    return subscribeWindPreview(() => {
      const target = getWindTarget()
      const encoded = JSON.stringify(target)
      if (!backendWindRef.current || encoded === sentTarget.current) return
      sentTarget.current = encoded
      void sendWind(() => setBackendWindTarget(target), '測試風送出失敗')
    })
  }, [controlMode, sendWind])
  // 其他裝置切換了待機：播放狀態回報的待機和手上的不同時重新讀取。 / Another client toggled standby: re-read.
  const playbackStandby = playback.state?.active ? playback.state.standby === true : null
  useEffect(() => {
    if (controlMode !== 'backend' || playbackStandby === null || !backendWindRef.current) return
    if (playbackStandby !== backendWindRef.current.standby) {
      const request = ++windRequest.current
      void getBackendWind().then((wind) => { if (request === windRequest.current) adoptWind(wind) }).catch(() => undefined)
    }
  }, [controlMode, playbackStandby, adoptWind])

  // 總亮度：拖滑桿時連續送出，只採用最後一次的回應；不擋其他操作。
  // Master level: sent repeatedly while dragging; only the latest response is used, and it does not block other actions.
  const masterRequest = useRef(0)
  const setMasterLevel = useCallback(async (level: number) => {
    if (controlMode !== 'backend') return
    const request = ++masterRequest.current
    try {
      const state = await setBackendMasterLevel(level)
      if (request !== masterRequest.current) return
      setBackendState(state)
      setBackendError(null)
      setPlaybackPoll((current) => current + 1)
    } catch (error) {
      if (request === masterRequest.current) setBackendError(error instanceof Error ? error.message : '亮度命令送出失敗')
    }
  }, [controlMode])

  const restartBackend = useCallback(async (source: 'sequence' | 'single') => {
    if (controlMode !== 'backend' || backendBusy || backendState === null) return
    const request = backendRequest.current
    setBackendBusy(true)
    setBackendError(null)
    try {
      const state = await restartBackendPlayback(source)
      if (request === backendRequest.current) {
        setSequencePlaybackMode(state.playbackMode)
        setPlaybackPoll((current) => current + 1)
      }
    } catch (error) {
      if (request === backendRequest.current) {
        setBackendError(error instanceof Error ? error.message : '重新播放失敗')
      }
    } finally {
      if (request === backendRequest.current) setBackendBusy(false)
    }
  }, [backendBusy, backendState, controlMode])

  const updateSetting = useCallback(
    (name: Exclude<keyof SimulationSettings, 'ledCount'>, value: number) => {
      setSettings((current) => ({ ...current, [name]: value }))
    },
    [],
  )

  const degreesPerFrame = useMemo(
    () => degreesPerControllerFrame(settings.rpm, settings.controllerFps),
    [settings.rpm, settings.controllerFps],
  )
  const exposureArc = (settings.rpm * 6 * settings.exposureMs) / 1000
  const activeEffect = getEffectManifest(displayedPreset.effectId)

  const selectEffect = useCallback((nextEffectId: EffectId) => {
    if (inShow) {
      showEditor.updateSelectedPresets((holder) => ({ ...holder, preset: createDefaultEffectPreset(nextEffectId, settings.ledCount) }))
    } else if (inSequence) {
      updateSelectedStep((step) => {
        const preset = createDefaultEffectPreset(nextEffectId, settings.ledCount)
        return { ...step, preset, playback: defaultPlaybackRule(preset) }
      })
    } else {
      setEffectId(nextEffectId)
    }
  }, [inShow, showEditor, inSequence, updateSelectedStep, settings.ledCount])

  const updateEffectParameter = useCallback((key: string, value: number) => {
    const updatePreset = (preset: EffectPreset): EffectPreset => {
      const parameters = { ...preset.parameters, [key]: value }
      if (preset.effectId === 'random-leds' && key === 'holdFrames') {
        parameters.minHoldFrames = Math.min(parameters.minHoldFrames ?? 1, value)
      }
      return { ...preset, parameters }
    }
    if (inShow) {
      showEditor.updateSelectedPresets((holder) => ({ ...holder, preset: updatePreset(holder.preset) }))
    } else if (inSequence) {
      updateSelectedStep((step) => ({ ...step, preset: updatePreset(step.preset) }))
    } else {
      setEffectPresets((current) => ({
        ...current, [effectId]: updatePreset(current[effectId]),
      }))
    }
  }, [effectId, inShow, showEditor, inSequence, updateSelectedStep])

  const updateEffectColor = useCallback((color: EffectColor) => {
    if (inShow) {
      showEditor.updateSelectedPresets((holder) => ({ ...holder, preset: { ...holder.preset, color } }))
    } else if (inSequence) {
      updateSelectedStep((step) => ({ ...step, preset: { ...step.preset, color } }))
    } else {
      setEffectPresets((current) => ({
        ...current, [effectId]: { ...current[effectId], color },
      }))
    }
  }, [effectId, inShow, showEditor, inSequence, updateSelectedStep])

  const updateUnitVariation = useCallback((unitVariation: UnitVariation | null) => {
    const updatePreset = (preset: EffectPreset): EffectPreset => {
      const { unitVariation: _, ...base } = preset
      return unitVariation ? { ...base, unitVariation } : base
    }
    if (inShow) {
      showEditor.updateSelectedPresets((holder) => ({ ...holder, preset: updatePreset(holder.preset) }))
    } else if (inSequence) {
      updateSelectedStep((step) => ({ ...step, preset: updatePreset(step.preset) }))
    } else {
      setEffectPresets((current) => ({
        ...current, [effectId]: updatePreset(current[effectId]),
      }))
    }
  }, [effectId, inShow, showEditor, inSequence, updateSelectedStep])

  const updateFixturePreset = useCallback((group: FixtureGroup, preset: FixturePreset | null) => {
    if (inShow) {
      showEditor.updateSelectedPresets((holder) => {
        const { [group]: _omit, ...others } = holder.fixtures ?? {}
        const fixtures = preset ? { ...others, [group]: preset } : others
        return { preset: holder.preset, ...(Object.keys(fixtures).length ? { fixtures } : {}) }
      })
    } else if (inSequence) {
      updateSelectedStep((step) => {
        const { [group]: _omit, ...others } = step.fixtures ?? {}
        const fixtures = preset ? { ...others, [group]: preset } : others
        const { fixtures: _old, ...base } = step
        return Object.keys(fixtures).length ? { ...base, fixtures } : base
      })
    } else {
      setFixturePresets((current) => ({ ...current, [group]: preset }))
    }
  }, [inShow, showEditor, inSequence, updateSelectedStep])

  const addStep = () => {
    markSequenceEdited()
    const id = nextStepId.current++
    const step = createSequenceStep(id, structuredClone(selectedStep.preset))
    const index = sequenceSteps.findIndex((item) => item.id === selectedStepId)
    setSequenceSteps([
      ...sequenceSteps.slice(0, index + 1),
      step,
      ...sequenceSteps.slice(index + 1),
    ])
    setSelectedStepId(id)
    resetSequencePreview({ kind: 'step', id })
  }

  const duplicateStep = () => {
    markSequenceEdited()
    const id = nextStepId.current++
    const step = { ...structuredClone(selectedStep), id }
    const index = sequenceSteps.findIndex((item) => item.id === selectedStepId)
    setSequenceSteps([
      ...sequenceSteps.slice(0, index + 1),
      step,
      ...sequenceSteps.slice(index + 1),
    ])
    setSelectedStepId(id)
    resetSequencePreview({ kind: 'step', id })
  }

  const removeStep = () => {
    if (sequenceSteps.length <= 1) return
    markSequenceEdited()
    const index = sequenceSteps.findIndex((item) => item.id === selectedStepId)
    const next = sequenceSteps.filter((item) => item.id !== selectedStepId)
    setSequenceSteps(next)
    const nextSelected = next[Math.min(index, next.length - 1)].id
    setSelectedStepId(nextSelected)
    resetSequencePreview({ kind: 'step', id: nextSelected })
  }

  const moveStep = (direction: -1 | 1) => {
    const index = sequenceSteps.findIndex((item) => item.id === selectedStepId)
    const destination = index + direction
    if (destination < 0 || destination >= sequenceSteps.length) return
    markSequenceEdited()
    const next = [...sequenceSteps]
    ;[next[index], next[destination]] = [next[destination], next[index]]
    setSequenceSteps(next)
    resetSequencePreview({ kind: 'step', id: selectedStepId })
  }

  const updateEnding = (ending: SequenceEnding) => {
    markSequenceEdited()
    setSequenceEnding(ending)
    resetSequencePreview({ kind: 'transition', id: sequenceSteps[sequenceSteps.length - 1].id })
  }

  const updatePlayback = (playback: PlaybackRule) => {
    updateSelectedStep((step) => ({ ...step, playback }))
  }

  const updateTransition = (kind: TransitionKind, seconds: number) => {
    updateSelectedStep((step) => ({ ...step, transition: { kind, seconds } }), { kind: 'transition', id: selectedStepId })
  }

  const showPlaying = showEditor.preview === 'playing' || showEditor.preview === 'silent'
  // 演出預覽時，畫面跟著播放／停止；停止時停在目前位置。 / In a show preview the view runs only while playing.
  // 待機不跟演出預覽的播放／停止。 / Standby ignores the show preview's play / stop.
  const scenePaused = paused || (!inStandby && previewShow !== null && !showPlaying)
  // 模擬中演出的音樂位置（秒）。 / The simulator's position in the show's track, in seconds.
  const showPreviewSeconds = previewShow
    ? (previewShow.loop ? diagnostics.globalFrame % previewShow.totalFrames
      : Math.min(diagnostics.globalFrame, previewShow.totalFrames)) / settings.controllerFps
    : null

  const backendDirty = backendState !== null && (
    JSON.stringify(effectPreset) !== JSON.stringify(backendState.preset)
    || (backendFixtures !== null && JSON.stringify(fixturePresets) !== JSON.stringify(backendFixtures))
  )

  return (
    <main className="app-shell simulator-shell">
      <header className="app-header">
        <h1>美術館燈光模擬器</h1>
        <div className="segmented-control mode-switch" aria-label="控制模式">
          <button type="button" className={controlMode === 'simulation' ? 'active' : 'secondary'}
            aria-pressed={controlMode === 'simulation'} onClick={() => setControlMode('simulation')}>模擬</button>
          <button type="button" className={controlMode === 'backend' ? 'active' : 'secondary'}
            aria-pressed={controlMode === 'backend'} onClick={() => setControlMode('backend')}>後端控制</button>
        </div>
      </header>

      <section className={inShow && timelineVisible ? 'workspace with-timeline' : 'workspace'}>
        <div className="viewport-panel">
          <div className="viewport-toolbar">
            <div className="button-row">
              {previewShow ? (
                // 演出預覽：畫面只在播放時前進（像剪輯軟體），這顆按鈕即播放／停止。
                // Show preview: the view only advances while playing (like an editor); this button plays / stops.
                <button type="button" onClick={() => {
                  if (showPlaying) showEditor.stopPreview()
                  else void showEditor.playPreview(showPreviewSeconds ?? 0)
                }}>{showPlaying ? '停止' : '播放'}</button>
              ) : (
                <button type="button" onClick={() => setPaused((current) => !current)}>
                  {paused ? '繼續' : '暫停'}
                </button>
              )}
              <button type="button" className="secondary" onClick={() => {
                seekTarget.current = { kind: 'start' }
                setResetToken((current) => current + 1)
                setDiagnostics(INITIAL_DIAGNOSTICS)
              }}>重設時間</button>
              {(!inBackend || backendWind) && (
                <button type="button" className={inStandby ? 'active' : 'secondary'} aria-pressed={inStandby}
                  title="全場改播待機效果（風流，在「風力」分頁設定）；再按一次回到原本的效果／串接／演出"
                  onClick={() => changeStandby(!inStandby)}>{inStandby ? '待機中' : '待機'}</button>
              )}
            </div>
            <div className="segmented-control view-switch" aria-label="視角">
              {VIEW_LABELS.map(([value, label]) => (
                <button key={value} type="button" className={view === value ? 'active' : 'secondary'}
                  aria-pressed={view === value} onClick={() => setView(value)}>{label}</button>
              ))}
            </div>
            <label className="unit-select">
              <span>轉燈</span>
              <select aria-label="選取的轉燈" value={selectedUnit} onChange={(event) => setSelectedUnit(Number(event.target.value))}>
                {Array.from({ length: unitCount }, (_, index) => <option key={index} value={index}>{index + 1}</option>)}
              </select>
            </label>
            {(['spinner', 'ball', 'wash'] as const).map((group) => (
              <label className="scene-toggle" key={group}>
                <input type="checkbox" checked={visible[group]}
                  onChange={(event) => setVisible((current) => ({ ...current, [group]: event.target.checked }))} />
                <span>{{ spinner: '轉燈', ball: '球燈', wash: '洗牆燈' }[group]}</span>
              </label>
            ))}
            <label className="scene-toggle">
              <input type="checkbox" checked={showStructure}
                onChange={(event) => setShowStructure(event.target.checked)} />
              <span>骨架</span>
            </label>
            {inShow && (
              <label className="scene-toggle">
                <input type="checkbox" checked={timelineVisible}
                  onChange={(event) => setTimelineVisible(event.target.checked)} />
                <span>時間軸</span>
              </label>
            )}
          </div>
          <div className="canvas-wrap">
            <SimulationCanvas
              settings={settings}
              weather={weather}
              effectPreset={viewPreset}
              fixturePresets={viewSequence && !inStandby ? NO_FIXTURES : viewFixtures}
              sequence={inStandby ? null : viewSequence}
              sequenceRevision={sequenceRevision}
              syncFrame={inBackend ? backendFrame : !inStandby && previewShow && showEditor.preview === 'playing' ? showEditor.previewFrame : null}
              syncTolerance={!inBackend && previewShow && showEditor.preview === 'playing' ? 0 : 2}
              startFrame={previewShow ? sequenceSeekFrame(previewShow, seekTarget.current, settings.controllerFps)
                : inSequence ? sequenceSeekFrame(sequenceSchedule, seekTarget.current, settings.controllerFps) : 0}
              paused={scenePaused}
              resetToken={resetToken}
              view={view}
              layout={layout}
              pixelRotation={pixelRotation}
              visible={visible}
              selectedUnit={selectedUnit}
              markedUnits={overrideUnits}
              selectedFixture={selectedFixture}
              showStructure={showStructure}
              // 編輯轉燈覆寫時，點轉燈是加入／移出這組覆寫。 / While editing an override, clicking a spinner toggles it.
              onSelectUnit={(unit) => { if (editingOverride) showEditor.toggleOverrideUnit(unit + 1); else setSelectedUnit(unit) }}
              onSelectFixture={setSelectedFixture}
              onDiagnostics={setDiagnostics}
              masterLevel={inBackend ? backendState?.masterLevel ?? 1 : 1}
              unitColors={unitTestColors}
              fixtureColors={fixtureTestColors}
            />
            <p className="viewport-caption">
              <strong>{inStandby ? '待機：風流' : activeEffect.label}</strong>
              <span>{settings.controllerFps} FPS · {settings.rpm} RPM</span>
              {inBackend && (
                <span>{following
                  ? `後端輸出 · ${liveMode === 'show' ? '演出' : liveMode === 'sequence' ? '串接' : '單一效果'}`
                  : '後端未播放 · 本地模擬'}</span>
              )}
              {previewShow && <span>演出預覽{showEditor.preview === 'playing' ? ' · 跟隨音樂' : ''}</span>}
              {inBackend && playback.state?.active && (playback.state.blackout || playback.state.blackoutGroups?.length)
                ? <span className="viewport-test">{playback.state.blackout ? '全部關燈中'
                  : `關燈中：${playback.state.blackoutGroups!.map((group) => ({ spinner: '轉燈', ball: '球燈', wash: '洗牆燈' }[group])).join('、')}`}</span>
                : (unitTestColors || fixtureTestColors) && <span className="viewport-test">單色測試中</span>}
              {selectedFixture && (
                <span>選取：{selectedFixture.group === 'ball' ? '球燈' : '洗牆燈'} {selectedFixture.id}</span>
              )}
            </p>
            <div className="diagnostics" aria-live="polite">
              <div><span>Rotor</span><strong>{diagnostics.angleDegrees.toFixed(1)}°</strong></div>
              <div><span>Frame</span><strong>{diagnostics.controllerFrame}</strong></div>
              <div><span>Phase</span><strong>{diagnostics.cyclePhase + 1}/20</strong></div>
              <div><span>°/frame</span><strong>{degreesPerFrame.toFixed(1)}°</strong></div>
              <div><span>Exposure</span><strong>{exposureArc.toFixed(1)}°</strong></div>
              <div><span>Time</span><strong>{diagnostics.simulationTime.toFixed(1)} s</strong></div>
            </div>
          </div>
          {inShow && timelineVisible && <ShowTimeline editor={showEditor} fallbackSeconds={showPreviewSeconds} />}
        </div>

        <ControlPanel
          standby={inStandby}
          onStandby={inBackend && !backendWind ? null : changeStandby}
          windPresets={shownWindPresets}
          onWindPreset={changeWindPreset}
          settings={settings}
          weather={weather}
          onWeather={setWeather}
          effectPreset={effectPreset}
          unitCount={unitCount}
          editGroup={editGroup}
          onEditGroup={setEditGroup}
          fixturePresets={editedFixtures}
          onFixturePreset={updateFixturePreset}
          fixtureCounts={fixtureCounts}
          fixtureSelection={selectedFixture}
          onFixtureSelection={setSelectedFixture}
          patch={siteInfo.patch}
          controlMode={controlMode}
          backendState={backendState}
          backendBusy={backendBusy}
          backendError={backendError}
          backendDirty={backendDirty}
          playback={playback.unsupported ? null : playback.state}
          playbackSupported={!playback.unsupported}
          playbackReceivedAt={playback.receivedAt}
          playbackError={playbackError}
          playbackStepCount={liveShowId ? liveShowDocument?.sections.length ?? null : savedDocument?.steps.length ?? null}
          savedSequence={savedDocument}
          backendPlayingStepId={backendPlayingStepId}
          playingStepId={playingStepId}
          sequenceEnabled={sequenceEnabled}
          sequenceConnected={sequenceConnected}
          sequencePlaybackMode={sequencePlaybackMode}
          sequenceBusy={sequenceBusy}
          sequenceError={sequenceError}
          sequenceDirty={sequenceDirty}
          onSaveSequence={() => { void saveSequence() }}
          onReloadSequence={() => setSequenceRefresh((current) => current + 1)}
          sequenceSteps={sequenceSteps}
          sequenceEnding={sequenceEnding}
          selectedStepId={selectedStepId}
          sequenceSchedule={sequenceSchedule}
          currentSequenceFrame={currentSequenceFrame}
          onSequenceEnabled={(enabled) => { setSequenceEnabled(enabled); resetSequencePreview() }}
          onSequenceSelect={setSelectedStepId}
          onSequencePlayFrom={(id) => resetSequencePreview({ kind: 'step', id })}
          onSequencePreviewTransition={(id) => resetSequencePreview({ kind: 'transition', id })}
          onSequenceAdd={addStep}
          onSequenceDuplicate={duplicateStep}
          onSequenceRemove={removeStep}
          onSequenceMove={moveStep}
          onSequencePlayback={updatePlayback}
          onSequenceEnding={updateEnding}
          onSequenceTransition={updateTransition}
          onRefreshBackend={() => setBackendRefresh((current) => current + 1)}
          onApplyBackend={() => { void applyToBackend(effectPreset, fixturePresets) }}
          onMotor={(enabled, units) => { void setMotor(enabled, units) }}
          onMotorStagger={(stagger) => { void setMotor(stagger ? 'stagger' : 'together') }}
          onBlackout={(enabled, groups) => { void setBlackout(enabled, groups) }}
          onMasterLevel={(level) => { void setMasterLevel(level) }}
          onRestartBackend={(source) => { void restartBackend(source) }}
          onUnitColorTest={() => setPlaybackPoll((current) => current + 1)}
          onSetting={updateSetting}
          onEffectChange={selectEffect}
          onEffectParameter={updateEffectParameter}
          onEffectColor={updateEffectColor}
          onUnitVariation={updateUnitVariation}
          showEditor={showEditor}
          showEnabled={inShow}
          onShowEnabled={(enabled) => { setShowEnabled(enabled); if (!enabled) showEditor.stopPreview(); resetSequencePreview() }}
          currentShowIndex={previewShow ? resolveSequenceFrame(previewShow, diagnostics.globalFrame)?.stepIndex ?? null : null}
          showPreviewSeconds={showPreviewSeconds}
          backendShowPlaying={liveShowId && playback.state?.active && playback.state.mode === 'show'
            ? { showId: liveShowId, sectionId: playback.state.step.id } : null}
          onShowPlayFrom={(id) => resetSequencePreview({ kind: 'step', id })}
        />
      </section>

    </main>
  )
}
