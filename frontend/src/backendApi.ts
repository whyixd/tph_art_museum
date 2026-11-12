import type { EffectPreset, RgbColor } from './effects'
import type { FixturePreset } from './fixtures'
import type { SequenceDocument, SequenceStep } from './simulation/sequence'
import type { BeatMap, ShowDocument, ShowStart, ShowTransition } from './simulation/show'

/**
 * 後端 Sequence v2：每段 spinner（轉燈）＋選填 ball／wash（球燈／洗牆燈，省略 = 全暗）。
 * 前端內部仍用 v1 形狀（step.preset = 轉燈），球燈／洗牆燈放在 step.fixtures 原樣保留，M7 再提供編輯。
 * Backend Sequence v2: spinner plus optional ball / wash per step (omitted = dark). The frontend keeps its v1 shape
 * internally (step.preset = spinner) and carries ball / wash through step.fixtures untouched until M7 edits them.
 */
export type BackendSequenceStep = {
  id: number
  spinner: EffectPreset
  ball?: FixturePreset
  wash?: FixturePreset
  playback: SequenceStep['playback']
  transition: SequenceStep['transition']
}

export type BackendSequenceDocument = {
  schemaVersion: 2
  ending: SequenceDocument['ending']
  steps: BackendSequenceStep[]
}

export function fromBackendSequence(document: SequenceDocument | BackendSequenceDocument): SequenceDocument {
  if (document.schemaVersion === 1) return document
  return {
    schemaVersion: 1,
    ending: document.ending,
    steps: document.steps.map(({ id, spinner, ball, wash, playback, transition }) => ({
      id, preset: spinner, playback, transition,
      ...(ball || wash ? { fixtures: { ...(ball ? { ball } : {}), ...(wash ? { wash } : {}) } } : {}),
    })),
  }
}

export function toBackendSequence(document: SequenceDocument): BackendSequenceDocument {
  return {
    schemaVersion: 2,
    ending: document.ending,
    steps: document.steps.map(({ id, preset, playback, transition, fixtures }) => ({
      id, spinner: preset, ...fixtures, playback, transition,
    })),
  }
}

function withInternalSequence(state: BackendSequenceState): BackendSequenceState {
  return { ...state, sequence: fromBackendSequence(state.sequence as SequenceDocument | BackendSequenceDocument) }
}

export type BackendFixtureGroup = 'ball' | 'wash'

export type BackendState = {
  revision: number
  /** The spinner's single-effect preset. */
  preset: EffectPreset
  /** enabled／value：有任何一台在轉；units：每台是否轉動（index = 編號 − 1，舊後端沒有）。 / enabled: any unit runs; units: per unit. */
  /**
   * waiting：每台要轉但還在排隊（等前一次緩停結束、依控箱分區）的剩餘秒數（null = 沒有在等；舊後端沒有）。
   * waiting: per unit, seconds until a held start is sent (null = not held; missing on old backends).
   */
  motor: { channel: number; enabled: boolean; value: number; units?: boolean[]; waiting?: (number | null)[] }
  /** true：全部燈光關閉（轉燈、球燈、洗牆燈送全黑；舊後端沒有）。 / true: every light sends black (missing on old backends). */
  blackout?: boolean
  /** 總亮度 0..1（轉燈、球燈、洗牆燈；舊後端沒有）。 / Master level 0..1 for every light (missing on old backends). */
  masterLevel?: number
  output: {
    ip: string
    universe: number
    fps: number
    rpm: number
    unitId: number
    unitCount: number
    // 舊版後端沒有以下欄位。 / Older backends omit the fields below.
    pixels?: number
    pixelRotation?: number
  }
  /** Ball / wash: fixture count and single-effect preset (null = dark). Missing on older backends. */
  fixtures?: Record<BackendFixtureGroup, { count: number; preset: FixturePreset | null }>
}

/** Single-effect document: only the groups given change; null turns a fixture group off. */
export type PresetDocument = { spinner?: EffectPreset; ball?: FixturePreset | null; wash?: FixturePreset | null }

export type BackendSequenceState = {
  revision: number
  sequence: SequenceDocument
  playbackMode: 'sequence' | 'single'
  output: { fps: number; rpm: number; unitCount: number }
}

async function readState<T>(response: Response): Promise<T> {
  let body: T & { error?: string }
  try {
    body = await response.json() as T & { error?: string }
  } catch {
    throw new Error('無法讀取後端回應（HTTP ' + response.status + '）')
  }
  if (!response.ok) throw new Error(body.error ?? '後端回應 ' + response.status)
  return body
}

