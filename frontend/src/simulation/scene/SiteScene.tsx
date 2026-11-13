/**
 * 全場 3D：24 台轉燈（3 條整圈燈條、旋轉殘影）、球燈／洗牆燈（發光球）與房間。
 * Site 3D: spinners (three full-circle strips with rotation trails), ball / wall-washer fixtures and the room.
 *
 * Timing matches the backend: the controller frame drives the effects (sample-and-hold), the rotor angle runs
 * continuously; sequence layers, transitions (unit-fade-black per spinner / per fixture in id order), weather
 * palette, cloudy offsets and rain apply to every group.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useFrame, type ThreeEvent } from '@react-three/fiber'
import * as THREE from 'three'
import type { WeatherState } from '../../backendApi'
import {
  createEffectContext,
  ignoresWeatherTiming,
  presetForUnit,
  sampleEffect,
  withWeatherPalette,
  getEffectCompletion,
  type EffectPreset,
  type RgbColor,
} from '../../effects'
import {
  fixtureGeometry,
  renderFixtureFrame,
  type FixturePreset,
  type WeatherPalette,
} from '../../fixtures'
import { fixturePoles, pillarRanks, poleArms, ROOM_DEPTH_CM, sceneBounds, scenePositions, toScene, type LayoutPosition, type SceneBounds, type ScenePoint, type SiteLayout } from '../../site/layout'
import { buildLoopLeds, SPINNER_RADIUS, STRIP_COUNT } from '../../site/spinnerGeometry'
import {
  angleAtTime,
  buildTemporalSamples,
  controllerFrameAtTime,
  normalizedAngle,
  simulatedUnknownPhase,
  type SimulationSettings,
} from '../model'
import { layerPresetForUnit, layerUnitWeight, resolveSequenceFrame, unitFadeWeight, type CompiledSequence, type SequenceFrame, type SequenceLayer } from '../sequence'
import { frontFacingBrightness } from './facing'

export type SceneView = 'single' | 'site' | 'top'
export type FixtureGroup = 'ball' | 'wash'
export type GroupVisibility = { spinner: boolean; ball: boolean; wash: boolean }
export type FixtureSelection = { group: FixtureGroup; id: number } | null
export type FixturePresets = { ball: FixturePreset | null; wash: FixturePreset | null }
export type FixtureTestColors = Partial<Record<FixtureGroup, (RgbColor | null)[]>>

export type Diagnostics = {
  angleDegrees: number
  controllerFrame: number
  globalFrame: number
  cyclePhase: number
  simulationTime: number
}

export type SiteSceneProps = {
  settings: SimulationSettings
  weather: WeatherState | null
  effectPreset: EffectPreset
  fixturePresets: FixturePresets
  sequence: CompiledSequence | null
  sequenceRevision: number
  paused: boolean
  resetToken: number
  view: SceneView
  layout: SiteLayout
  pixelRotation: number
  visible: GroupVisibility
  selectedUnit: number
  /** 改為標示這幾台（燈台索引，0 起；例如正在編輯的轉燈覆寫）。 / Mark these units instead (indices), e.g. an edited override. */
  markedUnits?: readonly number[] | null
  selectedFixture: FixtureSelection
  showStructure: boolean
  /** 在轉燈、球燈旁顯示編號。 / Show the unit and ball numbers next to them. */
  showLabels?: boolean
  onSelectUnit: (unitIndex: number) => void
  onSelectFixture: (selection: FixtureSelection) => void
  onDiagnostics: (diagnostics: Diagnostics) => void
  /** Live output frame to follow (fractional), or null to run the local clock. */
  syncFrame?: (() => number | null) | null
  /** 與 syncFrame 差超過幾格才對齊（預設 2）；0 = 每格直接採用（跟隨音樂）。 / Frames of drift before snapping; 0 = always (music). */
  syncTolerance?: number
  startFrame?: number
  /** Backend colour tests: one colour or null per spinner / fixture. */
  unitColors?: (RgbColor | null)[] | null
  fixtureColors?: FixtureTestColors | null
  /** 後端的總亮度 0..1（鏡像時跟著變暗）；預設 1。 / Backend master level 0..1 for the mirror; default 1. */
  masterLevel?: number
}

const BALL_RADIUS = 0.2
const SPINNER_HEIGHT_CM = 600
const FIXTURE_HEIGHT_CM = 400
const LED_RADIUS = 0.012
const GLOW_RADIUS = 0.024
/** 殘影每條燈條取幾分之一的燈點。 / Trail uses every n-th LED of each strip. */
const TRAIL_STRIDE = 3
const DARK_BALL = new THREE.Color(0.05, 0.055, 0.065)
const BLACK_COLOR = new THREE.Color(0, 0, 0)
/** 洗牆燈在牆面的受光區（m）：寬、高。 / Wall-washer light on the wall (m): width and height. */
const WASH_WIDTH = 1.6
const WASH_HEIGHT = 4
/** 受光區離牆面的距離，避免與牆重疊閃爍（m）。 / Offset from the wall plane, against z-fighting (m). */
const WASH_WALL_OFFSET = 0.02

