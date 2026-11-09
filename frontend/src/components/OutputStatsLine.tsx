import { useEffect, useState } from 'react'
import { getOutputStats, type OutputStats } from '../backendApi'

// 後端輸出迴圈是否跟得上：每 5 秒讀一次最近 60 秒的跳格數與每格時間。
// Whether the backend output loop keeps up: reads the last 60 s of skipped frames and frame time every 5 s.
export function OutputStatsLine() {
  const [stats, setStats] = useState<OutputStats | null | undefined>(undefined)
  useEffect(() => {
    let active = true
    const refresh = () => { void getOutputStats().then((value) => { if (active) setStats(value) }, () => undefined) }
    refresh()
    const timer = window.setInterval(refresh, 5_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  if (!stats) return null
  if (!stats.active) return <p className="control-note">輸出統計：後端沒有在輸出（--api-only 或尚未開始）。</p>
  const load = stats.renderMs.max / stats.budgetMs
  return (
    <p className={stats.skippedFrames > 0 || load > 1 ? 'backend-error' : 'control-note'} role="status">
      輸出統計（{stats.partial ? '前 60 秒內' : `最近 ${Math.round(stats.windowSeconds)} 秒`}）：
      {stats.frames} 格、跳過 {stats.skippedFrames} 格；每格 平均 {stats.renderMs.avg.toFixed(1)} ms／最長 {stats.renderMs.max.toFixed(1)} ms
      （預算 {stats.budgetMs.toFixed(1)} ms）
    </p>
  )
}
