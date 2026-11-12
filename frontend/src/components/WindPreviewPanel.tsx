import { useEffect, useReducer } from 'react'
import { Slider } from './Slider'
import { getWindPreview, getWindTarget, setWindTarget, subscribeWindPreview } from '../wind/windPreview'

const COMPASS = ['+x（沿長牆）', '', '+y（往後牆）', '', '−x', '', '−y', '']

/**
 * 風力效果原型的預覽風力（只在前端）：目標風速、風向、平滑、擾動；顯示平滑後的實際值。
 * Preview wind for the wind-effect prototype (frontend only): target speed, direction, smoothing and gustiness, with
 * the smoothed values the effect is actually using.
 */
export function WindPreviewPanel({ backend = false }: { backend?: boolean }) {
  const [, refresh] = useReducer((value: number) => value + 1, 0)
  useEffect(() => subscribeWindPreview(refresh), [])
  // 平滑值會自己變，每 250 ms 更新顯示。 / The smoothed values drift on their own: refresh the readout.
  useEffect(() => {
    const timer = window.setInterval(refresh, 250)
    return () => window.clearInterval(timer)
  }, [])
  const target = getWindTarget()
  const live = getWindPreview()
  const arrow = live.flowDeg
  return (
    <div className="color-controls wind-preview">
      <span className="control-label">{backend ? '測試風（送到後端，風速計接上前的風力來源）' : '預覽風力（只在前端模擬）'}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <svg width="56" height="56" viewBox="-28 -28 56 56" aria-label={`風往 ${arrow.toFixed(0)}°`}>
          <circle r="26" fill="none" stroke="currentColor" strokeOpacity="0.3" />
          {/* 上視圖：+x 向右、+y 向上（同 3D 上視：場景 z = −y）。 / Plan view: +x right, +y up (as the 3D top view). */}
          <g transform={`rotate(${-arrow})`}>
            <line x1="-18" y1="0" x2="16" y2="0" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
            <path d="M 20 0 L 10 -7 L 10 7 Z" fill="currentColor" />
          </g>
        </svg>
        <div className="control-note" style={{ margin: 0 }}>
          目前 {live.speedMs.toFixed(1)} m/s（顏色用 {live.colorSpeedMs.toFixed(1)}）・風往 {live.flowDeg.toFixed(0)}°
          {target.gustiness > 0 && <><br />瞬間 {live.instantMs.toFixed(1)} m/s</>}
        </div>
      </div>
      <Slider label="風速" value={target.speedMs} min={0} max={20} step={0.5} unit="m/s"
        onChange={(speedMs) => setWindTarget({ speedMs })} />
      <Slider label="風往（場地方向）" value={target.flowDeg} min={0} max={355} step={5}
        unit={`°${COMPASS[Math.round(target.flowDeg / 45) % 8] ? ' ' + COMPASS[Math.round(target.flowDeg / 45) % 8] : ''}`}
        onChange={(flowDeg) => setWindTarget({ flowDeg })} />
      <Slider label="平滑時間" value={target.smoothingSeconds} min={0} max={30} step={1} unit="s（顏色 ×3；0 = 立即）"
        onChange={(smoothingSeconds) => setWindTarget({ smoothingSeconds })} />
      <Slider label="陣風擾動" value={target.gustiness} min={0} max={0.6} step={0.05} unit=""
        onChange={(gustiness) => setWindTarget({ gustiness })} />
      <p className="control-note">
        風向轉動限制每秒 3°（同規劃）；風速低於 0.3 m/s 時風向不動。{backend ? '這裡的設定會存到後端，待機的風流照這個風力播放。' : '這裡的風只用於本機模擬；後端的測試風要在「後端控制」模式設定。'}
      </p>
    </div>
  )
}
