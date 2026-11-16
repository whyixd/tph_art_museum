/**
 * 色彩漫遊（color-roam）：起點那台是 A 色（漸層最後一色），其他轉燈是 B 色（第一色）；離起點越近越接近 A，
 * 超過影響範圍就是 B。起點在轉燈之間慢慢移動：下一台從最近的幾台中隨機挑、不回到剛走過的，移動時位置在兩台之間
 * 連續滑過去（漸層整團平順移動）。距離用轉燈中心的立體座標（cm）；沒有場地配置時依編號排成一排（每台 250 cm）。
 * 起點一直走下去、不重選，所以不會瞬移；路徑由固定種子決定，後端 color_roam.py 逐值一致。
 *
 * color-roam: the roaming unit shows colour A (the gradient's last colour), the rest colour B (the first); the closer
 * to the roamer the closer to A, B beyond the reach. The roamer moves slowly between spinners: the next one is a random
 * pick among the closest few, skipping the ones just visited, and its position slides continuously between the two
 * (the whole blend moves smoothly). Distance is 3D between spinner centres (cm); without a layout, units stand in a
 * row 250 cm apart. The walk never restarts, so it never jumps; fixed seeds make the backend color_roam.py match.
 */
import { EFFECT_SCHEMA_VERSION, clamp01, type EffectModule, type EffectSampleContext, type UnitPositions } from './contract'
import { sampleGradient } from './color'
import { hash01 } from './rouletteRing'

const DEFAULTS = { radiusCm: 400, stepSeconds: 4, choices: 3, noReturn: 2, roamers: 1, curve: 1 }
/** 沒有場地配置時相鄰兩台的距離（約同柱兩台）。 / Spacing without a layout (about two units on a pole). */
export const ROAM_INDEX_SPACING_CM = 250
// 雜湊的鹽，與其他效果分開。 / Hash salts, apart from the other effects.
const START_SALT = 71
const PICK_SALT = 73
/** 每段記一次走到哪（檢查點），跳著看也不用從頭算。 / Steps per checkpoint, so seeking need not start over. */
export const ROAM_BLOCK_STEPS = 256

type RoamLayout = { points: number[][]; neighbors: number[][] }
type RoamSettings = { roamers: number; choices: number; noReturn: number }

/**
 * 每台的座標（用每台都有的軸）與依距離由近到遠的其他台（同距離依編號）。
 * Each unit's point (the axes every unit has) and the other units by distance, nearest first (ties by index).
 */
export function roamLayout(positions: UnitPositions | undefined, count: number): RoamLayout {
  const usable = positions && positions.length === count ? positions : null
  const axes = usable === null ? [] : (['x', 'y', 'z'] as const).filter((key) => count > 0 && usable.every((position) => position?.[key] !== undefined))
  const points = axes.length > 0
    ? usable!.map((position) => axes.map((axis) => position![axis]!))
    : Array.from({ length: count }, (_, index) => [index * ROAM_INDEX_SPACING_CM])
  const neighbors = points.map((point, index) => {
    const others: [number, number][] = []
    for (let other = 0; other < count; other += 1) {
      if (other === index) continue
      let distance = 0
      for (let axis = 0; axis < point.length; axis += 1) {
        const delta = point[axis] - points[other][axis]
        distance += delta * delta
      }
      others.push([distance, other])
    }
    others.sort((a, b) => a[0] - b[0] || a[1] - b[1])
    return others.map(([, other]) => other)
  })
  return { points, neighbors }
}

const layoutCache = new WeakMap<UnitPositions, RoamLayout>()
const indexLayouts = new Map<number, RoamLayout>()

function cachedLayout(positions: UnitPositions | undefined, count: number): RoamLayout {
  if (positions && positions.length === count) {
    let layout = layoutCache.get(positions)
    if (!layout) {
      layout = roamLayout(positions, count)
      layoutCache.set(positions, layout)
    }
    return layout
  }
  let layout = indexLayouts.get(count)
  if (!layout) {
    layout = roamLayout(undefined, count)
    indexLayouts.set(count, layout)
  }
  return layout
}

