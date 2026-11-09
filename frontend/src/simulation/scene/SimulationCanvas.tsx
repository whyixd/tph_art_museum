import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { OrbitControls as ThreeOrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { SPINNER_RADIUS } from '../../site/spinnerGeometry'
import type { SiteLayout } from '../../site/layout'
import { SiteScene, siteBounds, type SceneView, type SiteSceneProps } from './SiteScene'

export type SimulationCanvasProps = SiteSceneProps

function CameraFraming({ view, layout }: { view: SceneView; layout: SiteLayout }) {
  const { camera, gl, size } = useThree()
  const controls = useMemo(() => new ThreeOrbitControls(camera, gl.domElement), [camera, gl.domElement])

  useEffect(() => {
    controls.enableDamping = true
    controls.dampingFactor = 0.075
    controls.minDistance = 0.5
    controls.maxDistance = 120
    return () => controls.dispose()
  }, [controls])

  useEffect(() => {
    if (!(camera instanceof THREE.PerspectiveCamera)) return
    const aspect = Math.max(size.width / Math.max(size.height, 1), 0.1)
    // 上視用窄視角、拉遠，接近正投影，燈桿不會被透視拉成放射線。
    // The top view uses a narrow field of view from far away (near orthographic), so tall poles don't fan out.
    camera.fov = view === 'top' ? 12 : 42
    const halfFov = THREE.MathUtils.degToRad(camera.fov) / 2
    if (view === 'single') {
      const distance = (SPINNER_RADIUS * 2.4) / Math.tan(halfFov) / Math.min(1, aspect)
      camera.position.set(distance * 0.55, distance * 0.3, distance * 0.8)
      controls.target.set(0, 0, 0)
    } else {
      // 依所有燈的範圍取景；燈比地面高、離鏡頭近，距離要加上最高點。
      // Frame every light; they sit above the floor, closer to the camera, so the top height is added.
      const bounds = siteBounds(layout)
      const cx = (bounds.minX + bounds.maxX) / 2
      const cz = (bounds.minZ + bounds.maxZ) / 2
      const halfWidth = (bounds.maxX - bounds.minX) / 2 + 1.5
      const halfDepth = (bounds.maxZ - bounds.minZ) / 2 + 1.5
      const tanV = Math.tan(halfFov)
      if (view === 'top') {
        const distance = bounds.maxY + Math.max(halfWidth / (tanV * aspect), halfDepth / tanV)
        camera.position.set(cx, distance, cz + 0.01)
        controls.target.set(cx, 0, cz)
      } else {
        const cy = (bounds.minY + bounds.maxY) / 2
        const distance = halfWidth / (tanV * aspect)
        camera.position.set(cx, cy + distance * 0.3, bounds.maxZ + distance * 0.9)
        controls.target.set(cx, cy * 0.85, cz)
      }
    }
    // 上視時鏡頭很遠，近平面放大避免地板格線深度閃爍。 / Far top camera: a larger near plane keeps the floor grid from z-fighting.
    camera.near = view === 'top' ? 2 : 0.05
    camera.far = 400
    camera.updateProjectionMatrix()
    controls.update()
  }, [camera, controls, view, layout, size.height, size.width])

  useFrame(() => controls.update())
  return null
}

export function SimulationCanvas(props: SimulationCanvasProps) {
  return (
    <Canvas
      aria-label="美術館 3D 模擬畫面：轉燈、球燈與洗牆燈"
      camera={{ position: [5.8, 3.8, 6.4], fov: 42, near: 0.05, far: 400 }}
      dpr={[1, 1.75]}
      gl={{ antialias: true, alpha: false, powerPreference: 'high-performance', toneMapping: THREE.ACESFilmicToneMapping }}
      onCreated={({ gl }) => {
        gl.setClearColor('#05070b')
        gl.outputColorSpace = THREE.SRGBColorSpace
      }}
      onPointerMissed={() => props.onSelectFixture(null)}
    >
      <CameraFraming view={props.view} layout={props.layout} />
      <fog attach="fog" args={['#05070b', 60, 200]} />
      <hemisphereLight args={['#b9d8ff', '#17120e', 1.25]} />
      <directionalLight position={[10, 14, 8]} intensity={2.4} color="#e8f4ff" />
      <directionalLight position={[-6, 5, -8]} intensity={1.0} color="#547dff" />
      <SiteScene {...props} />
    </Canvas>
  )
}