let washAlphaMap: THREE.Texture | null = null
/**
 * 受光區的透明度：底部最亮、往上漸淡，左右兩側淡出（alphaMap 讀綠色通道）。
 * Wash alpha: brightest at the bottom, fading upwards and to both sides (alphaMap reads green).
 */
function washAlpha(): THREE.Texture {
  if (washAlphaMap) return washAlphaMap
  const width = 64
  const height = 128
  const data = new Uint8Array(width * height * 4)
  for (let row = 0; row < height; row += 1) {
    const up = (row + 0.5) / height  // DataTexture row 0 = bottom (v = 0)
    for (let column = 0; column < width; column += 1) {
      const across = Math.sin(Math.PI * (column + 0.5) / width)
      const value = Math.round(255 * across * across * (1 - up) ** 1.6)
      data.set([value, value, value, 255], (row * width + column) * 4)
    }
  }
  washAlphaMap = new THREE.DataTexture(data, width, height)
  washAlphaMap.needsUpdate = true
  return washAlphaMap
}

/** 受光區打在最近的長牆（前牆 y = 0 或後牆 y = ROOM_DEPTH_CM）。 / The wash lands on the nearer long wall. */
export function washWallPoint(point: ScenePoint): ScenePoint {
  const depth = ROOM_DEPTH_CM / 100
  const wallZ = -point[2] > depth / 2 ? -depth + WASH_WALL_OFFSET : -WASH_WALL_OFFSET
  return [point[0], point[1] + WASH_HEIGHT / 2, wallZ]
}

function playbackAt(sequence: CompiledSequence | null, preset: EffectPreset, globalFrame: number): SequenceFrame {
  if (sequence) return resolveSequenceFrame(sequence, globalFrame)!
  return {
    stepIndex: 0,
    complete: false,
    layers: [{
      preset, stepIndex: 0, localFrame: globalFrame, elapsedFrame: globalFrame,
      cycleFrames: null, groupCycles: { spinner: null, ball: null, wash: null }, sourceFrame: globalFrame, weight: 1,
    }],
  }
}

// 與後端 render_frame 相同：依段落週期循環後加上各台錯開；各台參數不同時依該台自己的週期。
// Mirrors backend render_frame: wrapped per step cycle, then shifted by the unit offset; per-unit values use their own cycle.
function layerControllerFrame(layer: SequenceLayer, preset: EffectPreset, fps: number, unitCount: number, offset: number): number {
  const frame = layer.elapsedFrame
  const cycle = layer.cycleFrames === null || preset === layer.preset
    ? layer.cycleFrames
    : getEffectCompletion(preset, fps, unitCount)?.frames ?? layer.cycleFrames
  return (cycle === null ? frame : frame % cycle) + offset
}

function weatherPalette(weather: WeatherState | null): WeatherPalette | null {
  return weather?.category && weather.color?.mode === 'gradient' ? { mode: 'gradient', colors: weather.color.colors } : null
}

type UnitPlacement = { index: number; position: ScenePoint }