export async function getBackendState(): Promise<BackendState> {
  return readState<BackendState>(await fetch('/api/state', { cache: 'no-store' }))
}

export async function applyBackendPreset(preset: EffectPreset | PresetDocument): Promise<BackendState> {
  return readState<BackendState>(await fetch('/api/preset', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(preset),
  }))
}

export type PatchPosition = { x?: number; y?: number; z?: number }

export type BackendPatch = {
  spinners: { unitId: number; ip: string; universes: number[]; position?: PatchPosition | null }[]
  /** Every unit's layout position (index = unit - 1). */
  spinnerPositions?: (PatchPosition | null)[]
  /** 燈桿（x、y、頂端 z）。 / Poles (x, y, top z). */
  poles?: PatchPosition[]
  nodes: { name: string; ip: string; universe: number }[]
  fixtures: Record<BackendFixtureGroup, {
    id: number; node: string; ip: string; universe: number; address: number; lastChannel: number
    profile: string; channels: string[]; position: PatchPosition | null
  }[]>
}

// 舊版後端沒有這個 API，回 null。 / Older backends lack this API; returns null.
export async function getBackendPatch(): Promise<BackendPatch | null> {
  const response = await fetch('/api/patch', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<BackendPatch>(response)
}

/** 輸出迴圈的影格時間（最近一個 60 秒區間）。 / Output loop frame timing over the last 60 s window. */
/**
 * Art-Net 狀態：sending 送出中；waiting = 本機沒有來源位址（例如 eth0 沒接線），暫停中、位址回來後自動開始。
 * --api-only 時沒有這個欄位。 / Art-Net state; waiting = the source address is not on this host. Absent with --api-only.
 */
export type ArtnetLink = { state: 'sending' | 'waiting'; sourceIp: string | null }

export type OutputStats = { artnet?: ArtnetLink } & (
  | { active: false }
  | {
    active: true
    partial?: true
    windowSeconds: number
    frames: number
    skippedFrames: number
    renderMs: { avg: number; max: number }
    budgetMs: number
  }
)

export async function getOutputStats(): Promise<OutputStats | null> {
  const response = await fetch('/api/output', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<OutputStats>(response)
}

export type FixtureColorTest = Partial<Record<BackendFixtureGroup, UnitColorTest>>

export async function getFixtureColorTest(): Promise<FixtureColorTest | null> {
  const response = await fetch('/api/test/fixtures', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<FixtureColorTest>(response)
}

export async function setFixtureColorTest(
  group: BackendFixtureGroup, colors: (RgbColor | null)[], seconds: number | null,
): Promise<FixtureColorTest> {
  return readState<FixtureColorTest>(await fetch('/api/test/fixtures', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group, colors, seconds }),
  }))
}

export async function clearFixtureColorTest(): Promise<FixtureColorTest> {
  return readState<FixtureColorTest>(await fetch('/api/test/fixtures', { method: 'DELETE' }))
}

/** 全部（units 省略）或指定轉燈的馬達轉動／停止。 / Start or stop every unit, or only the given unit ids. */
export async function setBackendMotor(enabled: boolean, units?: number[]): Promise<BackendState> {
  return readState<BackendState>(await fetch('/api/motor', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(units ? { enabled, units } : { enabled }),
  }))
}

/** 總亮度 0..1：轉燈、球燈、洗牆燈一起調暗；馬達不受影響。 / Master level 0..1 for every light; motors untouched. */
export async function setBackendMasterLevel(level: number): Promise<BackendState> {
  return readState<BackendState>(await fetch('/api/master', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level }),
  }))
}

/** 全部關燈（轉燈、球燈、洗牆燈送全黑）或恢復；馬達不受影響。 / All lights off or back on; motors untouched. */
export async function setBackendBlackout(enabled: boolean): Promise<BackendState> {
  return readState<BackendState>(await fetch('/api/blackout', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  }))
}

export async function getBackendSequence(): Promise<BackendSequenceState> {
  return withInternalSequence(await readState<BackendSequenceState>(await fetch('/api/sequence', { cache: 'no-store' })))
}

export async function saveBackendSequence(sequence: SequenceDocument): Promise<BackendSequenceState> {
  return withInternalSequence(await readState<BackendSequenceState>(await fetch('/api/sequence', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toBackendSequence(sequence)),
  })))
}

