/**
 * 風力效果原型（PLAN.md §10.6）：前端預覽用的風力狀態與相位累積。
 * Wind effect prototype (PLAN.md §10.6): the preview wind state and phase accumulation, frontend only.
 *
 * 風速、風向由「預覽風力」面板設定，依平滑時間慢慢跟上（同後端規劃的一階低通與轉向速率上限）。
 * 風力效果的流動距離不能用「時間 × 速度」直接算（風速一變整段跳動），所以每格依當下的速度累積。
 * The panel sets the target speed and direction; the smoothed values follow them (the planned first-order low-pass and
 * turn-rate limit). The flow distance is accumulated frame by frame at the current speed, because time × speed would
 * jump whenever the wind changes.
 */
import { clamp01 } from '../effects/contract'

export type WindPreviewTarget = {
  /** 目標風速 m/s。 / Target wind speed. */
  speedMs: number
  /** 風吹往的場地方向（0° = +x 沿長牆、90° = +y）。 / Direction the wind blows toward on the site plan. */
  flowDeg: number
  /** 風速平滑時間常數（秒）；顏色用 3 倍。0 = 立即。 / Speed time constant (s); colour uses 3×. 0 = immediate. */
  smoothingSeconds: number
  /** 風速擾動（0–0.6），模擬陣風起伏。 / Speed fluctuation, simulating gusts. */
  gustiness: number
}

export type WindPreviewState = {
  speedMs: number
  colorSpeedMs: number
  flowDeg: number
  /** 擾動後、平滑前的瞬間風速。 / Instant speed after fluctuation, before smoothing. */
  instantMs: number
}

const MAX_TURN_DEG_PER_SECOND = 3
const CALM_DIRECTION_MS = 0.3

let target: WindPreviewTarget = { speedMs: 3, flowDeg: 0, smoothingSeconds: 2, gustiness: 0 }
let state: WindPreviewState = { speedMs: 3, colorSpeedMs: 3, flowDeg: 0, instantMs: 3 }
let lastTick: number | null = null
const listeners = new Set<() => void>()

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now())

/** 平順的擾動（數個不成倍數的正弦相加），-1..1。 / Smooth fluctuation from incommensurate sines, -1..1. */
function fluctuation(seconds: number): number {
  return (Math.sin(seconds * 0.71) * 0.5 + Math.sin(seconds * 1.93 + 1.3) * 0.3 + Math.sin(seconds * 4.1 + 2.1) * 0.2)
}

function approach(current: number, goal: number, dt: number, tau: number): number {
  if (tau <= 0) return goal
  return current + (goal - current) * (1 - Math.exp(-dt / tau))
}

/** 依經過的時間更新平滑值；每次取值前呼叫。 / Advance the smoothed values by the elapsed time. */
function tick(): void {
  const t = now()
  const dt = lastTick === null ? 0 : Math.min(1, (t - lastTick) / 1000)
  lastTick = t
  const instant = Math.max(0, target.speedMs * (1 + target.gustiness * fluctuation(t / 1000)))
  const tau = target.smoothingSeconds
  const speedMs = approach(state.speedMs, instant, dt, tau)
  const colorSpeedMs = approach(state.colorSpeedMs, instant, dt, tau * 3)
  let flowDeg = state.flowDeg
  if (speedMs >= CALM_DIRECTION_MS || tau <= 0) {
    const delta = ((target.flowDeg - flowDeg + 540) % 360) - 180
    const limit = tau <= 0 ? 180 : MAX_TURN_DEG_PER_SECOND * dt
    flowDeg = (flowDeg + Math.max(-limit, Math.min(limit, delta)) + 360) % 360
  }
  state = { speedMs, colorSpeedMs, flowDeg, instantMs: instant }
}

export function getWindPreview(): WindPreviewState {
  tick()
  return state
}

export function getWindTarget(): WindPreviewTarget {
  return target
}

export function setWindTarget(next: Partial<WindPreviewTarget>): void {
  target = { ...target, ...next }
  listeners.forEach((listener) => listener())
}