/** Spinner colours by effect pixel, cached per (unit, controller frame): effects only change at frame boundaries. */
function useSpinnerColors(props: SiteSceneProps, unitCount: number) {
  const { settings, weather, effectPreset, sequence, unitColors, layout } = props
  const ledCount = settings.ledCount
  const ranks = useMemo(() => pillarRanks(layout.spinners, layout.poles), [layout])
  // 水平流動用：轉燈在全部轉燈範圍內的平面位置（0–1）。 / Horizontal flow: each spinner's plan position (0–1).
  const flow = useMemo(() => fixtureGeometry(layout.spinners), [layout])
  const weatherPreset = useMemo(() => {
    const cache = new WeakMap<EffectPreset, EffectPreset>()
    return (preset: EffectPreset): EffectPreset => {
      if (!weather?.color || !weather.category || preset.color.weatherPalette === false) return preset
      const cached = cache.get(preset)
      if (cached) return cached
      const colored = withWeatherPalette(preset, weather.color)
      cache.set(preset, colored)
      return colored
    }
  }, [weather?.color, weather?.category])
  const cache = useMemo(() => new Map<string, Float32Array>(), [
    weatherPreset, effectPreset, sequence, ledCount, unitCount, unitColors, settings.controllerFps,
    settings.unitFrameOffsetStep, weather?.frameStep, ranks, flow,
  ])
  const frameStep = spinnerFrameStep(props)
  return (unit: number, globalFrame: number): Float32Array => {
    const key = `${unit}:${globalFrame}`
    const hit = cache.get(key)
    if (hit) return hit
    if (cache.size > unitCount * 48) cache.clear()
    const colors = new Float32Array(ledCount * 3)
    const test = unitColors?.[unit] ?? null
    if (test) {
      for (let led = 0; led < ledCount; led += 1) colors.set([test.r, test.g, test.b], led * 3)
    } else {
      const playback = playbackAt(sequence, effectPreset, globalFrame)
      const raining = weather?.category === 'rain'
      for (const layer of playback.layers) {
        const weight = layerUnitWeight(layer, unit, unitCount)
        if (weight <= 0) continue
        // 覆寫：這台在名單內就改用覆寫的效果。 / Overrides: a listed unit plays its override's effect.
        const ownPreset = layerPresetForUnit(layer, unit)
        const unitPreset = presetForUnit(ownPreset, unit, unitCount)
        const preset = weatherPreset(unitPreset)
        // 共用時間表的效果不套用各台錯開。 / Shared-schedule effects skip unit offsets.
        const offset = ignoresWeatherTiming(ownPreset.effectId) ? 0 : unit * frameStep
        const frame = layerControllerFrame(layer, unitPreset, settings.controllerFps, unitCount, offset)
        for (let led = 0; led < ledCount; led += 1) {
          const context = createEffectContext(
            frame, settings.controllerFps, led, ledCount, unit, unitCount, layer.localFrame,
          )
          context.unitRank = ranks[unit]
          context.unitPosition = layout.spinners[unit] ?? undefined
          context.unitFlowPosition = flow[unit] ? { x: flow[unit][1], y: flow[unit][2] } : undefined
          const color = sampleEffect(preset, context, layer.elapsedFrame, raining)
          colors[led * 3] += color.r * weight
          colors[led * 3 + 1] += color.g * weight
          colors[led * 3 + 2] += color.b * weight
        }
      }
    }
    cache.set(key, colors)
    return colors
  }
}