// 從第 0 影格重播後端已儲存的串接或已套用的效果。 / Replay the saved sequence or applied preset from frame 0.
export async function restartBackendPlayback(source: 'sequence' | 'single'): Promise<BackendSequenceState> {
  return withInternalSequence(await readState<BackendSequenceState>(await fetch('/api/playback/restart', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source }),
  })))
}

export type WeatherCategory = 'clear' | 'cloudy' | 'rain'

export type WeatherState = {
  enabled: boolean
  category: WeatherCategory | null
  temperatureC: number | null
  weatherCode: number | null
  observedAt: string | null
  updatedAt: string | null
  /** v0.1.10（含）以前的降水延遲；之後一律為 0。 / Rain delays up to v0.1.10; always 0 since. */
  startPhases: number[]
  frameStep: number
  color: import('./effects').EffectColor | null
  generation: number
  error: string | null
  override: { category: WeatherCategory; temperatureC: number; remainingSeconds: number } | null
  real: { category: WeatherCategory | null; temperatureC: number | null }
  thresholds: number[]
  // 舊版後端沒有以下欄位。 / Older backends omit the fields below.
  source?: 'local' | 'visit' | 'override'
  visit?: { name: string; category: WeatherCategory; temperatureC: number; observedAt: string | null } | null
  /** v0.1.7（含）以前：整點後等這一輪播完才造訪。 / Up to v0.1.7: the hourly visit waited for the round to end. */
  visits?: { count: number; pending?: boolean; next: string; names?: string[] } | null
  /** 天氣分類效果（多雲錯開、雨天線條）等下一段才套用。 / The weather category effects wait for the next step. */
  categoryPending?: boolean
}

export async function getWeatherState(): Promise<WeatherState | null> {
  const state = await readState<WeatherState>(await fetch('/api/weather', { cache: 'no-store' }))
  return state.enabled ? state : null
}

export async function setWeatherOverride(category: WeatherCategory, temperatureC: number): Promise<WeatherState> {
  return readState<WeatherState>(await fetch('/api/weather/override', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category, temperatureC }),
  }))
}

// 測試：立即造訪清單中的地點（播一輪後回到當地）。 / Test: visit a listed location now for one round.
export async function startWeatherVisit(index: number): Promise<WeatherState> {
  return readState<WeatherState>(await fetch('/api/weather/visit', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ index }),
  }))
}

export async function endWeatherVisit(): Promise<WeatherState> {
  return readState<WeatherState>(await fetch('/api/weather/visit', { method: 'DELETE' }))
}

export async function clearWeatherOverride(): Promise<WeatherState> {
  return readState<WeatherState>(await fetch('/api/weather/override', { method: 'DELETE' }))
}

export type UnitColorTest = {
  active: boolean
  /** 每台一色；null 表示該台照常播放。 / One color per unit; null plays normally. */
  colors: (RgbColor | null)[] | null
  /** null：手動結束。 / null: until ended manually. */
  remainingSeconds: number | null
}

// 舊版後端沒有這個 API，回 null。 / Older backends lack this API; returns null.
export async function getUnitColorTest(): Promise<UnitColorTest | null> {
  const response = await fetch('/api/test/units', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<UnitColorTest>(response)
}

export async function setUnitColorTest(colors: (RgbColor | null)[], seconds: number | null): Promise<UnitColorTest> {
  return readState<UnitColorTest>(await fetch('/api/test/units', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ colors, seconds }),
  }))
}

export async function clearUnitColorTest(): Promise<UnitColorTest> {
  return readState<UnitColorTest>(await fetch('/api/test/units', { method: 'DELETE' }))
}

export type WeatherPaletteBand = { belowC: number | null; colors: RgbColor[] }

export type WeatherPalette = {
  schemaVersion: 1
  axis: 'strip' | 'radius'
  bands: WeatherPaletteBand[]
}

export async function getWeatherPalette(): Promise<WeatherPalette> {
  return readState<WeatherPalette>(await fetch('/api/weather/palette', { cache: 'no-store' }))
}

export async function saveWeatherPalette(palette: WeatherPalette): Promise<WeatherPalette> {
  return readState<WeatherPalette>(await fetch('/api/weather/palette', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(palette),
  }))
}

export type PlaybackTransition = {
  kind: 'crossfade' | 'fade-black' | 'unit-fade-black' | 'unit-breathe-black'
  fromIndex: number
  elapsed: number
  frames: number
}