export function roamSettings(p: Readonly<Record<string, number>>, count: number): RoamSettings {
  return {
    roamers: Math.min(count, Math.max(1, Math.trunc(p.roamers ?? DEFAULTS.roamers))),
    choices: Math.max(1, Math.trunc(p.choices ?? DEFAULTS.choices)),
    noReturn: Math.max(0, Math.trunc(p.noReturn ?? DEFAULTS.noReturn)),
  }
}

/** 起點：雜湊最小的 roamers 台（不重複）。 / Starts: the `roamers` units with the smallest hash. */
export function roamStarts(count: number, roamers: number): number[] {
  const order = Array.from({ length: count }, (_, index) => index)
  const keys = order.map((index) => hash01(0, index, START_SALT))
  order.sort((a, b) => keys[a] - keys[b] || a - b)
  return order.slice(0, roamers)
}

/**
 * 第 step 步：從 tail 最後一台走到下一台。tail = 最近走過的幾台（最後一台是目前這台）。
 * Step `step`: from the last unit of `tail` to the next. `tail` holds the units just visited, current last.
 */
function roamPick(layout: RoamLayout, settings: RoamSettings, roamer: number, step: number, tail: readonly number[]): number {
  const current = tail[tail.length - 1]
  const all = layout.neighbors[current]
  if (all.length === 0) return current
  const recent = tail.slice(0, -1)
  let candidates = all.filter((other) => !recent.includes(other))
  if (candidates.length === 0) candidates = all
  candidates = candidates.slice(0, settings.choices)
  return candidates[Math.floor(hash01(step, roamer, PICK_SALT) * candidates.length)]
}

type Walk = {
  layout: RoamLayout
  key: string
  settings: RoamSettings
  /** checkpoints[b][roamer]：第 b × BLOCK 步時的 tail。 / The tail at step b × BLOCK. */
  checkpoints: number[][][]
  /** 目前這段：nodes[roamer][i] = 第 index × BLOCK + i 步所在（含下一段第一步）。 / The current block's units. */
  block: { index: number; nodes: number[][] } | null
}

function runBlock(walk: Walk, index: number): { nodes: number[][]; next: number[][] } {
  const { layout, settings } = walk
  const nodes: number[][] = []
  const next: number[][] = []
  walk.checkpoints[index].forEach((start, roamer) => {
    const tail = [...start]
    const path = [tail[tail.length - 1]]
    for (let step = index * ROAM_BLOCK_STEPS; step < (index + 1) * ROAM_BLOCK_STEPS; step += 1) {
      const unit = roamPick(layout, settings, roamer, step, tail)
      path.push(unit)
      tail.push(unit)
      if (tail.length > settings.noReturn + 1) tail.shift()
    }
    nodes.push(path)
    next.push(tail)
  })
  return { nodes, next }
}

const walks: Walk[] = []

function walkFor(layout: RoamLayout, settings: RoamSettings): Walk {
  const key = `${settings.roamers}:${settings.choices}:${settings.noReturn}`
  let walk = walks.find((entry) => entry.layout === layout && entry.key === key)
  if (!walk) {
    walk = {
      layout, key, settings, block: null,
      checkpoints: [roamStarts(layout.points.length, settings.roamers).map((start) => [start])],
    }
    walks.unshift(walk)
    if (walks.length > 4) walks.pop()
  }
  return walk
}

/**
 * 每個起點在第 step 步的起訖兩台。 / Each roamer's [from, to] units on step `step`.
 */
export function roamSegment(layout: RoamLayout, settings: RoamSettings, step: number): [number, number][] {
  const walk = walkFor(layout, settings)
  const index = Math.floor(step / ROAM_BLOCK_STEPS)
  if (walk.block?.index !== index) {
    while (walk.checkpoints.length <= index) {
      const last = walk.checkpoints.length - 1
      const { nodes, next } = runBlock(walk, last)
      walk.checkpoints.push(next)
      walk.block = { index: last, nodes }
    }
    if (walk.block?.index !== index) walk.block = { index, nodes: runBlock(walk, index).nodes }
  }
  const offset = step - index * ROAM_BLOCK_STEPS
  return walk.block!.nodes.map((path) => [path[offset], path[offset + 1]])
}

function smoothstep(value: number): number {
  const t = clamp01(value)
  return t * t * (3 - 2 * t)
}