function SpinnerUnit({
  placement, selected, view, showStructure, onSelect, onRotor, onLights, onGlow, ledCount,
}: {
  placement: UnitPlacement
  selected: boolean
  view: SceneView
  showStructure: boolean
  onSelect: () => void
  onRotor: (node: THREE.Group | null) => void
  onLights: (node: THREE.InstancedMesh | null) => void
  onGlow: (node: THREE.InstancedMesh | null) => void
  ledCount: number
}) {
  const leds = useMemo(() => buildLoopLeds(ledCount, 0), [ledCount])
  const lightRef = useRef<THREE.InstancedMesh | null>(null)
  const glowRef = useRef<THREE.InstancedMesh | null>(null)
  useEffect(() => {
    const matrix = new THREE.Matrix4()
    const black = new THREE.Color(0, 0, 0)
    for (const mesh of [lightRef.current, glowRef.current]) {
      if (!mesh) continue
      leds.forEach((led, index) => {
        matrix.makeTranslation(...led.position)
        mesh.setMatrixAt(index, matrix)
        mesh.setColorAt(index, black)
      })
      mesh.instanceMatrix.needsUpdate = true
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    }
  }, [leds])
  const ring = useMemo(() => new THREE.TorusGeometry(SPINNER_RADIUS, 0.006, 6, 96), [])
  useEffect(() => () => ring.dispose(), [ring])
  return (
    <group position={placement.position} onClick={(event) => { event.stopPropagation(); onSelect() }}>
      {/* 看不見的點選範圍（整顆光球大小）：燈環與燈點太細，很難點中。 / Invisible hit sphere: the rings and LEDs are too thin to click. */}
      {view !== 'single' && (
        <mesh>
          <sphereGeometry args={[SPINNER_RADIUS, 16, 12]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      )}
      {selected && view !== 'single' && (
        <mesh position={[0, SPINNER_RADIUS + 0.25, 0]} rotation-z={Math.PI}>
          <coneGeometry args={[0.1, 0.2, 3]} />
          <meshBasicMaterial color="#65d9f8" side={THREE.DoubleSide} />
        </mesh>
      )}
      <group ref={onRotor}>
        {showStructure && Array.from({ length: STRIP_COUNT }, (_, strip) => (
          <mesh key={strip} geometry={ring} rotation-y={-(strip * Math.PI) / STRIP_COUNT}>
            <meshStandardMaterial color="#6d7f90" metalness={0.5} roughness={0.4} />
          </mesh>
        ))}
        <instancedMesh ref={(node) => { lightRef.current = node; onLights(node) }} args={[undefined, undefined, leds.length]}>
          <sphereGeometry args={[LED_RADIUS, 6, 4]} />
          <meshBasicMaterial toneMapped={false} />
        </instancedMesh>
        <instancedMesh ref={(node) => { glowRef.current = node; onGlow(node) }} args={[undefined, undefined, leds.length]}>
          <sphereGeometry args={[GLOW_RADIUS, 6, 4]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.25} depthWrite={false}
            blending={THREE.AdditiveBlending} toneMapped={false} />
        </instancedMesh>
      </group>
      {showStructure && (
        <mesh position={[0, 0, 0]}>
          <cylinderGeometry args={[0.012, 0.012, SPINNER_RADIUS * 2.3, 8]} />
          <meshStandardMaterial color="#93a1ad" metalness={0.8} roughness={0.25} />
        </mesh>
      )}
    </group>
  )
}

type Clock = { current: number }

function phaseOf(unit: number, settings: SimulationSettings, unknownPhases: number[]): number {
  return (unit * settings.unitPhaseStepDegrees * Math.PI) / 180 + (unknownPhases[unit] ?? 0)
}

/** 場景共用的時鐘：暫停、重設、跟隨後端影格、診斷資訊。 / Shared scene clock: pause, reset, follow the backend, diagnostics. */
function ClockDriver(props: SiteSceneProps & { clock: Clock; unknownPhases: number[] }) {
  const { settings, clock, unknownPhases } = props
  const diagnosticAccumulator = useRef(0)
  useEffect(() => {
    // 加一點點避免浮點誤差讓 floor 落到前一格。 / A hair over so flooring never lands on the previous frame.
    clock.current = props.startFrame ? (props.startFrame + 1e-6) / settings.controllerFps : 0
  }, [props.resetToken, props.sequenceRevision])
  useFrame((_, delta) => {
    if (!props.paused) clock.current += Math.min(delta, 0.1)
    // 跟隨後端時，差超過兩格就對齊後端影格。 / When following the backend, snap once off by more than two frames.
    const liveFrame = props.paused ? null : props.syncFrame?.() ?? null
    if (liveFrame !== null) {
      const liveTime = liveFrame / settings.controllerFps
      if (Math.abs(clock.current - liveTime) > (props.syncTolerance ?? 2) / settings.controllerFps) clock.current = liveTime
    }
    diagnosticAccumulator.current += delta
    if (diagnosticAccumulator.current < 0.1) return
    diagnosticAccumulator.current = 0
    const now = clock.current
    const unit = props.selectedUnit
    const offset = unit * spinnerFrameStep(props)
    const frame = controllerFrameAtTime(now, settings.controllerFps, offset / settings.controllerFps)
    props.onDiagnostics({
      angleDegrees: (normalizedAngle(angleAtTime(now, settings.rpm, phaseOf(unit, settings, unknownPhases))) * 180) / Math.PI,
      controllerFrame: frame,
      globalFrame: controllerFrameAtTime(now, settings.controllerFps),
      cyclePhase: frame % 20,
      simulationTime: now,
    })
  })
  return null
}

/** Frames between adjacent spinners: the backend's cloudy step, else the simulator knob (0 by default, as the backend). */
function spinnerFrameStep({ weather, settings }: Pick<SiteSceneProps, 'weather' | 'settings'>): number {
  if (weather?.category) return weather.category === 'cloudy' ? weather.frameStep : 0
  return settings.unitFrameOffsetStep
}

function Spinners(props: SiteSceneProps & { placements: UnitPlacement[]; unitCount: number; clock: Clock; unknownPhases: number[] }) {
  const { settings, placements, unitCount, view, pixelRotation, clock, unknownPhases } = props
  const master = props.masterLevel ?? 1
  const ledCount = settings.ledCount
  const rotorRefs = useRef<Array<THREE.Group | null>>([])
  const lightRefs = useRef<Array<THREE.InstancedMesh | null>>([])
  const glowRefs = useRef<Array<THREE.InstancedMesh | null>>([])
  const color = useMemo(() => new THREE.Color(), [])
  const worldNormal = useMemo(() => new THREE.Vector3(), [])
  const worldPosition = useMemo(() => new THREE.Vector3(), [])
  const leds = useMemo(() => buildLoopLeds(ledCount, pixelRotation), [ledCount, pixelRotation])
  const trailLeds = useMemo(() => leds.filter((led) => led.index % TRAIL_STRIDE === 0), [leds])
  const colorsFor = useSpinnerColors(props, unitCount)
  const samplesPerPoint = settings.temporalSamples
  const pointCount = trailLeds.length * samplesPerPoint * placements.length
  const geometry = useMemo(() => {
    const next = new THREE.BufferGeometry()
    next.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pointCount * 3), 3))
    next.setAttribute('color', new THREE.BufferAttribute(new Float32Array(pointCount * 3), 3))
    next.boundingSphere = new THREE.Sphere(new THREE.Vector3(18, 5, -5), 40)
    return next
  }, [pointCount])
  useEffect(() => () => geometry.dispose(), [geometry])

  useFrame(({ camera }) => {
    const now = clock.current
    const samples = buildTemporalSamples(now, settings.exposureMs, settings.persistenceMs, samplesPerPoint)
    const positions = geometry.attributes.position.array as Float32Array
    const colors = geometry.attributes.color.array as Float32Array
    const nowFrame = controllerFrameAtTime(now, settings.controllerFps)
    let cursor = 0
    for (const { index: unit, position: [ux, uy, uz] } of placements) {
      const phase = phaseOf(unit, settings, unknownPhases)
      for (const sample of samples) {
        const angle = angleAtTime(sample.timeSeconds, settings.rpm, phase)
        const cosine = Math.cos(angle)
        const sine = Math.sin(angle)
        const frameColors = colorsFor(unit, controllerFrameAtTime(sample.timeSeconds, settings.controllerFps))
        for (const led of trailLeds) {
          const [x, y, z] = led.position
          const px = ux + cosine * x + sine * z
          const py = uy + y
          const pz = uz - sine * x + cosine * z
          positions[cursor] = px
          positions[cursor + 1] = py
          positions[cursor + 2] = pz
          const [nx, ny, nz] = led.normal
          const facing = frontFacingBrightness(
            cosine * nx + sine * nz, ny, -sine * nx + cosine * nz,
            px, py, pz, camera.position.x, camera.position.y, camera.position.z,
          )
          const base = led.effectIndex * 3
          color.setRGB(frameColors[base], frameColors[base + 1], frameColors[base + 2], THREE.SRGBColorSpace)
          const energy = sample.weight * samples.length * 0.25 * facing * master
          colors[cursor] = color.r * energy
          colors[cursor + 1] = color.g * energy
          colors[cursor + 2] = color.b * energy
          cursor += 3
        }
      }
      const rotor = rotorRefs.current[unit]
      const lights = lightRefs.current[unit]
      const glow = glowRefs.current[unit]
      if (rotor) rotor.rotation.y = normalizedAngle(angleAtTime(now, settings.rpm, phase))
      if (rotor && lights && glow) {
        rotor.updateWorldMatrix(true, false)
        const frameColors = colorsFor(unit, nowFrame)
        leds.forEach((led, index) => {
          worldPosition.set(...led.position).applyMatrix4(rotor.matrixWorld)
          worldNormal.set(...led.normal).transformDirection(rotor.matrixWorld)
          const facing = frontFacingBrightness(
            worldNormal.x, worldNormal.y, worldNormal.z, worldPosition.x, worldPosition.y, worldPosition.z,
            camera.position.x, camera.position.y, camera.position.z,
          )
          const base = led.effectIndex * 3
          const level = facing * master
          color.setRGB(frameColors[base] * level, frameColors[base + 1] * level, frameColors[base + 2] * level,
            THREE.SRGBColorSpace)
          lights.setColorAt(index, color)
          glow.setColorAt(index, color)
        })
        if (lights.instanceColor) lights.instanceColor.needsUpdate = true
        if (glow.instanceColor) glow.instanceColor.needsUpdate = true
      }
    }
    geometry.attributes.position.needsUpdate = true
    geometry.attributes.color.needsUpdate = true
    geometry.setDrawRange(0, cursor / 3)
  })

  return (
    <>
      <points geometry={geometry}>
        <pointsMaterial vertexColors transparent opacity={0.45} depthWrite={false} blending={THREE.AdditiveBlending}
          size={view === 'single' ? 2.4 : 1.4} sizeAttenuation={false} toneMapped={false} />
      </points>
      {placements.map((placement) => (
        <SpinnerUnit key={placement.index} placement={placement} ledCount={ledCount} view={view}
          selected={props.markedUnits ? props.markedUnits.includes(placement.index) : placement.index === props.selectedUnit}
          showStructure={props.showStructure}
          onSelect={() => props.onSelectUnit(placement.index)}
          onRotor={(node) => { rotorRefs.current[placement.index] = node }}
          onLights={(node) => { lightRefs.current[placement.index] = node }}
          onGlow={(node) => { glowRefs.current[placement.index] = node }} />
      ))}
    </>
  )
}

