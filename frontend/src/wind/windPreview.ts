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

const keyCache = new WeakMap<object, string>()

/** 累積器的鍵：群組＋參數（每個參數物件只序列化一次）。 / Accumulator key: group plus parameters, serialized once per object. */
export function windKey(group: string, parameters: Readonly<Record<string, number>>): string {
  let key = keyCache.get(parameters)
  if (key === undefined) {
    key = JSON.stringify(parameters)
    keyCache.set(parameters, key)
  }
  return group + ':' + key
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

/** 流過的圈數（球燈、洗牆燈的風流）。 / Turns travelled (fixture wind-flow). */
export function windFlowTurns(key: string, frame: number, fps: number, p: Readonly<Record<string, number>>): number {
  return advanceWind(key, frame, fps, p).turns
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

export type WindFlowSample = { brightness: number; colorPosition: number }

/**
 * 風流（wind-flow）：亮帶沿風吹往的方向流過場地，風越大流得越快；顏色依（較慢平滑的）風速在漸層上取色，
 * 亮帶波峰再往「風較大」那一側偏 colorSpread。
 * wind-flow: bright bands travel across the site toward where the wind blows, faster in stronger wind; the colour is
 * picked along the gradient by the (slower smoothed) wind speed, with crests shifted colorSpread toward stronger wind.
 */
export function sampleWindFlow(
  key: string, frame: number, fps: number, p: Readonly<Record<string, number>>, x: number, y: number,
): WindFlowSample {
  const wind = getWindPreview()
  const turns = windFlowTurns(key, frame, fps, p)
  const angle = (wind.flowDeg * Math.PI) / 180
  const along = x * Math.cos(angle) + y * Math.sin(angle)
  const phase = (p.waveCount ?? 1) * along - turns
  const crest = 0.5 + 0.5 * Math.cos(2 * Math.PI * phase)
  const depth = p.depth ?? 0.6
  const brightness = 1 - depth * (1 - crest)
  const colorPosition = clamp01(windStrength(wind.colorSpeedMs, p) + (p.colorSpread ?? 0.15) * (crest - 0.5))
  return { brightness, colorPosition }
}

/** 風力效果共用的參數（前後端預設值同 PLAN.md §10.6）。 / Shared wind-flow parameters. */
export const WIND_FLOW_PARAMETERS = [
  { key: 'speedHz', label: '基準流速', unit: '圈/秒', min: 0.02, max: 1, step: 0.02, defaultValue: 0.2 },
  { key: 'minRate', label: '無風倍率', unit: '倍', min: 0, max: 2, step: 0.1, defaultValue: 0.6 },
  { key: 'maxRate', label: '強風倍率', unit: '倍', min: 1, max: 8, step: 0.25, defaultValue: 3 },
  { key: 'calmMs', label: '無風門檻', unit: 'm/s', min: 0, max: 5, step: 0.5, defaultValue: 1 },
  { key: 'fullMs', label: '最大風速', unit: 'm/s', min: 3, max: 25, step: 0.5, defaultValue: 12 },
  { key: 'curve', label: '風速曲線', unit: '（>1 小風變化小）', min: 0.5, max: 3, step: 0.1, defaultValue: 1 },
  { key: 'waveCount', label: '色帶數', unit: '', min: 0.5, max: 6, step: 0.5, defaultValue: 1 },
  { key: 'depth', label: '亮度起伏', unit: '', min: 0, max: 1, step: 0.05, defaultValue: 0.6 },
  { key: 'colorSpread', label: '波峰色偏', unit: '', min: 0, max: 0.5, step: 0.05, defaultValue: 0.15 },
] as const

/** 預設漸層：無風 → 強風。 / Default gradient, calm to strong. */
export const WIND_FLOW_COLORS = [
  { r: 0.14, g: 0.27, b: 0.67 },
  { r: 0.2, g: 0.75, b: 0.9 },
  { r: 0.56, g: 1, b: 0.78 },
  { r: 1, g: 0.7, b: 0.28 },
  { r: 1, g: 0.28, b: 0.49 },
]

export const WIND_FLOW_DESCRIPTION =
  '【風力效果原型，只在前端預覽】亮帶沿風吹往的方向流過整個場地，風越大流得越快（基準流速 × 無風倍率 → 強風倍率）；'
  + '漸層代表風速：最左邊的顏色 = 無風、最右邊 = 最大風速，波峰再往風較大那側偏「波峰色偏」。純色時只有亮帶流動。'
  + '風速、風向用「預覽風力」調整。'