export function subscribeWindPreview(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 0–1：低於 calmMs 為 0，達到 fullMs 為 1，再套 curve。 / 0 below calmMs, 1 at fullMs, then the curve. */
export function windStrength(speedMs: number, p: Readonly<Record<string, number>>): number {
  const calm = p.calmMs ?? 1
  const full = Math.max(calm + 0.1, p.fullMs ?? 12)
  return clamp01((speedMs - calm) / (full - calm)) ** (p.curve ?? 1)
}

/** 目前的流速（圈/秒）= 基準流速 × 倍率（無風 minRate → fullMs 時 maxRate）。 / Current flow speed in turns per second. */
export function windFlowRate(speedMs: number, p: Readonly<Record<string, number>>): number {
  const minRate = p.minRate ?? 0.6
  const maxRate = p.maxRate ?? 3
  return (p.speedHz ?? 0.2) * (minRate + (maxRate - minRate) * windStrength(speedMs, p))
}

export type WindAdvance = {
  turns: number
  /** 亮點帶進度 0–1（null = 沒有亮點帶）。 / Sparkle sweep progress 0–1 (null: no sweep). */
  sweep: number | null
  /** 這道亮點帶開始時鎖定的風向（場地方向）。 / Wind direction locked when the sweep started. */
  sweepDeg: number
  /** 已播放的秒數（不看影格，串接段落重複也不歸零）。 / Seconds played, independent of the frame number. */
  played: number
}

type Accumulator = {
  /** 上一次被呼叫的時間（ms）與那一批的第一個影格。 / Last call time (ms) and the first frame of that burst. */
  lastCall: number
  /** 上一次推進狀態的時間（ms）。 / Time of the last advance (ms). */
  lastAdvance: number
  burstFrame: number
  /** 已播放的秒數（暫停時不前進）。 / Seconds played (does not advance while paused). */
  played: number
  turns: number
  /** 進行中的亮點帶進度 0–1（null = 沒有）。 / Progress 0–1 of the current sweep (null: none). */
  sweepStart: number | null
  sweepDeg: number
  /** 下一道亮點帶最早可以開始的 played。 / Earliest played the next sweep may start. */
  nextSweep: number
  result: WindAdvance
}
const accumulators = new Map<string, Accumulator>()
/** 同一批（同一次畫面更新）的呼叫間隔遠小於這個值。 / Calls within one render burst are far closer than this. */
const BURST_GAP_MS = 4

/**
 * 累積流過的圈數，並推進亮點帶。以實際經過的時間推進，不看影格編號：串接段落重複、效果週期歸零、3D 殘影回頭取
 * 前幾格，影格都會變小，但流動與亮點帶不能因此倒退或停住。同一次畫面更新內的所有呼叫拿到同一個狀態。
 * 風速達到 sparkleBelowMs（亮點門檻）時開始一道亮點帶：鎖定當下的風向，花 sweepSeconds 掃到最下風處，途中風速、風向
 * 再怎麼變都會掃完；結束後隔 gapSeconds，風速仍達到門檻才開始下一道。
 * Accumulates the turns and advances the sparkle sweeps by elapsed wall time, not by frame number: looping sequence
 * steps, effect cycles and the 3D trail reading back all make the frame go down, and none of them may rewind or
 * freeze the flow. Every call in one render burst gets the same state. At or above sparkleBelowMs (the sparkle threshold) a sweep starts with the current direction locked and always runs to the downwind
 * end in sweepSeconds; the next one may start gapSeconds later.
 */
export function advanceWind(key: string, frame: number, fps: number, p: Readonly<Record<string, number>>): WindAdvance {
  const wind = getWindPreview()
  const t = now()
  let accumulator = accumulators.get(key)
  if (!accumulator) {
    if (accumulators.size > 32) accumulators.clear()
    accumulator = {
      lastCall: t, lastAdvance: t, burstFrame: frame, played: 0, turns: 0, sweepStart: null, sweepDeg: wind.flowDeg, nextSweep: 0,
      result: { turns: 0, sweep: null, sweepDeg: wind.flowDeg, played: 0 },
    }
    accumulators.set(key, accumulator)
  }
  const gap = t - accumulator.lastCall
  accumulator.lastCall = t
  if (gap < BURST_GAP_MS) return accumulator.result
  // 不用影格判斷暫停：串接段落停在最後一格、轉場時影格不變，亮點帶會卡在半路不消失。
  // No pause check by frame: a sequence step holding its last frame would freeze a sweep halfway.
  accumulator.burstFrame = frame
  // 一次最多補 0.25 秒（分頁在背景、暫停後繼續）。 / Catch up at most 0.25 s (hidden tab, resume after pause).
  // 經過的時間從上一次推進算起，不是從上一次呼叫：一批呼叫本身的運算時間（裝置慢時每批十幾 ms）也要算進去，
  // 否則流動、亮點帶和間隔都會變慢。
  // Elapsed since the last advance, not the last call: the time a burst itself takes to compute (over 10 ms on a
  // slow device) must count, or the flow, sweeps and gaps all run slow.
  const elapsed = Math.min((t - accumulator.lastAdvance) / 1000, 0.25)
  accumulator.lastAdvance = t
  accumulator.played += elapsed
  accumulator.turns += elapsed * windFlowRate(wind.speedMs, p)
  const below = p.sparkleBelowMs
  if (below !== undefined) {
    if (accumulator.sweepStart !== null) {
      // 風越大掃得越快：門檻時花 sweepSeconds，最大風速時花 sweepFastSeconds，中間依速度（1/秒數）線性內插；
      // 進度逐格累積，所以途中風速改變也不會跳。
      // Faster in stronger wind: sweepSeconds at the threshold, sweepFastSeconds at fullMs, the speed (1 / seconds)
      // interpolated between; the progress accumulates, so a change of wind mid-sweep never jumps.
      const slow = 1 / Math.max(0.1, p.sweepSeconds ?? 2)
      const fast = 1 / Math.max(0.1, Math.min(p.sweepFastSeconds ?? 0.6, p.sweepSeconds ?? 2))
      const full = Math.max(below + 0.1, p.fullMs ?? 12)
      const amount = clamp01((wind.speedMs - below) / (full - below))
      accumulator.sweepStart += elapsed * (slow + (fast - slow) * amount)
      if (accumulator.sweepStart >= 1) {
        accumulator.sweepStart = null
        accumulator.nextSweep = accumulator.played + (p.gapSeconds ?? 6)
      }
    }
    if (accumulator.sweepStart === null && wind.speedMs >= below && accumulator.played >= accumulator.nextSweep) {
      accumulator.sweepStart = 0
      accumulator.sweepDeg = wind.flowDeg
    }
  }
  accumulator.result = {
    turns: accumulator.turns,
    sweep: accumulator.sweepStart,
    sweepDeg: accumulator.sweepDeg,
    played: accumulator.played,
  }
  return accumulator.result
}

/**
 * 沿風向的場地位置 0–1：把單位方格的四個角投影到風向上，最上風處 = 0、最下風處 = 1。
 * Position along the wind, 0–1: the unit square's corners projected on the direction, most upwind = 0.
 */
export function alongWind(x: number, y: number, flowDeg: number): number {
  const angle = (flowDeg * Math.PI) / 180
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const low = Math.min(0, c) + Math.min(0, s)
  const high = Math.max(0, c) + Math.max(0, s)
  return high === low ? 0.5 : (x * c + y * s - low) / (high - low)
}

export type WindBandSample = {
  /** 柔和亮帶的亮度。 / Soft band brightness. */
  band: number
  /** 隨機亮點的亮度。 / Sparkle brightness. */
  sparkle: number
  /** 0 = 純亮帶、1 = 純亮點。 / 0: band only, 1: sparkles only. */
  mix: number
  colorPosition: number
}

/** 決定流動與亮點帶時間的參數；這些值相同的群組共用同一個狀態，所以轉燈和球燈的亮帶、亮點帶同步。 */
/** Parameters that set the timing; groups with the same values share one state, so spinners and balls stay in step. */
const TIMING_KEYS = [
  'speedHz', 'minRate', 'maxRate', 'calmMs', 'fullMs', 'curve', 'sparkleBelowMs', 'sweepSeconds', 'sweepFastSeconds', 'gapSeconds',
] as const
const timingKeyCache = new WeakMap<object, string>()

function timingKey(p: Readonly<Record<string, number>>): string {
  let key = timingKeyCache.get(p)
  if (key === undefined) {
    key = TIMING_KEYS.map((name) => p[name]).join(',')
    timingKeyCache.set(p, key)
  }
  return key
}

function hash01(a: number, b: number, salt: number): number {
  let h = (Math.imul(a | 0, 0x9e3779b1) ^ Math.imul(b | 0, 0x85ebca6b) ^ Math.imul(salt | 0, 0xc2b2ae35)) >>> 0
  h = Math.imul(h ^ (h >>> 16), 0x7feb352d) >>> 0
  h = Math.imul(h ^ (h >>> 15), 0x846ca68b) >>> 0
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

/**
 * 風流（wind-flow）一個點的取樣，轉燈的 LED 與球燈、洗牆燈共用：
 * - 柔和的亮帶沿風吹往的方向流過全場：相位 = 沿風向的位置 × 亮帶數 + vertical − 流過的圈數，離亮帶中心越遠越暗（高斯形）。
 * - 風速達到門檻時，一道亮點帶沿（開始時鎖定的）風向掃過；帶內平順地由亮帶換成隨機閃爍的亮點。
 * - 顏色依（較慢平滑的）風速在漸層上取，波峰再往風較大那側偏 colorSpread。
 * x、y 是群組內的平面位置 0–1；vertical 是額外的相位（轉燈：球面由上到下；球燈：依高度）；id 決定各點亮點的隨機。
 *
 * One wind-flow sample, shared by spinner LEDs and ball / wash fixtures: a soft Gaussian band travelling along the
 * wind, a sparkle band sweeping along the locked direction at or above the threshold, and a colour picked along the
 * gradient by the slower smoothed wind speed. vertical adds phase (sphere top to bottom, or fixture height); id seeds
 * each point's sparkle.
 */
type BurstState = {
  burst: number
  turns: number
  sweep: number | null
  played: number
  colorStrength: number
  /** 沿風向位置的係數：along = (x × c + y × s − low) × scale。 / along = (x × c + y × s − low) × scale. */
  flow: [c: number, s: number, low: number, scale: number]
  sweepFlow: [c: number, s: number, low: number, scale: number]
}

function alongCoefficients(flowDeg: number): [number, number, number, number] {
  const angle = (flowDeg * Math.PI) / 180
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const low = Math.min(0, c) + Math.min(0, s)
  const high = Math.max(0, c) + Math.max(0, s)
  return [c, s, low, high === low ? 0 : 1 / (high - low)]
}

// 同一次畫面更新（同一個 JS 工作）內，風力狀態對每個取樣點都相同：只算一次，工作結束後（microtask）作廢。
// 原本每顆 LED 都重讀時間、重算平滑與亮點帶，24 台 × 129 顆每格上萬次，在 iPad 上會卡。
// Within one render pass (one JS task) the wind state is the same for every sample: computed once, invalidated by a
// microtask when the task ends. Re-reading the clock and smoothing for every LED (over 10,000 times a frame) janked
// on an iPad.
let burst = 0
let burstQueued = false
const burstStates = new WeakMap<object, BurstState>()
let lastParameters: object | null = null
let lastState: BurstState | null = null

function burstState(frame: number, fps: number, p: Readonly<Record<string, number>>): BurstState {
  if (lastParameters === p && lastState !== null && lastState.burst === burst) return lastState
  let state = burstStates.get(p)
  if (!state || state.burst !== burst) {
    if (!burstQueued) {
      burstQueued = true
      queueMicrotask(() => { burst += 1; burstQueued = false })
    }
    const wind = getWindPreview()
    const { turns, sweep, sweepDeg, played } = advanceWind(timingKey(p), frame, fps, p)
    state = {
      burst, turns, sweep, played,
      colorStrength: windStrength(wind.colorSpeedMs, p),
      flow: alongCoefficients(wind.flowDeg),
      sweepFlow: alongCoefficients(sweepDeg),
    }
    burstStates.set(p, state)
  }
  lastParameters = p
  lastState = state
  return state
}

export function sampleWindBand(
  frame: number, fps: number, p: Readonly<Record<string, number>>, x: number, y: number, vertical: number, id: number,
): WindBandSample {
  const state = burstState(frame, fps, p)
  const flow = state.flow
  const along = flow[3] === 0 ? 0.5 : (x * flow[0] + y * flow[1] - flow[2]) * flow[3]
  const cycle = (p.waveCount ?? 1) * along + vertical - state.turns
  const distance = Math.abs(cycle - Math.round(cycle)) * 2
  const crest = Math.exp(-((distance / (p.bandSoftness ?? 0.5)) ** 2) * 2)
  const minLevel = p.minLevel ?? 0.2
  const band = minLevel + (1 - minLevel) * crest
  const colorPosition = clamp01(state.colorStrength + (p.colorSpread ?? 0.15) * (crest - 0.5))
  if (state.sweep === null) return { band, sparkle: 0, mix: 0, colorPosition }
  // 亮點帶：前緣由最上風處前一個帶寬掃到最下風處後一個帶寬。 / From one band width upwind to one past the far end.
  const width = p.bandWidth ?? 0.3
  const centre = -width / 2 + state.sweep * (1 + width)
  const sweepFlow = state.sweepFlow
  const sweepAlong = sweepFlow[3] === 0 ? 0.5 : (x * sweepFlow[0] + y * sweepFlow[1] - sweepFlow[2]) * sweepFlow[3]
  const inside = clamp01(1 - Math.abs(sweepAlong - centre) / (width / 2))
  const mix = inside * inside * (3 - 2 * inside)
  if (mix <= 0) return { band, sparkle: 0, mix: 0, colorPosition }
  // 每個點各自錯開的閃爍（依播放時間）：每 twinkleFrames 一格，依比例亮起，格內以半個正弦亮起再熄。
  // Each point twinkles on its own offset (by played time): one cell per twinkleFrames, lit by density.
  const length = Math.max(1, Math.round(p.twinkleFrames ?? 6))
  const local = Math.floor(state.played * 40) + Math.floor(hash01(0, id, 67) * length)
  const cell = Math.floor(local / length)
  const within = local - cell * length
  const sparkle = hash01(cell, id, 71) < (p.density ?? 0.15) ? Math.sin((Math.PI * (within + 0.5)) / length) : 0
  return { band, sparkle, mix, colorPosition }
}

type WindParameter = { key: string; label: string; unit: string; min: number; max: number; step: number; defaultValue: number }

/** 風流的參數；vertical 是各群組自己的「上下錯開」參數，density 預設依群組不同。 / wind-flow parameters per group. */
export function windFlowParameters(vertical: WindParameter, density: number): WindParameter[] {
  return [
    { key: 'speedHz', label: '基準流速', unit: '圈/秒', min: 0.02, max: 1, step: 0.02, defaultValue: 0.15 },
    { key: 'minRate', label: '無風倍率', unit: '倍', min: 0, max: 2, step: 0.1, defaultValue: 0.6 },
    { key: 'maxRate', label: '強風倍率', unit: '倍', min: 1, max: 8, step: 0.25, defaultValue: 3 },
    { key: 'calmMs', label: '無風門檻', unit: 'm/s', min: 0, max: 5, step: 0.5, defaultValue: 1 },
    { key: 'fullMs', label: '最大風速', unit: 'm/s', min: 3, max: 25, step: 0.5, defaultValue: 12 },
    { key: 'curve', label: '風速曲線', unit: '（>1 小風變化小）', min: 0.5, max: 3, step: 0.1, defaultValue: 1 },
    { key: 'waveCount', label: '場地亮帶數', unit: '', min: 0.5, max: 4, step: 0.5, defaultValue: 1 },
    vertical,
    { key: 'bandSoftness', label: '亮帶寬度', unit: '（越大越柔和）', min: 0.1, max: 1, step: 0.05, defaultValue: 0.5 },
    { key: 'minLevel', label: '最低亮度', unit: '', min: 0, max: 0.9, step: 0.05, defaultValue: 0.2 },
    { key: 'colorSpread', label: '波峰色偏', unit: '', min: 0, max: 0.5, step: 0.05, defaultValue: 0.15 },
    { key: 'sparkleBelowMs', label: '亮點門檻 X', unit: 'm/s（達到時亮點帶掃過）', min: 0, max: 15, step: 0.5, defaultValue: 3 },
    { key: 'sweepSeconds', label: '亮點帶掃過時間（門檻風速）', unit: 's', min: 0.3, max: 30, step: 0.1, defaultValue: 2 },
    { key: 'sweepFastSeconds', label: '亮點帶掃過時間（最大風速）', unit: 's', min: 0.2, max: 30, step: 0.1, defaultValue: 0.6 },
    { key: 'gapSeconds', label: '亮點帶間隔', unit: 's', min: 0, max: 60, step: 0.5, defaultValue: 6 },
    { key: 'bandWidth', label: '亮點帶寬度', unit: '（場地 0–1）', min: 0.05, max: 1, step: 0.05, defaultValue: 0.3 },
    { key: 'density', label: '亮點比例', unit: '', min: 0.02, max: 1, step: 0.02, defaultValue: density },
    { key: 'twinkleFrames', label: '亮點閃爍長度', unit: '影格', min: 1, max: 40, step: 1, defaultValue: 6 },
  ]
}

/** 預設漸層：無風 → 強風。 / Default gradient, calm to strong. */
export const WIND_FLOW_COLORS = [
  { r: 0.14, g: 0.27, b: 0.67 },
  { r: 0.2, g: 0.75, b: 0.9 },
  { r: 0.56, g: 1, b: 0.78 },
  { r: 1, g: 0.7, b: 0.28 },
  { r: 1, g: 0.28, b: 0.49 },
]

const DESCRIPTION_TAIL = '風越大流得越快（基準流速 × 無風倍率 → 強風倍率）。風速達到「亮點門檻 X」時，另有一道隨機亮點帶沿風向'
  + '週期性掃過全場（風越大掃得越快：門檻風速時花「掃過時間（門檻風速）」、最大風速時花「掃過時間（最大風速）」；之後隔「間隔」再來一道），'
  + '經過的燈變成隨機閃爍的亮點，離開後恢復亮帶；每道開始時鎖定當下的風向，一定掃到底。低於門檻時只有亮帶。'
  + '效果依實際時間進行，不受串接段落重複、重設時間影響；流速、門檻、掃過時間、間隔都相同的群組（轉燈、球燈、洗牆燈）會同步。'
  + '漸層代表風速：最左邊 = 無風、最右邊 = 最大風速。風速、風向用「預覽風力」調整。'

export const WIND_FLOW_SPINNER_DESCRIPTION = '【風力效果原型，只在前端預覽】柔和的亮帶沿風吹往的方向流過全場的轉燈，'
  + '經過每台時由上往下掃過（「球面上下錯開」設 0 = 整顆一起亮），' + DESCRIPTION_TAIL

export const WIND_FLOW_FIXTURE_DESCRIPTION = '【風力效果原型，只在前端預覽】柔和的亮帶沿風吹往的方向流過全場的燈，'
  + '「高度錯開」大於 0 時較高的燈先亮、較低的晚一點（同轉燈由上往下），' + DESCRIPTION_TAIL