/** Fixture colours for a frame: sequence layers (weights, per-fixture unit fade) or the single-effect presets. */
export function fixtureColorsAt(
  group: FixtureGroup,
  geometry: [number, number, number][],
  globalFrame: number,
  options: {
    sequence: CompiledSequence | null
    preset: FixturePreset | null
    fps: number
    weather: WeatherState | null
    /** 每顆的柱子（球燈 pole-rise 用）。 / Each fixture's pole (pole-rise). */
    poles?: readonly { rank: number; step: number }[] | null
  },
): RgbColor[] {
  const { sequence, preset, fps, weather, poles } = options
  const palette = weatherPalette(weather)
  const raining = weather?.category === 'rain'
  const frameStep = weather?.category === 'cloudy' ? weather.frameStep : 0
  const count = geometry.length
  const result = geometry.map(() => ({ r: 0, g: 0, b: 0 }))
  const layers = sequence
    ? resolveSequenceFrame(sequence, globalFrame)!.layers.map((layer) => ({
      preset: layer.fixtures?.[group] ?? null, frame: layer.elapsedFrame, cycle: layer.groupCycles[group],
      weight: layer.weight, unitFade: layer.unitFade,
    }))
    : [{ preset, frame: globalFrame, cycle: null, weight: 1, unitFade: undefined }]
  for (const layer of layers) {
    if (!layer.preset || layer.weight <= 0) continue
    const colors = renderFixtureFrame(layer.preset, geometry, layer.frame, {
      fps, raining, palette: layer.preset.color.weatherPalette === false ? null : palette, frameStep, cycleFrames: layer.cycle,
      poles,
    })
    colors.forEach((color, index) => {
      // 逐台淡出：依編號（DMX 鏈順序）逐顆淡出。 / unit-fade-black: fixtures fade in id (DMX chain) order.
      const weight = layer.weight * (layer.unitFade ? unitFadeWeight(layer.unitFade, index, count) : 1)
      result[index].r += color.r * weight
      result[index].g += color.g * weight
      result[index].b += color.b * weight
    })
  }
  return result
}

