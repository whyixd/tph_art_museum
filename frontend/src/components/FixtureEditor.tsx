import {
  FIXTURE_MANIFEST,
  createDefaultFixturePreset,
  getFixtureManifest,
  type FixtureAxis,
  type FixtureColor,
  type FixtureEffectId,
  type FixturePreset,
} from '../fixtures'
import { ColorInput, GradientEditor } from './GradientEditor'
import { Slider } from './Slider'

export type FixtureGroup = 'ball' | 'wash'

export const GROUP_LABELS: Record<'spinner' | FixtureGroup, string> = { spinner: '轉燈', ball: '球燈', wash: '洗牆燈' }

const AXIS_LABELS: ReadonlyArray<[FixtureAxis, string]> = [
  ['height', '依高度（低 → 高）'],
  ['x', '依 x（左 → 右）'],
  ['y', '依 y（前 → 後）'],
  ['index', '依編號（DMX 鏈順序）'],
]

type FixtureEditorProps = {
  group: FixtureGroup
  tab: 'effect' | 'color'
  /** null = dark (no preset; a sequence step leaves the group out). */
  preset: FixturePreset | null
  count: number
  weatherEnabled: boolean
  onChange: (preset: FixturePreset | null) => void
}

// 球燈／洗牆燈的效果與色彩編輯；「關閉」即沒有 preset（串接該段省略此群組）。
// Ball / wash effect and colour editing; "off" means no preset (the sequence step leaves the group out).
export function FixtureEditor({ group, tab, preset, count, weatherEnabled, onChange }: FixtureEditorProps) {
  const label = GROUP_LABELS[group]
  if (tab === 'effect') {
    const effect = preset ? getFixtureManifest(preset.effectId) : null
    return (
      <>
        <label className="control-field effect-picker">
          <span>{label}效果</span>
          <select value={preset?.effectId ?? 'off'} onChange={(event) => {
            const id = event.target.value as FixtureEffectId
            // 風流的漸層代表風速，換成風流時用它的預設漸層。 / wind-flow's gradient is wind speed: switch to its default.
            onChange(id === 'off' ? null : { ...createDefaultFixturePreset(id),
              color: (id !== 'wind-flow' && preset?.color) || createDefaultFixturePreset(id).color,
              ...(preset?.maxLevel !== undefined ? { maxLevel: preset.maxLevel } : {}) })
          }}>
            {FIXTURE_MANIFEST.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.id === 'off' ? '關閉（全暗）' : entry.label}</option>
            ))}
          </select>
        </label>
        {count === 0 && <p className="control-note">後端設定沒有{label}（patch 為空）；仍可編輯，但不會輸出。</p>}
        {preset && effect && (
          <>
            {effect.parameters.map((parameter) => (
              parameter.key === 'direction' ? (
                <div className="color-controls" key={parameter.key}>
                  <span className="control-label">方向</span>
                  <div className="segmented-control" aria-label="方向">
                    {[0, 1].map((value) => (
                      <button key={value} type="button" className={preset.parameters.direction === value ? 'active' : 'secondary'}
                        aria-pressed={preset.parameters.direction === value}
                        onClick={() => onChange({ ...preset, parameters: { ...preset.parameters, direction: value } })}>
                        {value === 0 ? '正向' : '反向'}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <Slider key={parameter.key} label={parameter.label} value={preset.parameters[parameter.key]}
                  min={parameter.min} max={parameter.max} step={parameter.step} unit={parameter.unit}
                  onChange={(value) => onChange({ ...preset, parameters: { ...preset.parameters, [parameter.key]: value } })} />
              )
            ))}
            <Slider label="亮度上限" value={preset.maxLevel ?? 1} min={0} max={1} step={0.05} unit=""
              onChange={(value) => {
                const { maxLevel: _omit, ...rest } = preset
                onChange(value >= 1 ? rest : { ...rest, maxLevel: value })
              }} />
            <p className="effect-description">{effect.description}</p>
          </>
        )}
        {!preset && <p className="control-note">{label}全暗。選一個效果開始編輯。</p>}
      </>
    )
  }

  if (!preset) return <p className="control-note">{label}目前全暗；先在「效果」分頁選效果。</p>
  const color = preset.color
  const weatherFlag = color.weatherPalette === false ? { weatherPalette: false as const } : {}
  const setColor = (next: FixtureColor) => onChange({ ...preset, color: next })
  return (
    <div className="color-controls">
      {weatherEnabled && (
        <>
          <span className="control-label">顏色來源</span>
          <div className="segmented-control" aria-label="顏色來源">
            <button type="button" className={color.weatherPalette !== false ? 'active' : 'secondary'}
              aria-pressed={color.weatherPalette !== false}
              onClick={() => { const { weatherPalette: _omit, ...rest } = color; setColor(rest as FixtureColor) }}>天氣色盤</button>
            <button type="button" className={color.weatherPalette === false ? 'active' : 'secondary'}
              aria-pressed={color.weatherPalette === false}
              onClick={() => setColor({ ...color, weatherPalette: false })}>自訂顏色</button>
          </div>
          {color.weatherPalette !== false && (
            <p className="control-note">有天氣資料時用天氣色盤（純色會改依高度分佈，漸層保留下方的方向）；沒有時用下方顏色。</p>
          )}
        </>
      )}
      <div className="segmented-control" aria-label="顏色模式">
        <button type="button" className={color.mode === 'solid' ? 'active' : 'secondary'} aria-pressed={color.mode === 'solid'}
          onClick={() => color.mode === 'gradient' && setColor({ mode: 'solid', color: color.colors[0], ...weatherFlag })}>純色</button>
        <button type="button" className={color.mode === 'gradient' ? 'active' : 'secondary'} aria-pressed={color.mode === 'gradient'}
          onClick={() => color.mode === 'solid' && setColor({
            mode: 'gradient', axis: 'height', colors: [color.color, { r: 1, g: 1, b: 1 }], ...weatherFlag,
          })}>漸層</button>
      </div>
      {color.mode === 'solid' ? (
        <ColorInput label="顏色" color={color.color} onChange={(value) => setColor({ mode: 'solid', color: value, ...weatherFlag })} />
      ) : (
        <>
          <label className="control-field gradient-axis">
            <span>漸層方向</span>
            <select value={color.axis} onChange={(event) => setColor({ ...color, axis: event.target.value as FixtureAxis })}>
              {AXIS_LABELS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
            </select>
          </label>
          <GradientEditor colors={color.colors} onChange={(colors) => setColor({ ...color, colors })} />
        </>
      )}
    </div>
  )
}

export function fixtureLabel(preset: FixturePreset | null | undefined): string {
  return preset ? getFixtureManifest(preset.effectId).label : '全暗'
}
