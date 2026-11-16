/**
 * 亮度漫遊（brightness-walk）：隨機選幾顆球燈當起點，亮度從目前這顆（A）逐漸流到附近的下一顆（B），A + B 恆為 1；
 * 流完再從 B 往下一顆走。下一顆在離目前這顆最近的幾顆中隨機挑，且不回到剛走過的幾顆（只找最近的會困在同一根柱子繞圈）。
 * 幾條路徑流到同一顆時亮度相加，最高 1。路徑由固定種子決定，後端 fixture_effects.py 逐值一致。
 *
 * brightness-walk: a few random balls start; brightness flows from the current ball (A) to a nearby next one (B)
 * with A + B = 1, then on from B. The next ball is a random pick among the closest few, skipping the balls just
 * visited (only the very nearest would circle one pole forever). Paths meeting on a ball add up, capped at 1.
 * Paths come from fixed seeds, so backend fixture_effects.py matches value for value.
 */
import { hash01 } from '../effects/rouletteRing'

/** 每顆燈的其他燈，依距離由近到遠（同距離依編號）。 / Every other fixture by distance, nearest first (ties by index). */
export type FixtureNeighbors = readonly (readonly number[])[]

type Position = { x?: number; y?: number; z?: number }

// 雜湊的鹽，與其他球燈效果分開。 / Hash salts, apart from the other fixture effects.
const START_SALT = 61
const PICK_SALT = 67
/** 第幾條路徑 × 這個數 + 第幾步 = 雜湊的 unit（步數上限 200 < 1024）。 / walker × this + step is the hash unit. */
const WALKER_STRIDE = 1024

/**
 * 依 layout 位置（cm）算鄰近順序：用每顆都有的軸（x、y、z）算距離平方；沒有任何共同軸時依編號差。
 * Neighbour order from layout positions (cm): squared distance over the axes every fixture has; by index otherwise.
 */
export function fixtureNeighbors(positions: readonly (Position | null)[]): number[][] {
  const count = positions.length
  const axes = (['x', 'y', 'z'] as const).filter((key) => count > 0 && positions.every((position) => position?.[key] !== undefined))
  return positions.map((_, index) => {
    const others: [number, number][] = []
    for (let other = 0; other < count; other += 1) {
      if (other === index) continue
      let distance = 0
      if (axes.length === 0) {
        distance = (index - other) * (index - other)
      } else {
        for (const axis of axes) {
          const delta = positions[index]![axis]! - positions[other]![axis]!
          distance += delta * delta
        }
      }
      others.push([distance, other])
    }
    others.sort((a, b) => a[0] - b[0] || a[1] - b[1])
    return others.map(([, other]) => other)
  })
}

const indexNeighbors = new Map<number, number[][]>()

/** 沒有場地配置時依編號。 / Without a layout: by index. */
function neighborsByIndex(count: number): FixtureNeighbors {
  let table = indexNeighbors.get(count)
  if (!table) {
    table = fixtureNeighbors(Array.from({ length: count }, () => null))
    indexNeighbors.set(count, table)
  }
  return table
}

export type WalkSettings = { walkers: number; choices: number; noReturn: number; roundSteps: number }

export function walkSettings(p: Readonly<Record<string, number>>, count: number): WalkSettings {
  return {
    walkers: Math.min(count, Math.max(1, Math.trunc(p.walkers))),
    choices: Math.max(1, Math.trunc(p.choices)),
    noReturn: Math.max(0, Math.trunc(p.noReturn)),
    roundSteps: Math.max(1, Math.trunc(p.roundSteps)),
  }
}

/**
 * 第 round 輪每條路徑走過的燈（各 roundSteps + 1 顆）。起點：雜湊最小的 walkers 顆（不重複）。
 * Each path's fixtures in round `round` (roundSteps + 1 each). Starts: the `walkers` fixtures with the smallest hash.
 */
export function walkPaths(round: number, neighbors: FixtureNeighbors, settings: WalkSettings): number[][] {
  const count = neighbors.length
  const order = Array.from({ length: count }, (_, index) => index)
  const keys = order.map((index) => hash01(round, index, START_SALT))
  order.sort((a, b) => keys[a] - keys[b] || a - b)
  return order.slice(0, settings.walkers).map((start, walker) => {
    const path = [start]
    for (let step = 0; step < settings.roundSteps; step += 1) {
      const current = path[step]
      const recent = path.slice(Math.max(0, step - settings.noReturn), step)
      let candidates = neighbors[current].filter((other) => !recent.includes(other))
      if (candidates.length === 0) candidates = [...neighbors[current]]
      candidates = candidates.slice(0, settings.choices)
      path.push(candidates[Math.floor(hash01(round, walker * WALKER_STRIDE + step, PICK_SALT) * candidates.length)])
    }
    return path
  })
}

// 每顆燈各問一次，路徑只算一次：記住最近幾組（串接轉場時有兩層）。 / Asked per fixture; keep the last few path sets.
const pathCache: { neighbors: FixtureNeighbors; key: string; paths: number[][] }[] = []

function cachedPaths(round: number, neighbors: FixtureNeighbors, settings: WalkSettings): number[][] {
  const key = `${round}:${settings.walkers}:${settings.choices}:${settings.noReturn}:${settings.roundSteps}`
  const hit = pathCache.find((entry) => entry.neighbors === neighbors && entry.key === key)
  if (hit) return hit.paths
  const paths = walkPaths(round, neighbors, settings)
  pathCache.unshift({ neighbors, key, paths })
  if (pathCache.length > 8) pathCache.pop()
  return paths
}

function smoothstep(value: number): number {
  const t = Math.min(1, Math.max(0, value))
  return t * t * (3 - 2 * t)
}

/** 一輪 = 每步影格 × 幾步後重選起點。 / One round = step frames × steps per round. */
export function walkStepFrames(p: Readonly<Record<string, number>>, fps: number): number {
  return Math.max(1, Math.round(p.stepSeconds * fps))
}

/** 這顆燈在這一格的亮度。 / This fixture's brightness at this frame. */
export function brightnessWalk(
  p: Readonly<Record<string, number>>, frame: number, fps: number, fixtureIndex: number, fixtureCount: number,
  neighbors: FixtureNeighbors | undefined,
): number {
  if (fixtureCount <= 1) return 1
  const table = neighbors && neighbors.length === fixtureCount ? neighbors : neighborsByIndex(fixtureCount)
  const settings = walkSettings(p, fixtureCount)
  const stepFrames = walkStepFrames(p, fps)
  const roundFrames = stepFrames * settings.roundSteps
  const at = Math.max(0, frame)
  const round = Math.floor(at / roundFrames)
  const local = at - round * roundFrames
  const step = Math.floor(local / stepFrames)
  const linear = (local - step * stepFrames) / stepFrames
  // 流動曲線：0 等速，1 兩端放慢。 / Curve: 0 linear, 1 eased at both ends.
  const progress = p.curve === 1 ? smoothstep(linear) : linear
  let total = 0
  for (const path of cachedPaths(round, table, settings)) {
    if (path[step] === fixtureIndex) total += 1 - progress
    if (path[step + 1] === fixtureIndex) total += progress
  }
  return Math.min(1, total)
}