type ActivePlaybackBase = {
  active: true
  restart: number
  fps: number
  ageMs: number
  frame: number
  /** 單色測試進行中才有：每台一色或 null。 / Only during a unit colour test: one color or null per unit. */
  unitColors?: (RgbColor | null)[]
  /** 球燈／洗牆燈單色測試進行中才有。 / Only during a fixture colour test. */
  fixtureColors?: Partial<Record<BackendFixtureGroup, (RgbColor | null)[]>>
  /** 全部關燈中才有（unitColors／fixtureColors 此時全黑）。 / Only during a blackout (the colours above are then black). */
  blackout?: true
  /** 總亮度 0..1（舊後端沒有）。 / Master level 0..1 (missing on old backends). */
  masterLevel?: number
}

export type SequencePlayback = ActivePlaybackBase & {
  mode: 'sequence'
  sequenceFrame: number
  totalFrames: number
  ending: 'hold' | 'loop' | 'fade-black'
  loopIteration: number
  ended: boolean
  endFade: { elapsed: number; frames: number } | null
  step: {
    index: number
    id: number
    effectId: import('./effects').EffectId
    elapsed: number
    duration: number
    repeat: { current: number; total: number } | null
  }
  transition: PlaybackTransition | null
}

export type SinglePlayback = ActivePlaybackBase & {
  mode: 'single'
  effectId: import('./effects').EffectId
  cycle: { current: number; elapsed: number; frames: number } | null
}

/** 演出播放中：位置欄位同串接，另附曲目資訊。 / A show playing: sequence-style position plus the track. */
export type ShowPlayback = Omit<SequencePlayback, 'mode'> & {
  mode: 'show'
  show: { id: string; title: string; trackId: string; durationSeconds: number; totalFrames: number; loop: boolean }
}

export type BackendPlayback = { active: false } | SequencePlayback | SinglePlayback | ShowPlayback

// null：後端版本沒有此 API（v0.1.1 以前）。 / null: the backend predates this API.
export async function getBackendPlayback(): Promise<BackendPlayback | null> {
  const response = await fetch('/api/playback', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<BackendPlayback>(response)
}

export type AudioTrack = {
  id: string
  title: string
  /** WAV 檔名；內建節拍器為 null。 / WAV file name; null for the built-in metronome. */
  file: string | null
  durationSeconds: number
  tempo: number | null
  beatCount: number
  beatsPerBar: number
  /** 音檔是否在 Pi 上。 / Whether the WAV file is on the host. */
  available: boolean
  /** 是否已預載進記憶體（舊版後端沒有）。 / Whether it is preloaded into memory (absent on older backends). */
  loaded?: boolean
  /** 拍點是否經前端修正（M14）。 / Whether the beats were corrected in the frontend (M14). */
  edited?: boolean
}

export type AudioState = {
  available: boolean
  error: string | null
  device: { requested: string | null; name: string | null }
  calibration: { offsetMs: number; savedOffsetMs: number; maxOffsetMs: number }
  /** 音效卡緩衝（ms）、啟動後斷音次數；舊後端沒有。 / Sound card buffer (ms), drop-outs since start; absent on old backends. */
  bufferMs?: number | null
  underflows?: number
  tracks: AudioTrack[]
  playing: {
    trackId: string
    /** null：尚未開始出聲。 / null: not heard yet. */
    positionSeconds: number | null
    durationSeconds: number
    loop: boolean
    flash: boolean
    beatIndex: number | null
  } | null
}

// 舊版後端沒有音訊 API，回 null。 / Older backends lack the audio API; returns null.
export async function getAudioState(): Promise<AudioState | null> {
  const response = await fetch('/api/audio', { cache: 'no-store' })
  if (response.status === 404) return null
  return readState<AudioState>(response)
}

export async function playAudio(request: { trackId: string; startSeconds?: number; loop?: boolean; flash?: boolean }): Promise<AudioState> {
  return readState<AudioState>(await fetch('/api/audio/play', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  }))
}

export async function stopAudio(): Promise<AudioState> {
  return readState<AudioState>(await fetch('/api/audio/play', { method: 'DELETE' }))
}

/** save: false 只即時套用（給試調），true 也寫入檔案。 / save: false applies live only; true also writes the file. */
export async function setAudioCalibration(offsetMs: number, save: boolean): Promise<AudioState> {
  return readState<AudioState>(await fetch('/api/audio/calibration', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ offsetMs, save }),
  }))
}

// ── 演出（M12） / Shows (M12) ──

export type BackendShowSection = {
  id: number
  start: ShowStart
  spinner: EffectPreset
  ball?: FixturePreset
  wash?: FixturePreset
  /** 某幾台轉燈改用別的效果（燈台編號從 1 起）。 / Spinners playing another effect (unit numbers from 1). */
  overrides?: { units: number[]; spinner: EffectPreset }[]
  transition: ShowTransition
}