function Fixtures(props: SiteSceneProps & { group: FixtureGroup; points: ScenePoint[]; clock: Clock }) {
  const { group, points, layout, settings, sequence, weather, fixturePresets, fixtureColors, selectedFixture, clock } = props
  const master = props.masterLevel ?? 1
  const meshRef = useRef<THREE.InstancedMesh | null>(null)
  const glowRef = useRef<THREE.InstancedMesh | null>(null)
  const wallRef = useRef<THREE.InstancedMesh | null>(null)
  const color = useMemo(() => new THREE.Color(), [])
  const geometry = useMemo(() => fixtureGeometry(layout[group].map((fixture) => fixture.position)), [layout, group])
  const poles = useMemo(() => fixturePoles(layout[group].map((fixture) => fixture.position), layout.poles), [layout, group])
  useEffect(() => {
    const matrix = new THREE.Matrix4()
    for (const mesh of [meshRef.current, glowRef.current]) {
      if (!mesh) continue
      points.forEach((point, index) => {
        matrix.makeTranslation(...point)
        mesh.setMatrixAt(index, matrix)
        mesh.setColorAt(index, DARK_BALL)
      })
      mesh.instanceMatrix.needsUpdate = true
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    }
    const wall = wallRef.current
    if (wall) {
      points.forEach((point, index) => {
        wall.setMatrixAt(index, matrix.makeTranslation(...washWallPoint(point)))
        wall.setColorAt(index, BLACK_COLOR)
      })
      wall.instanceMatrix.needsUpdate = true
      if (wall.instanceColor) wall.instanceColor.needsUpdate = true
    }
  }, [points])
  const lastFrame = useRef(-1)
  useFrame(() => {
    const frame = controllerFrameAtTime(clock.current, settings.controllerFps)
    const mesh = meshRef.current
    const glow = glowRef.current
    if (!mesh || !glow || frame === lastFrame.current) return
    lastFrame.current = frame
    const test = fixtureColors?.[group]
    const colors = fixtureColorsAt(group, geometry, frame, {
      sequence, preset: fixturePresets[group], fps: settings.controllerFps, weather, poles,
    })
    colors.forEach((own, index) => {
      const value = test?.[index] ?? own
      color.setRGB(value.r * master, value.g * master, value.b * master, THREE.SRGBColorSpace)
      glow.setColorAt(index, color)
      wallRef.current?.setColorAt(index, color)
      // 暗的球仍看得到外殼。 / A dark ball still shows its shell.
      color.r = Math.max(color.r, DARK_BALL.r)
      color.g = Math.max(color.g, DARK_BALL.g)
      color.b = Math.max(color.b, DARK_BALL.b)
      mesh.setColorAt(index, color)
    })
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
    if (glow.instanceColor) glow.instanceColor.needsUpdate = true
    if (wallRef.current?.instanceColor) wallRef.current.instanceColor.needsUpdate = true
  })
  useEffect(() => { lastFrame.current = -1 }, [fixturePresets, sequence, weather, fixtureColors, geometry, master])
  const selectedIndex = selectedFixture?.group === group
    ? layout[group].findIndex((fixture) => fixture.id === selectedFixture.id) : -1
  const radius = group === 'ball' ? BALL_RADIUS : 0.15
  return (
    <>
      <instancedMesh ref={meshRef} args={[undefined, undefined, points.length]}
        onClick={(event: ThreeEvent<MouseEvent>) => {
          event.stopPropagation()
          if (event.instanceId !== undefined) props.onSelectFixture({ group, id: layout[group][event.instanceId].id })
        }}>
        {group === 'ball' ? <sphereGeometry args={[radius, 24, 16]} /> : <coneGeometry args={[radius, radius * 2, 16]} />}
        <meshBasicMaterial toneMapped={false} transparent opacity={0.92} />
      </instancedMesh>
      <instancedMesh ref={glowRef} args={[undefined, undefined, points.length]}>
        <sphereGeometry args={[radius * 1.8, 16, 12]} />
        <meshBasicMaterial transparent opacity={0.22} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </instancedMesh>
      {/* 洗牆燈打在牆上的光。 / The wall washers' light on the wall. */}
      {group === 'wash' && (
        <instancedMesh ref={wallRef} args={[undefined, undefined, points.length]} raycast={() => null}>
          <planeGeometry args={[WASH_WIDTH, WASH_HEIGHT]} />
          <meshBasicMaterial alphaMap={washAlpha()} transparent opacity={0.85} depthWrite={false}
            blending={THREE.AdditiveBlending} side={THREE.DoubleSide} toneMapped={false} />
        </instancedMesh>
      )}
      {selectedIndex >= 0 && (
        <mesh position={[points[selectedIndex][0], points[selectedIndex][1] + radius + 0.25, points[selectedIndex][2]]}
          rotation-z={Math.PI}>
          <coneGeometry args={[0.1, 0.2, 3]} />
          <meshBasicMaterial color="#ffd166" side={THREE.DoubleSide} />
        </mesh>
      )}
    </>
  )
}

