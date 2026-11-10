/**
 * 場景配置：24 台轉燈與 DMX 燈具（球燈、洗牆燈）的位置。
 * Site layout: positions of the spinners and the DMX fixtures (balls, wall washers).
 *
 * Layout coordinates are centimetres (backend `layout.json`): origin at the inner corner of the left wall and the
 * front wall, x along the long wall, y towards the back wall, z above the floor (spinner z = rotor centre).
 * The scene uses metres with Y up: X = x / 100, Y = z / 100, Z = -y / 100, so a camera in front (+Z) looks at the
 * back wall.
 */
import exampleLayout from '../../../backend/layout.example.json'
import type { FixturePosition } from '../fixtures'

export type LayoutPosition = FixturePosition

export type LayoutDocument = {
  schemaVersion: 1
  spinners?: Record<string, LayoutPosition>
  fixtures?: { ball?: Record<string, LayoutPosition>; wash?: Record<string, LayoutPosition> }
  /** 燈桿：x、y 與頂端高度 z（cm）。 / Poles: x, y and top height z (cm). */
  poles?: LayoutPosition[]
}

export type ScenePoint = readonly [x: number, y: number, z: number]

export type SiteFixture = { id: number; position: LayoutPosition | null }

export type SiteLayout = {
  /** Spinner positions by unit (index = unit - 1); null where the layout has none. */
  spinners: (LayoutPosition | null)[]
  ball: SiteFixture[]
  wash: SiteFixture[]
  poles: LayoutPosition[]
}

/** 牆面：長牆內側間距（cm）。 / Inner distance between the long walls (cm), as drawn. */
export const ROOM_DEPTH_CM = 953

export const EXAMPLE_LAYOUT = exampleLayout as LayoutDocument

function byId(entries: Record<string, LayoutPosition> | undefined, count: number): SiteFixture[] {
  return Array.from({ length: count }, (_, index) => ({ id: index + 1, position: entries?.[String(index + 1)] ?? null }))
}

/** The layout for `unitCount` spinners and the given fixture counts (fixture ids 1..count). */
export function siteLayout(
  document: LayoutDocument,
  unitCount: number,
  counts: { ball: number; wash: number },
): SiteLayout {
  return {
    spinners: Array.from({ length: unitCount }, (_, index) => document.spinners?.[String(index + 1)] ?? null),
    ball: byId(document.fixtures?.ball, counts.ball),
    wash: byId(document.fixtures?.wash, counts.wash),
    poles: (document.poles ?? []).filter((pole) => pole.x !== undefined && pole.y !== undefined && pole.z !== undefined),
  }
}

/** Example layout as shipped (24 spinners, 37 balls), for previews without a backend. */
export function exampleSiteLayout(): SiteLayout {
  const balls = Object.keys(EXAMPLE_LAYOUT.fixtures?.ball ?? {}).length
  const wash = Object.keys(EXAMPLE_LAYOUT.fixtures?.wash ?? {}).length
  return siteLayout(EXAMPLE_LAYOUT, Object.keys(EXAMPLE_LAYOUT.spinners ?? {}).length, { ball: balls, wash })
}

export function toScene(position: LayoutPosition): ScenePoint {
  return [(position.x ?? 0) / 100, (position.z ?? 0) / 100, -(position.y ?? 0) / 100]
}

/**
 * Scene positions for every item; ones without a layout position are lined up along the front wall
 * (spaced 1.5 m, at `fallbackHeight` cm) so they stay visible and clickable.
 */
export function scenePositions(items: (LayoutPosition | null)[], fallbackHeight: number): ScenePoint[] {
  return items.map((position, index) => (
    position && position.x !== undefined && position.y !== undefined
      ? toScene({ z: fallbackHeight, ...position })
      : toScene({ x: 150 * (index + 1), y: -150, z: position?.z ?? fallbackHeight })
  ))
}

export type SceneBounds = { minX: number; maxX: number; minY: number; maxY: number; minZ: number; maxZ: number }

export function sceneBounds(points: readonly ScenePoint[]): SceneBounds {
  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  const zs = points.map((point) => point[2])
  return points.length === 0
    ? { minX: 0, maxX: 1, minY: 0, maxY: 1, minZ: -1, maxZ: 0 }
    : { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys),
      minZ: Math.min(...zs), maxZ: Math.max(...zs) }
}

/** 懸臂只連到平面距離這麼近的燈桿（cm）。 / Arms only reach poles this close in plan (cm). */
export const ARM_REACH_CM = 120

export type PoleArm = { from: ScenePoint; to: ScenePoint }
/** 燈具在第幾根柱子（0 起）、該柱子上第幾顆（由低到高，0 起）。 / Pole rank and order on it (low to high). */
export type FixturePole = { rank: number; step: number }

