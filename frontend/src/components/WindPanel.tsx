import { useState } from 'react'
import { getEffectManifest, type EffectPreset, type RgbColor } from '../effects'
import { getFixtureManifest, type FixturePreset } from '../fixtures'
import { WIND_FLOW_COLORS } from '../wind/windPreview'
import { GradientEditor } from './GradientEditor'
import { Slider } from './Slider'
import { WindPreviewPanel } from './WindPreviewPanel'

export type WindGroup = 'spinner' | 'ball' | 'wash'
export type WindPresets = { spinner: EffectPreset; ball: FixturePreset; wash: FixturePreset }

const GROUPS: readonly [WindGroup, string][] = [['spinner', '轉燈'], ['ball', '球燈'], ['wash', '洗牆燈']]

type WindPanelProps = {
  /** 待機中（全場播風流）。 / Standby: wind-flow on every group. */
  standby: boolean
  /** null：不能切換待機（舊後端沒有風流）。 / null: standby unavailable (an old backend without wind-flow). */
  onStandby: ((standby: boolean) => void) | null
  /** 後端控制：設定與測試風送到後端。 / Backend control: presets and the test wind go to the backend. */
  backend: boolean
  presets: WindPresets
  onPreset: <G extends WindGroup>(group: G, preset: WindPresets[G]) => void
}

/**
 * 「風力」分頁：待機切換、預覽風力，以及三個群組的風流（待機效果）設定。風流只在這裡設定，不在一般的效果清單。
 * The wind tab: standby toggle, preview wind and the wind-flow (standby effect) settings of the three groups.
 * wind-flow is only set up here, not in the ordinary effect lists.
 */
export function WindPanel({ standby, onStandby, backend, presets, onPreset }: WindPanelProps) {
  const [group, setGroup] = useState<WindGroup>('spinner')
  const preset = presets[group]
  const parameters = group === 'spinner' ? getEffectManifest('wind-flow').parameters : getFixtureManifest('wind-flow').parameters
  const colors: RgbColor[] = preset.color.mode === 'gradient' ? preset.color.colors : WIND_FLOW_COLORS
  const update = (next: Partial<EffectPreset> & Partial<FixturePreset>) => {
    onPreset(group, { ...preset, ...next } as WindPresets[typeof group])
  }
  const fixture = group === 'spinner' ? null : presets[group]
  return (
    <>
      <div className="color-controls">
        <span className="control-label">{backend ? '待機效果：風流（後端輸出）' : '待機效果：風流（模擬預覽）'}</span>
        {onStandby ? (
          <div className="segmented-control" aria-label="待機">
            <button type="button" className={standby ? 'active' : 'secondary'} aria-pressed={standby}
              onClick={() => onStandby(true)}>待機（全場播風流）</button>
            <button type="button" className={!standby ? 'active' : 'secondary'} aria-pressed={!standby}
              onClick={() => onStandby(false)}>原本的播放</button>
          </div>
        ) : (
          <p className="control-note">這個後端版本沒有風流（需要 rc.10 以上），不能切換待機；切到「模擬」可以預覽。</p>
        )}
        <p className="control-note">
          {backend
            ? '待機時後端全場改送風流，蓋過單一效果、串接、演出（進入待機會停止演出的音樂；開始演出會離開待機）。下面的設定會存到後端，待機中立即生效。後端重新啟動後不會自動進入待機。'
            : '待機時全場改播風流，不管單一效果、串接或演出。下面的設定隨時可以改，待機中會立即看到。這裡是模擬用的設定，和後端的分開。'}
        </p>
      </div>
      <WindPreviewPanel backend={backend} />
      <div className="segmented-control group-switch" aria-label="風流設定的群組">
        {GROUPS.map(([value, label]) => (
          <button key={value} type="button" className={group === value ? 'active' : 'secondary'}
            aria-pressed={group === value} onClick={() => setGroup(value)}>{label}</button>
        ))}
      </div>
      <p className="control-note">
        流速、倍率、無風門檻、最大風速、風速曲線、亮點門檻、掃過時間、間隔在各群組都相同時，亮帶和亮點帶會同步。
      </p>
      {parameters.map((parameter) => (
        <Slider key={`${group}:${parameter.key}`} label={parameter.label} value={preset.parameters[parameter.key]}
          min={parameter.min} max={parameter.max} step={parameter.step} unit={parameter.unit}
          onChange={(value) => update({ parameters: { ...preset.parameters, [parameter.key]: value } })} />
      ))}
      {fixture && (
        <Slider label="亮度上限" value={fixture.maxLevel ?? 1} min={0} max={1} step={0.05} unit=""
          onChange={(value) => {
            const { maxLevel: _omit, ...rest } = fixture
            onPreset(group, (value >= 1 ? rest : { ...rest, maxLevel: value }) as WindPresets[typeof group])
          }} />
      )}
      <div className="color-controls">
        <span className="control-label">顏色（左 = 無風 → 右 = 最大風速）</span>
        <GradientEditor colors={colors} onChange={(next) => update({
          color: group === 'spinner'
            ? { mode: 'gradient', axis: 'radius', colors: next }
            : { mode: 'gradient', axis: 'x', colors: next },
        } as Partial<EffectPreset> & Partial<FixturePreset>)} />
      </div>
    </>
  )
}