/** 編號在螢幕上的高度（px），不隨距離縮放。 / Label height on screen (px), whatever the distance. */
const LABEL_PIXELS = 20
const LABEL_GAP = 0.2

/** 編號貼圖：深色圓角底、彩色字。 / A number label texture: dark rounded plate, coloured digits. */
function labelTexture(text: string, color: string): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = 128
  canvas.height = 64
  const context = canvas.getContext('2d')
  if (context) {
    context.fillStyle = 'rgba(8, 12, 18, 0.78)'
    context.beginPath()
    context.roundRect(4, 4, 120, 56, 14)
    context.fill()
    context.fillStyle = color
    context.font = '600 44px system-ui, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.fillText(text, 64, 34)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}

/**
 * 編號標籤：永遠面向鏡頭、不被燈具或牆擋住、不接收點選。放在燈具下方，避開上方的選取箭頭。
 * Number labels: always face the camera, drawn over lights and walls, never clickable. Below the light, clear of
 * the selection marker above it.
 */
function NumberLabels({ items, color }: { items: readonly { text: string; position: ScenePoint }[]; color: string }) {
  const key = items.map((item) => item.text).join(',')
  const textures = useMemo(() => items.map((item) => labelTexture(item.text, color)), [key, color])
  useEffect(() => () => textures.forEach((texture) => texture.dispose()), [textures])
  const group = useRef<THREE.Group | null>(null)
  // sizeAttenuation 關掉後大小只跟視角有關：每格依視角與畫面高度換算成固定的像素高度（全場、上視、單台都一樣大）。
  // Without size attenuation the size depends on the field of view only: rescale every frame to a fixed pixel height.
  useFrame(({ camera, size }) => {
    if (!group.current || !(camera instanceof THREE.PerspectiveCamera)) return
    const height = (LABEL_PIXELS / size.height) * 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)
    for (const sprite of group.current.children) sprite.scale.set(height * 2, height, 1)
  })
  return (
    <group ref={group}>
      {items.map((item, index) => (
        <sprite key={item.text} position={item.position} scale={[0, 0, 1]} renderOrder={10} raycast={() => null}>
          <spriteMaterial map={textures[index]} transparent depthTest={false} depthWrite={false} toneMapped={false}
            sizeAttenuation={false} />
        </sprite>
      ))}
    </group>
  )
}

/** 燈桿直徑約 20 cm（側視圖桿寬）。 / Poles are about 20 cm across (side-view width). */
const POLE_RADIUS = 0.1
const ARM_RADIUS = 0.025
const UP = new THREE.Vector3(0, 1, 0)

/** Poles from the floor to their top, and a horizontal arm from each pole to the lights it carries. */
function Poles({ layout }: { layout: SiteLayout }) {
  const arms = useMemo(() => poleArms(layout, SPINNER_RADIUS * 100).map(({ from, to }) => {
    const start = new THREE.Vector3(...from)
    const end = new THREE.Vector3(...to)
    const direction = end.clone().sub(start)
    return {
      position: start.clone().add(end).multiplyScalar(0.5),
      quaternion: new THREE.Quaternion().setFromUnitVectors(UP, direction.clone().normalize()),
      length: direction.length(),
    }
  }), [layout])
  return (
    <group>
      {layout.poles.map((pole, index) => {
        const [x, top, z] = toScene(pole)
        return (
          <mesh key={`pole-${index}`} position={[x, top / 2, z]}>
            <cylinderGeometry args={[POLE_RADIUS, POLE_RADIUS, top, 16]} />
            <meshStandardMaterial color="#5f6d7a" metalness={0.55} roughness={0.4} />
          </mesh>
        )
      })}
      {arms.map((arm, index) => (
        <mesh key={`arm-${index}`} position={arm.position} quaternion={arm.quaternion}>
          <cylinderGeometry args={[ARM_RADIUS, ARM_RADIUS, arm.length, 8]} />
          <meshStandardMaterial color="#7d8b98" metalness={0.6} roughness={0.35} />
        </mesh>
      ))}
    </group>
  )
}

