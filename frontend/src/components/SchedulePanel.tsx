import { useEffect, useState } from 'react'
import {
  confirmSchedule, getSchedule, getShows, saveSchedule,
  type ScheduleGroup, type ScheduleSettings, type ScheduleState,
} from '../backendApi'

const GROUPS: { id: ScheduleGroup; label: string }[] = [
  { id: 'spinner', label: '轉燈' },
  { id: 'ball', label: '球燈' },
  { id: 'wash', label: '洗牆燈' },
]

const PHASES: Record<ScheduleState['status']['phase'], string> = {
  disabled: '排程停用（手動控制）',
  off: '啟動時間外：馬達停止、選擇的燈組關閉',
  standby: '待機效果',
  show: '演出中',
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/
const MONTH_DAY = /^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/

/** 輸入的表演時間（逗號、空白或換行分隔）。 / Show times as typed (commas, spaces or new lines). */
function parseTimes(text: string): { times: string[]; invalid: string[] } {
  const parts = text.split(/[\s,，、]+/).filter((part) => part !== '')
  // 9:30 → 09:30
  const normalized = parts.map((part) => (/^\d:[0-5]\d$/.test(part) ? `0${part}` : part))
  return {
    times: [...new Set(normalized.filter((part) => TIME.test(part)))].sort(),
    invalid: normalized.filter((part) => !TIME.test(part)),
  }
}

/** 每小時的第幾分（0–59）。 / Minutes of the hour as typed. */
function parseMinutes(text: string): { minutes: number[]; invalid: string[] } {
  const parts = text.split(/[\s,，、]+/).filter((part) => part !== '')
  const good = (part: string) => /^\d{1,2}$/.test(part) && Number(part) <= 59
  return {
    minutes: [...new Set(parts.filter(good).map(Number))].sort((a, b) => a - b),
    invalid: parts.filter((part) => !good(part)),
  }
}

function minutes(time: string): number {
  return Number(time.slice(0, 2)) * 60 + Number(time.slice(3))
}

function inHours(hours: ScheduleSettings['hours'], time: string): boolean {
  const start = minutes(hours.start)
  const end = minutes(hours.end)
  const at = minutes(time)
  return start < end ? at >= start && at < end : at >= start || at < end
}

// 每日排程（M13）：裝置啟動時間內播待機效果、表演時間播演出；啟動時間外馬達停止、選擇的燈組關閉。
// Daily schedule (M13): inside the opening hours the standby effect plays and shows play at their times; outside
// them the motors stop and the chosen light groups go dark.
export function SchedulePanel() {
  const [state, setState] = useState<ScheduleState | null | undefined>(undefined)
  // 編輯中的設定；null = 跟著後端。 / The settings being edited; null = follow the backend.
  const [draft, setDraft] = useState<ScheduleSettings | null>(null)
  const [timesText, setTimesText] = useState<string | null>(null)
  const [minutesText, setMinutesText] = useState<string | null>(null)
  const [shows, setShows] = useState<{ id: string; title: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    const refresh = () => {
      getSchedule().then((next) => { if (active) setState(next) },
        (failure) => { if (active) setError(failure instanceof Error ? failure.message : '無法讀取排程') })
      getShows().then((next) => {
        if (active && next) setShows(next.shows.map((show) => ({ id: show.id, title: show.title })))
      }, () => undefined)
    }
    refresh()
    const timer = window.setInterval(refresh, 5_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  if (state === undefined) return null
  if (state === null) {
    return (
      <div className="schedule-panel">
        <span className="control-label">每日排程</span>
        <p className="control-note">排程需要較新的後端（/api/schedule）。</p>
      </div>
    )
  }

  const settings = draft ?? state.settings
  const text = timesText ?? state.settings.showTimes.join(' ')
  const parsed = parseTimes(text)
  const everyText = minutesText ?? state.settings.showMinutes.join(' ')
  const every = parseMinutes(everyText)
  const dirty = draft !== null || timesText !== null || minutesText !== null
  const edit = (change: Partial<ScheduleSettings>) => setDraft({ ...settings, ...change })
  const hoursValid = TIME.test(settings.hours.start) && TIME.test(settings.hours.end)
    && settings.hours.start !== settings.hours.end
  const rulesValid = settings.dateShows.every((rule) =>
    MONTH_DAY.test(rule.from) && MONTH_DAY.test(rule.to) && rule.showId !== '')
  const repeatsValid = Number.isInteger(settings.showRepeats) && settings.showRepeats >= 1 && settings.showRepeats <= 50
  const showHoursValid = TIME.test(settings.showHours.start) && TIME.test(settings.showHours.end)
  const valid = hoursValid && showHoursValid && rulesValid && repeatsValid && parsed.invalid.length === 0 && every.invalid.length === 0
  // 剛好在關閉時間的那一場照播。 / A time exactly at closing still plays.
  const playable = (time: string) => inHours(settings.hours, time) || time === settings.hours.end
  const outside = hoursValid ? parsed.times.filter((time) => !playable(time)) : []
  // 表演時段內實際會播的時間。 / The times that play inside the show period.
  const periodTimes: string[] = []
  if (hoursValid && showHoursValid) {
    const first = minutes(settings.showHours.start)
    const last = minutes(settings.showHours.end)
    for (let hour = 0; hour < 24; hour += 1) {
      for (const minute of every.minutes) {
        const at = hour * 60 + minute
        const time = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
        if ((first <= last ? at >= first && at <= last : at >= first || at <= last) && playable(time)) periodTimes.push(time)
      }
    }
  }
  const title = (id: string | null) => (id === null ? '未指定' : shows.find((show) => show.id === id)?.title ?? `${id}（找不到）`)
  const status = state.status

  const save = async (next: ScheduleSettings) => {
    setBusy(true)
    setError(null)
    try {
      setState(await saveSchedule(next))
      setDraft(null)
      setTimesText(null)
      setMinutesText(null)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '無法儲存排程')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      setState(await confirmSchedule())
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '無法確認')
    } finally {
      setBusy(false)
    }
  }
  // 後端重啟後、排程已啟用但還沒確認：排程不會啟動馬達。 / Enabled but unconfirmed since the backend started.
  const needsConfirm = state.settings.enabled && state.settings.startMotors && !status.motorsConfirmed

  const showOptions = (selected: string | null) => (
    <>
      {selected !== null && selected !== '' && !shows.some((show) => show.id === selected) && (
        <option value={selected}>{selected}（找不到）</option>
      )}
      {shows.map((show) => <option key={show.id} value={show.id}>{show.title}</option>)}
    </>
  )

  return (
    <div className="schedule-panel">
      <span className="control-label">每日排程</span>
      <p className="weather-override" role="status">
        {PHASES[status.phase]}
        {` · 主機時間 ${status.serverTime.slice(11, 16)}`}
        {status.nextShow
          ? ` · 下一場 ${status.nextShow.at.slice(5, 10)} ${status.nextShow.at.slice(11, 16)}「${title(status.nextShow.showId)}」`
          : status.phase !== 'disabled' ? ' · 沒有下一場演出' : ''}
      </p>
      {needsConfirm && (
        <div className="schedule-confirm" role="alert">
          <p>
            後端重新啟動過：排程<strong>不會啟動馬達</strong>，燈光與演出照常。確認現場安全後按下確認
            {status.inHours ? '，馬達會立刻啟動' : '，下次啟動時間開始時馬達才會啟動'}。每次重啟只需確認一次。
          </p>
          <button type="button" disabled={busy} onClick={() => { void confirm() }}>確認，允許排程啟動馬達</button>
        </div>
      )}
      {status.warning && <p className="backend-error" role="alert">{status.warning}</p>}
      {status.error && <p className="backend-error" role="alert">{status.error}</p>}

      <label className="audio-option">
        <input type="checkbox" checked={settings.enabled} onChange={(event) => edit({ enabled: event.target.checked })} />
        啟用排程（儲存後立刻依現在時間套用，會自動啟動／停止馬達；手動啟用視同已確認）
      </label>

      <div className="schedule-row">
        <label className="sequence-field">
          裝置啟動
          <input type="time" value={settings.hours.start}
            onChange={(event) => edit({ hours: { ...settings.hours, start: event.target.value } })} />
        </label>
        <label className="sequence-field">
          裝置關閉
          <input type="time" value={settings.hours.end}
            onChange={(event) => edit({ hours: { ...settings.hours, end: event.target.value } })} />
        </label>
      </div>
      {!hoursValid && <p className="backend-error" role="alert">啟動與關閉時間要填，而且不能相同。</p>}

      <span className="schedule-caption">啟動時間外關閉的燈組（馬達一律停止）</span>
      <div className="schedule-row">
        {GROUPS.map((group) => (
          <label key={group.id} className="audio-option">
            <input type="checkbox" checked={settings.offGroups.includes(group.id)}
              onChange={(event) => edit({
                offGroups: GROUPS.map((entry) => entry.id).filter((id) =>
                  (id === group.id ? event.target.checked : settings.offGroups.includes(id))),
              })} />
            {group.label}
          </label>
        ))}
      </div>
      <label className="audio-option">
        <input type="checkbox" checked={settings.startMotors} onChange={(event) => edit({ startMotors: event.target.checked })} />
        啟動時間開始時啟動全部馬達
      </label>

      <div className="schedule-row">
        <label className="sequence-field">
          表演時段開始
          <input type="time" value={settings.showHours.start}
            onChange={(event) => edit({ showHours: { ...settings.showHours, start: event.target.value } })} />
        </label>
        <label className="sequence-field">
          表演時段結束（含）
          <input type="time" value={settings.showHours.end}
            onChange={(event) => edit({ showHours: { ...settings.showHours, end: event.target.value } })} />
        </label>
      </div>
      {!showHoursValid && <p className="backend-error" role="alert">表演時段的開始與結束時間都要填。</p>}
      <div className="schedule-row">
        <label className="sequence-field">
          時段內每小時的第幾分表演（0 30 = 整點與 30 分；留空 = 不用）
          <input type="text" value={everyText} placeholder="0 30" spellCheck={false}
            onChange={(event) => setMinutesText(event.target.value)} />
        </label>
        <label className="sequence-field">
          每次連播幾輪
          <input type="number" min={1} max={50} step={1} value={Number.isNaN(settings.showRepeats) ? '' : settings.showRepeats}
            onChange={(event) => edit({ showRepeats: event.target.value === '' ? NaN : Number(event.target.value) })} />
        </label>
      </div>
      {every.invalid.length > 0 && (
        <p className="backend-error" role="alert">看不懂的分鐘：{every.invalid.join('、')}（0–59）</p>
      )}
      {showHoursValid && every.invalid.length === 0 && (
        <p className="schedule-caption" role="status">
          {periodTimes.length > 0
            ? `表演時段共 ${periodTimes.length} 場：${periodTimes.join('、')}`
            : '表演時段內沒有會播的時間。'}
        </p>
      )}
      {!repeatsValid && <p className="backend-error" role="alert">連播輪數要是 1–50 的整數。</p>}
      <label className="sequence-field">
        另外指定的表演時間（每天；用空白或逗號分隔，例如 11:15 14:45；可留空）
        <input type="text" value={text} placeholder="11:15 14:45" spellCheck={false}
          onChange={(event) => setTimesText(event.target.value)} />
      </label>
      {parsed.invalid.length > 0 && (
        <p className="backend-error" role="alert">看不懂的時間：{parsed.invalid.join('、')}（格式 HH:MM）</p>
      )}
      {outside.length > 0 && (
        <p className="backend-error" role="status">在啟動時間外、不會播放：{outside.join('、')}</p>
      )}

      <label className="sequence-field">
        演出（平常）
        <select value={settings.showId ?? ''} onChange={(event) => edit({ showId: event.target.value || null })}>
          <option value="">不播放演出</option>
          {showOptions(settings.showId)}
        </select>
      </label>

      <span className="schedule-caption">指定日期改播（月-日，含頭尾，可跨年；由上往下第一個符合的）</span>
      {settings.dateShows.map((rule, index) => {
        const change = (patch: Partial<typeof rule>) => edit({
          dateShows: settings.dateShows.map((entry, at) => (at === index ? { ...entry, ...patch } : entry)),
        })
        return (
          <div key={index} className="schedule-row schedule-rule">
            <input type="text" value={rule.from} placeholder="12-01" aria-label="開始日期（月-日）" maxLength={5}
              onChange={(event) => change({ from: event.target.value })} />
            <span>到</span>
            <input type="text" value={rule.to} placeholder="12-25" aria-label="結束日期（月-日）" maxLength={5}
              onChange={(event) => change({ to: event.target.value })} />
            <select value={rule.showId} aria-label="演出" onChange={(event) => change({ showId: event.target.value })}>
              <option value="">選擇演出</option>
              {showOptions(rule.showId)}
            </select>
            <button type="button" className="secondary" aria-label="刪除這條日期規則"
              onClick={() => edit({ dateShows: settings.dateShows.filter((_, at) => at !== index) })}>刪除</button>
          </div>
        )
      })}
      {!rulesValid && <p className="backend-error" role="alert">日期格式為 MM-DD（例如 12-25），而且每條都要選演出。</p>}
      <div className="button-row">
        <button type="button" className="secondary" disabled={settings.dateShows.length >= 32}
          onClick={() => edit({ dateShows: [...settings.dateShows, { from: '', to: '', showId: '' }] })}>新增日期規則</button>
      </div>

      <div className="button-row">
        <button type="button" disabled={busy || !dirty || !valid}
          onClick={() => { void save({ ...settings, showTimes: parsed.times, showMinutes: every.minutes }) }}>儲存並套用</button>
        <button type="button" className="secondary" disabled={busy || !dirty}
          onClick={() => { setDraft(null); setTimesText(null); setMinutesText(null); setError(null) }}>放棄變更</button>
      </div>
      {error && <p className="backend-error" role="alert">{error}</p>}
      <p className="control-note">
        依主機（Pi）的本地時間。啟動時間內播待機效果（「風力」分頁的風流），表演時段內每到表演時間就播當天的演出，連播設定的輪數後回到待機
        （上一場還沒播完就到下一個表演時間時，會從頭重新開始）；關閉時間一到，馬達全停、勾選的燈組關閉、手動播的演出停止。
        剛好在關閉時間的那一場照播，排程開始的演出會等它播完才關閉。排程只在狀態改變時動作，
        中間手動調整的馬達、關燈、待機會保留到下一次改變；要長時間手動控制請先停用排程。
        後端或 Pi 重新啟動後，燈光與演出依當時時間自動套用，但馬達要先在這裡確認一次才會由排程啟動。
      </p>
    </div>
  )
}
