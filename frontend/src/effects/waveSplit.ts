import { SPINNER_RADIUS } from '../site/spinnerGeometry'
import { EFFECT_SCHEMA_VERSION, clamp01, effectTimeSeconds, mixRgb, type EffectModule, type EffectSampleContext, type RgbColor } from './contract'

// 海浪分界：把全部轉燈當成一個空間，依每顆燈離地的高度分成上下兩色，分界是起伏的海面，海面上有一條浪花線。
// 轉燈繞垂直軸旋轉，燈的高度不隨旋轉改變，所以不需要馬達角度。
// Wave split: all spinners form one space; each LED takes the top or bottom colour by its height above the floor,
// split by a moving sea surface with a foam line. The rotor spins about the vertical axis, so an LED's height never
// changes with rotation and no motor angle is needed.

const DEFAULTS = {
  levelMeters: 6.2, amplitudeMeters: 2, wavelengthMeters: 10, speedMps: 1.5, directionDegrees: 0, chop: 0.4,
  foamCm: 6, softnessCm: 4,
}
const WHITE: RgbColor = { r: 1, g: 1, b: 1 }
/** 轉燈半徑（cm）。 / Rotor radius (cm). */
export const WAVE_SPINNER_RADIUS_CM = SPINNER_RADIUS * 100
/** 碎浪：主浪的 0.43 倍波長、偏 35°、1.3 倍速度。 / Chop: 0.43 × wavelength, 35° off, 1.3 × speed. */
const CHOP = { wavelength: 0.43, degrees: 35, speed: 1.3 }

const smoothstep = (value: number) => {
  const t = clamp01(value)
  return t * t * (3 - 2 * t)
}

/** 轉燈中心（cm）；配置缺的軸用預設：x = 150 × (編號 + 1)、y = 0、z = 500。 / Centre (cm); missing axes default. */
export function waveUnitCentre(context: EffectSampleContext): { x: number; y: number; z: number } {
  const position = context.unitPosition
  return {
    x: position?.x ?? 150 * (context.unitIndex + 1),
    y: position?.y ?? 0,
    z: position?.z ?? 500,
  }
}

/** 這顆燈離海面多高（cm，正值在海面上）。 / This LED's height above the sea surface (cm, positive above). */
export function heightAboveSurface(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): number {
  const p = { ...DEFAULTS, ...parameters }
  const centre = waveUnitCentre(context)
  // 角度從頂部起算：半徑 0 在頂部、1 在底部。 / Angle from the top: radius 0 is the top, 1 the bottom.
  const led = centre.z + WAVE_SPINNER_RADIUS_CM * Math.cos(Math.PI * context.normalizedRadius)
  const time = effectTimeSeconds(context)
  const wave = (wavelength: number, degrees: number, speed: number) => {
    const angle = (degrees * Math.PI) / 180
    const along = (centre.x * Math.cos(angle) + centre.y * Math.sin(angle)) / 100
    return Math.sin((2 * Math.PI * (along - speed * time)) / wavelength)
  }
  const main = wave(p.wavelengthMeters, p.directionDegrees, p.speedMps)
  const chop = wave(p.wavelengthMeters * CHOP.wavelength, p.directionDegrees + CHOP.degrees, p.speedMps * CHOP.speed)
  const surface = 100 * (p.levelMeters + p.amplitudeMeters * (main + p.chop * chop) / (1 + p.chop))
  return led - surface
}

/** (水面上的比例 0..1, 浪花強度 0..1)。 / (share above the water 0..1, foam strength 0..1). */
function split(context: EffectSampleContext, parameters: Readonly<Record<string, number>>): [number, number] {
  const above = heightAboveSurface(context, parameters)
  const softness = parameters.softnessCm ?? DEFAULTS.softnessCm
  const foamWidth = parameters.foamCm ?? DEFAULTS.foamCm
  const top = softness <= 0 ? (above >= 0 ? 1 : 0) : smoothstep(above / softness + 0.5)
  const foam = foamWidth <= 0 ? 0 : 1 - smoothstep(Math.abs(above) / foamWidth)
  return [top, foam]
}

export const waveSplit: EffectModule = {
  completion: {
    label: '主浪前進一個波長',
    frames: (parameters, fps) =>
      fps * (parameters.wavelengthMeters ?? DEFAULTS.wavelengthMeters) / (parameters.speedMps ?? DEFAULTS.speedMps),
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'wave-split',
    label: '海浪分界',
    description: '把所有轉燈當成同一個空間：依每顆燈離地的高度分成上下兩色，分界是一片起伏前進的海面（主浪加上斜向的碎浪），海面上有一條浪花線。轉燈旋轉時燈的高度不變，所以每顆光球上都是一條穩定的水平分界。漸層的第一色是海面上、最後一色是海面下、中間色是浪花（只有兩色時浪花為白色）；純色時只亮海面下與浪花。依場地配置的轉燈座標；九台共用同一片海面，不受天氣的多雲錯開影響。',
    parameters: [
      { key: 'levelMeters', label: '水位', unit: 'm（離地）', min: 0, max: 15, step: 0.1, defaultValue: DEFAULTS.levelMeters },
      { key: 'amplitudeMeters', label: '起伏高度', unit: 'm', min: 0, max: 6, step: 0.1, defaultValue: DEFAULTS.amplitudeMeters },
      { key: 'wavelengthMeters', label: '波長', unit: 'm', min: 2, max: 40, step: 0.5, defaultValue: DEFAULTS.wavelengthMeters },
      { key: 'speedMps', label: '波速', unit: 'm/s', min: 0.1, max: 10, step: 0.1, defaultValue: DEFAULTS.speedMps },
      { key: 'directionDegrees', label: '前進方向', unit: '°（0 = 沿長牆）', min: 0, max: 360, step: 5, defaultValue: DEFAULTS.directionDegrees },
      { key: 'chop', label: '碎浪', unit: '', min: 0, max: 1, step: 0.05, defaultValue: DEFAULTS.chop },
      { key: 'foamCm', label: '浪花寬度', unit: 'cm', min: 0, max: 40, step: 1, defaultValue: DEFAULTS.foamCm },
      { key: 'softnessCm', label: '分界柔和', unit: 'cm', min: 0, max: 40, step: 1, defaultValue: DEFAULTS.softnessCm },
    ],
  },
  ignoresWeatherTiming: true,

  // 純色：海面下亮、海面上暗，浪花全亮。 / Solid: lit below the surface, dark above, the foam fully lit.
  sample(context, parameters) {
    const [top, foam] = split(context, parameters)
    const value = Math.max(1 - top, foam)
    return { r: value, g: value, b: value }
  },
  gradientColor(context, parameters, colors) {
    const [top, foam] = split(context, parameters)
    const base = mixRgb(colors[colors.length - 1], colors[0], top)
    return mixRgb(base, colors.length >= 3 ? colors[1] : WHITE, foam)
  },
}