function Room({ width }: { width: number }) {
  const depth = ROOM_DEPTH_CM / 100
  return (
    <group>
      <mesh position={[width / 2, -0.005, -depth / 2]} rotation-x={-Math.PI / 2} receiveShadow>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial color="#0b1016" metalness={0.1} roughness={0.9} />
      </mesh>
      {/* 1 m 格線，只鋪在房間內。 / 1 m grid, inside the room only. */}
      <gridHelper args={[width, Math.round(width), '#1f2c3a', '#141c26']}
        position={[width / 2, 0, -depth / 2]} scale={[1, 1, depth / width]} />
      {[0, -depth].map((z) => (
        <mesh key={z} position={[width / 2, 0.15, z]}>
          <boxGeometry args={[width, 0.3, 0.06]} />
          <meshStandardMaterial color="#2b3a4a" />
        </mesh>
      ))}
      <mesh position={[0, 0.15, -depth / 2]}>
        <boxGeometry args={[0.06, 0.3, depth]} />
        <meshStandardMaterial color="#2b3a4a" />
      </mesh>
    </group>
  )
}

export function SiteScene(props: SiteSceneProps) {
  const { layout, view, visible, selectedUnit } = props
  const unitCount = layout.spinners.length
  const spinnerPoints = useMemo(() => scenePositions(layout.spinners, SPINNER_HEIGHT_CM), [layout])
  const placements = useMemo<UnitPlacement[]>(() => (view === 'single'
    ? [{ index: Math.min(selectedUnit, unitCount - 1), position: [0, 0, 0] }]
    : spinnerPoints.map((position, index) => ({ index, position }))), [view, selectedUnit, unitCount, spinnerPoints])
  const ballPoints = useMemo(() => scenePositions(layout.ball.map((fixture) => fixture.position), FIXTURE_HEIGHT_CM), [layout])
  const washPoints = useMemo(() => scenePositions(layout.wash.map((fixture) => fixture.position), FIXTURE_HEIGHT_CM), [layout])
  const width = Math.max(10, ...spinnerPoints.map((point) => point[0] + 2), ...ballPoints.map((point) => point[0] + 2))
  const clock = useRef(0)
  const unknownPhases = useMemo(
    () => Array.from({ length: unitCount }, (_, index) => simulatedUnknownPhase(index, props.resetToken)),
    [unitCount, props.resetToken],
  )
  // 編號：轉燈是燈台編號（1 起），球燈是 DMX 編號。 / Labels: the spinner unit number (from 1), the ball's DMX id.
  const spinnerLabels = useMemo(() => placements.map(({ index, position }) => (
    { text: String(index + 1), position: [position[0], position[1] - SPINNER_RADIUS - LABEL_GAP, position[2]] as ScenePoint }
  )), [placements])
  const ballLabels = useMemo(() => ballPoints.map((point, index) => (
    { text: String(layout.ball[index].id), position: [point[0], point[1] - BALL_RADIUS - LABEL_GAP, point[2]] as ScenePoint }
  )), [ballPoints, layout])
  return (
    <>
      <ClockDriver {...props} clock={clock} unknownPhases={unknownPhases} />
      {visible.spinner && unitCount > 0 && (
        <Spinners {...props} placements={placements} unitCount={unitCount} clock={clock} unknownPhases={unknownPhases} />
      )}
      {view !== 'single' && visible.ball && ballPoints.length > 0 && <Fixtures {...props} group="ball" points={ballPoints} clock={clock} />}
      {view !== 'single' && visible.wash && washPoints.length > 0 && <Fixtures {...props} group="wash" points={washPoints} clock={clock} />}
      {props.showLabels && visible.spinner && <NumberLabels items={spinnerLabels} color="#65d9f8" />}
      {props.showLabels && view !== 'single' && visible.ball && <NumberLabels items={ballLabels} color="#ffd166" />}
      {view !== 'single' && props.showStructure && <Room width={width} />}
      {view !== 'single' && props.showStructure && <Poles layout={layout} />}
    </>
  )
}

/** Scene bounds of the placed spinners and fixtures (placeholders without a layout position are left out). */
export function siteBounds(layout: SiteLayout): SceneBounds {
  const placed = (items: (LayoutPosition | null)[], height: number) =>
    scenePositions(items, height).filter((_, index) => items[index]?.x !== undefined && items[index]?.y !== undefined)
  const points = [
    ...placed(layout.spinners, SPINNER_HEIGHT_CM),
    ...placed(layout.ball.map((fixture) => fixture.position), FIXTURE_HEIGHT_CM),
    ...placed(layout.wash.map((fixture) => fixture.position), FIXTURE_HEIGHT_CM),
  ]
  return sceneBounds(points.length ? points : scenePositions(layout.spinners, SPINNER_HEIGHT_CM))
}