export type BackendShowDocument = Omit<ShowDocument, 'sections'> & { sections: BackendShowSection[] }

export function fromBackendShow(document: BackendShowDocument): ShowDocument {
  return {
    ...document,
    sections: document.sections.map(({ id, start, spinner, ball, wash, overrides, transition }) => ({
      id, start, preset: spinner, transition,
      ...(ball || wash ? { fixtures: { ...(ball ? { ball } : {}), ...(wash ? { wash } : {}) } } : {}),
      ...(overrides?.length ? { overrides: overrides.map(({ units, spinner: preset }) => ({ units, preset })) } : {}),
    })),
  }
}

export function toBackendShow(document: ShowDocument): BackendShowDocument {
  return {
    ...document,
    sections: document.sections.map(({ id, start, preset, fixtures, overrides, transition }) => ({
      id, start, spinner: preset, ...fixtures,
      // 沒選任何轉燈的覆寫不存。 / Overrides without units are not saved.
      ...(overrides?.some((override) => override.units.length)
        ? { overrides: overrides.filter((override) => override.units.length).map(({ units, preset: spinner }) => ({ units: [...units].sort((a, b) => a - b), spinner })) }
        : {}),
      transition,
    })),
  }
}

export type ShowsState = {
  revision: number
  shows: ShowDocument[]
  /** 無法編譯的演出（例如拍點檔不見了）。 / Shows that do not compile (e.g. a missing beat map). */
  errors: Record<string, string>
  playing: {
    showId: string
    trackId: string
    /** 沒有音效卡時以系統時鐘無聲播放。 / Without a sound card the show runs silently on the system clock. */
    silent: boolean
    positionSeconds: number
    durationSeconds: number
    loop: boolean
  } | null
}

async function readShows(response: Response): Promise<ShowsState> {
  const state = await readState<Omit<ShowsState, 'shows'> & { shows: BackendShowDocument[] }>(response)
  return { ...state, shows: state.shows.map(fromBackendShow) }
}

// 舊版後端沒有演出 API，回 null。 / Older backends lack the show API; returns null.
export async function getShows(): Promise<ShowsState | null> {
  const response = await fetch('/api/shows', { cache: 'no-store' })
  if (response.status === 404) return null
  return readShows(response)
}

export async function saveShow(show: ShowDocument): Promise<ShowsState> {
  return readShows(await fetch(`/api/shows/${encodeURIComponent(show.id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(toBackendShow(show)),
  }))
}

export async function deleteShow(id: string): Promise<ShowsState> {
  return readShows(await fetch(`/api/shows/${encodeURIComponent(id)}`, { method: 'DELETE' }))
}

export async function playShow(showId: string, startSeconds = 0): Promise<ShowsState> {
  return readShows(await fetch('/api/show/play', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ showId, startSeconds }),
  }))
}

export async function stopShow(): Promise<ShowsState> {
  return readShows(await fetch('/api/show/play', { method: 'DELETE' }))
}

export async function getBeatMap(trackId: string): Promise<BeatMap> {
  return readState<BeatMap>(await fetch(`/api/audio/tracks/${encodeURIComponent(trackId)}`, { cache: 'no-store' }))
}

/** 曲目的 WAV（給瀏覽器預覽）；不在主機上時回 null。 / The track's WAV for the browser preview; null when not on the host. */
export async function getTrackAudio(trackId: string): Promise<ArrayBuffer | null> {
  const response = await fetch(`/api/audio/tracks/${encodeURIComponent(trackId)}/wav`)
  if (response.status === 404) return null
  if (!response.ok) throw new Error('無法下載音檔（HTTP ' + response.status + '）')
  return response.arrayBuffer()
}

/** 儲存修正後的拍點（存在 Pi 的 preset/beat-edits/，優先於內建版本）。 / Save corrected beats (they override the shipped map). */
export async function saveBeatMap(beatMap: BeatMap): Promise<BeatMap> {
  return readState<BeatMap>(await fetch(`/api/audio/tracks/${encodeURIComponent(beatMap.id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(beatMap),
  }))
}

/** 刪除修正、回到內建拍點。 / Drop the correction and use the shipped beat map again. */
export async function revertBeatMap(trackId: string): Promise<BeatMap> {
  return readState<BeatMap>(await fetch(`/api/audio/tracks/${encodeURIComponent(trackId)}`, { method: 'DELETE' }))
}
