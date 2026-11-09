/**
 * Generate read-only reference vectors for the DMX fixture effects (src/fixtures) into
 * backend/fixtures/fixtures_v1.json. Without --write the JSON is printed to stdout.
 *
 * 球燈／洗牆燈效果的前後端一致性向量。
 */
import { createHash } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import {
  FIXTURE_MANIFEST,
  createDefaultFixturePreset,
  fixtureGeometry,
  getFixtureCompletion,
  renderFixtureFrame,
  sampleFixture,
  type FixturePreset,
  type WeatherPalette,
} from '../src/fixtures'

// 球燈 1–37 的高度（cm，DMX＿線間距.pdf）；x 未知 → 依編號。
// Ball heights 1–37 (cm, from the DMX cable drawing); x unknown, so it falls back to the order.
const BALL_HEIGHTS = [
  340, 430, 675, 890, 370, 555, 745, 305, 530, 745, 245, 475, 705, 360, 540, 745, 245, 460, 660, 850,
  450, 650, 785, 360, 450, 750, 450, 650, 835, 450, 500, 650, 835, 360, 705, 1030, 450,
]
const geometries = {
  balls: fixtureGeometry(BALL_HEIGHTS.map((z) => ({ z }))),
  // 洗牆燈範例：完整 x/y/z。 / Wall washer example with full x/y/z.
  wash: fixtureGeometry([
    { x: 0, y: 0, z: 300 }, { x: 700, y: 0, z: 300 }, { x: 1400, y: 950, z: 450 },
    { x: 2100, y: 950, z: 300 }, { x: 2833, y: 0, z: 600 },
  ]),
  single: fixtureGeometry([{ z: 500 }]),
}

const gradient = [
  { r: 1, g: 0.1, b: 0 }, { r: 0.1, g: 0.9, b: 0.3 }, { r: 0, g: 0.2, b: 1 }, { r: 0.9, g: 0.8, b: 0.1 },
]
const presets: FixturePreset[] = FIXTURE_MANIFEST.flatMap(({ id }) => {
  const base = createDefaultFixturePreset(id)
  const variants: FixturePreset[] = [
    base,
    { ...base, color: { mode: 'solid', color: { r: 0.2, g: 0.75, b: 0.1 } }, maxLevel: 0.6 },
    ...(['height', 'x', 'y', 'index'] as const).map((axis): FixturePreset => ({
      ...base, color: { mode: 'gradient', axis, colors: gradient.slice(0, axis === 'index' ? 2 : 4) },
    })),
  ]
  // 非預設參數（含反向）。 / Non-default parameters, including the reverse direction.
  const alternate = Object.fromEntries(
    FIXTURE_MANIFEST.find((entry) => entry.id === id)!.parameters.map((item) => [
      item.key, item.key === 'direction' ? 1 : Math.min(item.max, item.min + item.step * 3),
    ]),
  )
  if (Object.keys(alternate).length) variants.push({ ...base, parameters: alternate })
  return variants
})

const toBytes = (colors: { r: number; g: number; b: number }[]) =>
  colors.flatMap((color) => [color.r, color.g, color.b].map((value) => Math.floor(Math.min(1, Math.max(0, value)) * 255 + 0.5)))

// 逐顆取樣：各 preset × 幾個影格 × 幾顆球燈，晴天與雨天。
// Single samples: each preset × a few frames × a few balls, dry and raining.
const sampleFrames = [0, 7, 133, 2651]
const sampleFixtures = [0, 18, 36]
const samples = presets.flatMap((preset, presetIndex) => sampleFrames.flatMap((frame) => sampleFixtures.flatMap((index) =>
  [false, true].map((raining) => {
    const [height, x, y] = geometries.balls[index]
    const context = {
      controllerFrame: frame, controllerFps: 40, fixtureIndex: index, fixtureCount: geometries.balls.length,
      normalizedHeight: height, normalizedX: x, normalizedY: y,
    }
    return { presetIndex, context, raining, expected: sampleFixture(preset, context, raining) }
  }))))

// 整組影格：每個 preset × 幾何 × fps × 天氣，連續影格的位元組取 sha256。
// Whole-group frames: preset × geometry × fps × weather, consecutive frames' bytes hashed.
const palette: WeatherPalette = { mode: 'gradient', colors: [{ r: 0.9, g: 0.4, b: 0.1 }, { r: 0.2, g: 0.3, b: 1 }, { r: 1, g: 1, b: 1 }] }
const weathers = [
  { name: 'clear', raining: false, palette: null, frameStep: 0 },
  { name: 'cloudy', raining: false, palette, frameStep: 6 },
  { name: 'rain', raining: true, palette, frameStep: 0 },
] as const
const groupFrames = presets.flatMap((preset, presetIndex) => (Object.keys(geometries) as (keyof typeof geometries)[]).flatMap((geometry) =>
  [40, 60].flatMap((fps) => weathers.map((weather) => {
    const cycleFrames = getFixtureCompletion(preset, fps, geometries[geometry].length)
    const bytes: number[] = []
    for (let frame = 0; frame < 240; frame += 1) {
      bytes.push(...toBytes(renderFixtureFrame(preset, geometries[geometry], frame, {
        fps, raining: weather.raining, palette: weather.palette, frameStep: weather.frameStep, cycleFrames,
      })))
    }
    return {
      presetIndex, geometry, fps, weather: weather.name, cycleFrames,
      sha256: createHash('sha256').update(Buffer.from(bytes)).digest('hex'),
    }
  }))))

const output = JSON.stringify({
  source: 'frontend/src/fixtures',
  schemaVersion: 1,
  controllerFps: 40,
  ballHeights: BALL_HEIGHTS,
  geometries,
  palette,
  weathers,
  presets,
  samples,
  groupFrames,
}, null, 2)

if (process.argv.includes('--write')) {
  writeFileSync(new URL('../../backend/fixtures/fixtures_v1.json', import.meta.url), `${output}\n`)
} else {
  console.log(output)
}
