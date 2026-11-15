import { useEffect, useRef, useState } from 'react'
import {
  getAnemometerProbe, probeAnemometer,
  type AnemometerProbeInfo, type AnemometerProbeKind, type AnemometerProbeRequest, type AnemometerProbeResult,
} from '../backendApi'
import { suggestedTimeoutSeconds, summarizeProbes } from '../wind/probeStats'

const KIND_LABELS: Record<AnemometerProbeKind, string> = {
  ok: '成功',
  'no-port': '找不到序列埠',
  'open-failed': '序列埠打不開',
  'no-response': '沒有回應',
  incomplete: '回應不完整',
  'bad-frame': '回應無效',
  exception: '風速計回報錯誤',
}
const DIRECTIONS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
const INTERVAL_MS = 1_000

// 風速計測試讀取（現場用）：讀一次，或每秒讀一次、統計成功率與來回時間。只讀，不改風速計設定。
// Anemometer test read (on site): read once, or once a second with the success rate and round trips. Read only.
export function AnemometerProbePanel() {
  const [info, setInfo] = useState<AnemometerProbeInfo | null | undefined>(undefined)
  const [request, setRequest] = useState<AnemometerProbeRequest | null>(null)
  const [last, setLast] = useState<AnemometerProbeResult | null>(null)
  const [runs, setRuns] = useState<AnemometerProbeResult[]>([])
  const [target, setTarget] = useState(120)
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const stop = useRef(false)

  useEffect(() => {
    let active = true
    getAnemometerProbe().then((next) => {
      if (!active) return
      setInfo(next)
      if (next) setRequest(next.defaults)
    }, (failure) => { if (active) setError(failure instanceof Error ? failure.message : '無法讀取風速計設定') })
    return () => { active = false; stop.current = true }
  }, [])

  if (info === undefined && error === null) return null
  if (info === null) {
    return (
      <div className="schedule-panel">
        <span className="control-label">風速計測試</span>
        <p className="control-note">這個後端版本沒有風速計測試讀取（/api/wind/probe），需要升級。</p>
      </div>
    )
  }

  const refreshPorts = async () => {
    try {
      const next = await getAnemometerProbe()
      if (next) setInfo(next)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '無法讀取序列埠')
    }
  }

  const readOnce = async (): Promise<AnemometerProbeResult | null> => {
    if (request === null) return null
    try {
      const result = await probeAnemometer(request)
      setLast(result)
      setError(null)
      return result
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '讀取失敗')
      return null
    }
  }

  const single = async () => {
    setBusy(true)
    await readOnce()
    setBusy(false)
  }

  const continuous = async () => {
    stop.current = false
    setRuns([])
    setRunning(true)
    setBusy(true)
    for (let index = 0; index < target && !stop.current; index += 1) {
      const started = performance.now()
      const result = await readOnce()
      if (result === null) break  // 設定錯誤或後端斷線。 / Bad settings or the backend is gone.
      setRuns((previous) => [...previous, result])
      const wait = INTERVAL_MS - (performance.now() - started)
      if (wait > 0 && index + 1 < target && !stop.current) await new Promise((resolve) => window.setTimeout(resolve, wait))
    }
    setRunning(false)
    setBusy(false)
  }

  const edit = (change: Partial<AnemometerProbeRequest>) => { if (request) setRequest({ ...request, ...change }) }
  const stats = summarizeProbes(runs)
  const suggested = suggestedTimeoutSeconds(stats)
  const values = last?.values ?? null

  return (
    <div className="schedule-panel anemometer-probe">
      <span className="control-label">風速計測試（Modbus 讀取）</span>
      <p className="control-note">
        經 USB 轉 RS485、LoRa DTU 讀風速計一次（功能碼 0x03，從 reg 0 讀 4 個），只讀不改設定。
        {info && !info.configured && ' config.toml 沒有設定 [wind] port，預設用偵測到的第一個序列埠。'}
      </p>
      {error && <p className="backend-error" role="alert">{error}</p>}
      {request && info && (
        <>
          <div className="schedule-row">
            <label className="sequence-field anemometer-port">
              序列埠
              <input type="text" value={request.port} list="anemometer-ports" spellCheck={false} disabled={running}
                placeholder="/dev/serial/by-id/usb-FTDI_…" onChange={(event) => edit({ port: event.target.value })} />
              <datalist id="anemometer-ports">
                {info.ports.map((port) => <option key={port} value={port} />)}
              </datalist>
            </label>
          </div>
          <p className="control-note anemometer-ports">
            偵測到：{info.ports.length === 0 ? '沒有（USB 轉 RS485 沒插？）' : info.ports.join('、')}
            {' '}<button type="button" className="secondary" disabled={running} onClick={() => { void refreshPorts() }}>重新偵測</button>
          </p>
          <div className="schedule-row">
            <label className="sequence-field">
              baud
              <select value={request.baudrate} disabled={running} onChange={(event) => edit({ baudrate: Number(event.target.value) })}>
                {info.baudrates.map((rate) => <option key={rate} value={rate}>{rate}</option>)}
              </select>
            </label>
            <label className="sequence-field">
              設備位址（1–247）
              <input type="number" min={1} max={247} step={1} value={request.address} disabled={running}
                onChange={(event) => edit({ address: Number(event.target.value) })} />
            </label>
            <label className="sequence-field">
              逾時（秒）
              <input type="number" min={0.1} max={10} step={0.1} value={request.timeoutSeconds} disabled={running}
                onChange={(event) => edit({ timeoutSeconds: Number(event.target.value) })} />
            </label>
          </div>
          <div className="schedule-row">
            <button type="button" disabled={busy || request.port === ''} onClick={() => { void single() }}>讀一次</button>
          </div>
          <div className="schedule-row">
            <label className="audio-option anemometer-run">
              每秒讀一次，共
              <input type="number" className="anemometer-count" min={1} max={3600} step={1} value={target} disabled={running}
                onChange={(event) => setTarget(Math.max(1, Math.min(3600, Math.round(Number(event.target.value)) || 1)))} />
              次
            </label>
            {running ? (
              <button type="button" className="secondary" onClick={() => { stop.current = true }}>停止（{runs.length}/{target}）</button>
            ) : (
              <button type="button" className="secondary" disabled={busy || request.port === ''}
                onClick={() => { void continuous() }}>開始連續讀取</button>
            )}
          </div>
        </>
      )}

      {last && (
        <div className={`anemometer-result ${last.ok ? 'ok' : 'failed'}`} role="status">
          <p>
            <strong>{KIND_LABELS[last.kind]}</strong>
            {last.roundTripMs !== null && ` · 來回 ${last.roundTripMs.toFixed(0)} ms`}
            {` · 收到 ${last.bytes} bytes`}
          </p>
          <p>{last.message}</p>
          {last.detail && <p>{last.detail}</p>}
          {values && (
            <p className="anemometer-values">
              風速 <strong>{values.speedMs.toFixed(1)} m/s</strong>（{values.beaufort} 級）
              · 風向 <strong>{values.directionDeg.toFixed(1)}°</strong>
              · 方位代碼 {values.directionCode}（{DIRECTIONS[values.directionCode] ?? '?'}）
              {values.codeMatches ? ' ✓ 與角度一致' : ' ✗ 與角度不一致'}
            </p>
          )}
          <code>送出 {last.sent}</code>
          <code>收到 {last.received || '（無）'}</code>
          {last.registers && <code>暫存器 {last.registers.join(' ')}</code>}
        </div>
      )}

      {runs.length > 0 && (
        <p className="weather-override" role="status">
          連續讀取 {stats.count} 次：成功 {stats.ok}（{((stats.rate ?? 0) * 100).toFixed(0)}%{stats.rate !== null && stats.count >= 20 ? (stats.rate >= 0.95 ? '，達到 95%' : '，未達 95%') : ''}）
          {stats.roundTrip && ` · 來回 最短 ${stats.roundTrip.min.toFixed(0)} / 平均 ${stats.roundTrip.mean.toFixed(0)} / 最長 ${stats.roundTrip.max.toFixed(0)} ms`}
          {suggested !== null && ` · 建議逾時 ${suggested.toFixed(1)} 秒`}
          {Object.entries(stats.kinds).filter(([kind]) => kind !== 'ok').map(([kind, count]) =>
            ` · ${KIND_LABELS[kind as AnemometerProbeKind]} ${count}`).join('')}
        </p>
      )}
    </div>
  )
}