/**
 * 依柱子的順序（效果 pillar-fill 用；與後端 config.pillar_ranks 相同）：每台轉燈歸屬平面上最近、距離在 ARM_REACH_CM
 * 內的柱子，依（柱子在配置中的順序、高度 z、燈台編號）排序；沒有柱子或位置的轉燈排在最後（依編號）。回傳每台的名次。
 * Order by pillar (pillar-fill; same as backend config.pillar_ranks): each spinner belongs to the nearest pole in plan
 * within ARM_REACH_CM; sorted by (pole order in the layout, height z, unit); spinners without a pole or position come
 * last, by unit. Returns each unit's rank.
 */
/** 平面上最近、距離在 ARM_REACH_CM 內的柱子編號；沒有時 Infinity。 / Nearest pole in plan within ARM_REACH_CM, else Infinity. */
function nearestPole(position: LayoutPosition | null, poles: readonly LayoutPosition[]): number {
  let pole = Infinity
  let bestDistance = ARM_REACH_CM
  if (position?.x !== undefined && position.y !== undefined) {
    poles.forEach((candidate, index) => {
      const distance = Math.hypot(candidate.x! - position.x!, candidate.y! - position.y!)
      if (distance <= bestDistance) {
        pole = index
        bestDistance = distance
      }
    })
  }
  return pole
}

export function pillarRanks(spinners: readonly (LayoutPosition | null)[], poles: readonly LayoutPosition[]): number[] {
  const keys = spinners.map((position, unit) => {
    const pole = nearestPole(position, poles)
    return { unit, pole, z: pole === Infinity ? 0 : position?.z ?? 0 }
  })
  const sorted = [...keys].sort((a, b) => a.pole - b.pole || a.z - b.z || a.unit - b.unit)
  const ranks = new Array<number>(spinners.length)
  sorted.forEach((key, rank) => { ranks[key.unit] = rank })
  return ranks
}

/**
 * A horizontal arm from the nearest pole (in plan, within ARM_REACH_CM) to each placed light, at the light's
 * attachment height: spinners hang from their top (centre + `spinnerRadiusCm`), fixtures from their centre.
 */
/**
 * 球燈／洗牆燈的柱子（球燈 pole-rise 用；與後端 config.fixture_poles 相同）：每顆歸屬平面上最近、ARM_REACH_CM 內的柱子；
 * 有燈具的柱子依配置順序編為第 0、1、2… 根，沒有柱子的燈具各自成一根排在最後（依編號）。step 是在該柱子上由低到高的順序。
 * Poles for DMX fixtures (pole-rise; same as backend config.fixture_poles): each fixture belongs to the nearest pole
 * in plan within ARM_REACH_CM; poles holding fixtures are numbered 0, 1, 2… in layout order, and fixtures without a
 * pole each form their own after them (by index). step is the order on that pole, low to high.
 */
export function fixturePoles(fixtures: readonly (LayoutPosition | null)[], poles: readonly LayoutPosition[]): FixturePole[] {
  const owners = fixtures.map((position) => nearestPole(position, poles))
  const used = [...new Set(owners.filter((pole) => pole !== Infinity))].sort((a, b) => a - b)
  const loose = owners.map((pole, index) => (pole === Infinity ? index : -1)).filter((index) => index >= 0)
  const result = new Array<FixturePole>(fixtures.length)
  used.forEach((pole, rank) => {
    owners.map((owner, index) => ({ owner, index, z: fixtures[index]?.z ?? 0 }))
      .filter((entry) => entry.owner === pole)
      .sort((a, b) => a.z - b.z || a.index - b.index)
      .forEach((entry, step) => { result[entry.index] = { rank, step } })
  })
  loose.forEach((index, order) => { result[index] = { rank: used.length + order, step: 0 } })
  return result
}

export function poleArms(layout: SiteLayout, spinnerRadiusCm: number): PoleArm[] {
  const lights: LayoutPosition[] = [
    ...layout.spinners.filter((position): position is LayoutPosition => position !== null && position.z !== undefined)
      .map((position) => ({ ...position, z: position.z! + spinnerRadiusCm })),
    ...[...layout.ball, ...layout.wash].map((fixture) => fixture.position)
      .filter((position): position is LayoutPosition => position !== null && position.z !== undefined),
  ]
  const arms: PoleArm[] = []
  for (const light of lights) {
    if (light.x === undefined || light.y === undefined) continue
    let best: LayoutPosition | null = null
    let bestDistance = ARM_REACH_CM
    for (const pole of layout.poles) {
      const distance = Math.hypot(pole.x! - light.x, pole.y! - light.y)
      if (distance <= bestDistance) {
        best = pole
        bestDistance = distance
      }
    }
    if (best && bestDistance > 1) {
      arms.push({ from: toScene({ x: best.x, y: best.y, z: light.z }), to: toScene(light) })
    }
  }
  return arms
}
