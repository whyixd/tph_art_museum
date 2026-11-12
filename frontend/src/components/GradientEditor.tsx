import { hexToRgb, rgbToHex, MULTI_FADE_MAX_SECONDS, type MultiColorFields, type RgbColor } from '../effects'
import { Slider } from './Slider'

type ColorInputProps = {
  label: string
  color: RgbColor
  onChange: (color: RgbColor) => void
}

export function ColorInput({ label, color, onChange }: ColorInputProps) {
  return (
    <label className="color-input">
      <span>{label}</span>
      <input
        type="color"
        value={rgbToHex(color)}
        onChange={(event) => onChange(hexToRgb(event.target.value))}
      />
      <output>{rgbToHex(color).toUpperCase()}</output>
    </label>
  )
}

type GradientEditorProps = {
  colors: RgbColor[]
  onChange: (colors: RgbColor[]) => void
  /** 預覽畫成一格一色（多色模式），不是連續漸層。 / Preview as one block per colour (multi mode), not a blend. */
  blocks?: boolean
}

// 2–8 個色點的漸層編輯器，effect 顏色與天氣色盤共用。
// Gradient editor (2–8 stops) shared by effect colors and the weather palette.
export function GradientEditor({ colors, onChange, blocks = false }: GradientEditorProps) {
  const stops = blocks
    ? colors.flatMap((color, index) => [`${rgbToHex(color)} ${(index / colors.length) * 100}%`, `${rgbToHex(color)} ${((index + 1) / colors.length) * 100}%`])
    : colors.map(rgbToHex)
  return (
    <>
      <div
        className="gradient-preview"
        aria-hidden="true"
        style={{ background: `linear-gradient(to right, ${stops.join(', ')})` }}
      />
      <div className="gradient-stops">
        {colors.map((color, index) => (
          <div className="gradient-stop" key={index}>
            <ColorInput
              label={`色點 ${index + 1}`}
              color={color}
              onChange={(nextColor) => onChange(colors.map((current, currentIndex) => (
                currentIndex === index ? nextColor : current
              )))}
            />
            {colors.length > 2 && (
              <button
                type="button"
                className="secondary remove-stop"
                aria-label={`移除色點 ${index + 1}`}
                onClick={() => onChange(colors.filter((_, currentIndex) => currentIndex !== index))}
              >移除</button>
            )}
          </div>
        ))}
      </div>
      {colors.length < 8 && (
        <button
          type="button"
          className="secondary add-stop"
          onClick={() => onChange([...colors, { ...colors.at(-1)! }])}
        >新增色點</button>
      )}
    </>
  )
}

type MultiColorFieldsEditorProps = {
  value: MultiColorFields
  onChange: (fields: MultiColorFields) => void
}

// 多色模式的共用設定：顏色清單、照順序／隨機、換色間隔、淡入淡出。轉燈與球燈／洗牆燈共用。
// Shared multi-colour settings: the colour list, order, step and fade. Used by spinners and DMX fixtures.
export function MultiColorFieldsEditor({ value, onChange }: MultiColorFieldsEditorProps) {
  const { colors, order, stepSeconds, fadeSeconds } = value
  const set = (patch: Partial<MultiColorFields>) => onChange({ colors, order, stepSeconds, fadeSeconds, ...patch })
  return (
    <>
      <div className="segmented-control" aria-label="顯示方式">
        <button type="button" className={order === 'sequence' ? 'active' : 'secondary'}
          aria-pressed={order === 'sequence'} onClick={() => set({ order: 'sequence' })}>照順序</button>
        <button type="button" className={order === 'random' ? 'active' : 'secondary'}
          aria-pressed={order === 'random'} onClick={() => set({ order: 'random' })}>隨機</button>
      </div>
      <Slider label="換色間隔" value={stepSeconds} min={0} max={30} step={0.1} unit="s（0 = 固定不變）"
        onChange={(next) => set({ stepSeconds: next, fadeSeconds: Math.min(fadeSeconds, next) })} />
      <Slider label="淡入淡出" value={fadeSeconds} min={0} max={Math.min(MULTI_FADE_MAX_SECONDS, Math.max(0.1, stepSeconds))} step={0.1}
        unit="s（0 = 直接跳）" disabled={stepSeconds <= 0}
        onChange={(next) => set({ fadeSeconds: Math.min(next, stepSeconds) })} />
      <GradientEditor colors={colors} onChange={(next) => set({ colors: next })} blocks />
      <p className="control-note">
        {order === 'sequence'
          ? '第 1 點第 1 色、第 2 點第 2 色…循環；每過一個間隔整排往前推一格。'
          : '每點從清單隨機抽一色；每過一個間隔重新抽。'}
      </p>
    </>
  )
}