export function roamStepFrames(p: Readonly<Record<string, number>>, fps: number): number {
  return Math.max(1, Math.round((p.stepSeconds ?? DEFAULTS.stepSeconds) * fps))
}

/** 這台接近 A 色的程度 0..1（1 = 起點正在這台）。 / How close this unit is to colour A (1 = the roamer is here). */
export function colorRoamStrength(context: EffectSampleContext, p: Readonly<Record<string, number>>): number {
  const count = Math.max(1, context.unitCount)
  const unit = Math.min(count - 1, Math.max(0, context.unitIndex))
  const layout = cachedLayout(context.unitPositions, count)
  const settings = roamSettings(p, count)
  const stepFrames = roamStepFrames(p, context.controllerFps)
  const at = Math.max(0, context.controllerFrame)
  const step = Math.floor(at / stepFrames)
  const linear = (at - step * stepFrames) / stepFrames
  // 移動曲線：0 等速，1 兩端放慢。 / Curve: 0 linear, 1 eased at both ends.
  const progress = (p.curve ?? DEFAULTS.curve) === 1 ? smoothstep(linear) : linear
  const radius = p.radiusCm ?? DEFAULTS.radiusCm
  const point = layout.points[unit]
  let strength = 0
  for (const [from, to] of roamSegment(layout, settings, step)) {
    const a = layout.points[from]
    const b = layout.points[to]
    let squared = 0
    for (let axis = 0; axis < point.length; axis += 1) {
      const delta = point[axis] - (a[axis] + (b[axis] - a[axis]) * progress)
      squared += delta * delta
    }
    strength = Math.max(strength, 1 - smoothstep(Math.sqrt(squared) / radius))
  }
  return strength
}

export const colorRoam: EffectModule = {
  completion: {
    label: '起點走過與台數相同的步數',
    frames: (parameters, fps, unitCount) => roamStepFrames(parameters, fps) * Math.max(1, unitCount),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'color-roam',
    label: '色彩漫遊',
    description: '起點那台是漸層的最後一色（A），其他轉燈是第一色（B）；離起點越近越接近 A，超過影響範圍就是 B（中間有顏色時漸層會經過它們）。起點在轉燈之間慢慢移動：下一台從最近的幾台中隨機挑、不回到剛走過的幾台，移動時在兩台之間連續滑過去，整團顏色平順移動、不會瞬移。距離依場地配置的轉燈立體座標（含高度）。純色時 B 是全暗，像一團光在場地裡漫遊。多個起點時各自漫遊，重疊處取最接近 A 的。全部轉燈共用同一條路徑，不受天氣的多雲錯開與雨天線條影響。',
    parameters: [
      { key: 'radiusCm', label: '影響範圍', unit: 'cm', min: 50, max: 2000, step: 10, defaultValue: DEFAULTS.radiusCm },
      { key: 'stepSeconds', label: '每段移動時間', unit: 's', min: 0.5, max: 30, step: 0.5, defaultValue: DEFAULTS.stepSeconds },
      { key: 'choices', label: '下一台的候選數', unit: '台', min: 1, max: 6, step: 1, defaultValue: DEFAULTS.choices },
      { key: 'noReturn', label: '不回頭步數', unit: '步', min: 0, max: 6, step: 1, defaultValue: DEFAULTS.noReturn },
      { key: 'roamers', label: '起點數量', unit: '個', min: 1, max: 6, step: 1, defaultValue: DEFAULTS.roamers },
      { key: 'curve', label: '移動曲線', unit: '0 等速／1 兩端放慢', min: 0, max: 1, step: 1, defaultValue: DEFAULTS.curve },
    ],
  },
  ignoresWeatherTiming: true,
  // 漸層時整台全亮、靠顏色表現；純色時 B 是全暗。 / Gradient: fully lit, shown by colour; solid: B is dark.
  brightness: (context, colorMode, parameters) =>
    colorMode === 'gradient' ? 1 : colorRoamStrength(context, parameters ?? {}),
  sample(context, parameters) {
    const strength = colorRoamStrength(context, parameters)
    return { r: strength, g: strength, b: strength }
  },
  // 不論方向：第一色 B、最後一色 A。 / Any axis: the first colour is B, the last A.
  gradientColor: (context, parameters, colors) => sampleGradient(colors, colorRoamStrength(context, parameters), false),
}
