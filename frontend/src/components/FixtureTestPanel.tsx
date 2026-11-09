import { useEffect, useState } from 'react'
import {
  clearFixtureColorTest,
  getFixtureColorTest,
  setFixtureColorTest,
  type BackendPatch,
  type FixtureColorTest,
} from '../backendApi'
import type { RgbColor } from '../effects'
import { GROUP_LABELS, type FixtureGroup } from './FixtureEditor'

const WHITE: RgbColor = { r: 1, g: 1, b: 1 }
const BLACK: RgbColor = { r: 0, g: 0, b: 0 }
/** 對燈測試的安全時限（秒）；忘記結束時自動恢復。 / Safety limit (s): the test ends by itself if forgotten. */
const LOCATE_SECONDS = 1800

export type FixtureSelection = { group: FixtureGroup; id: number } | null

type Props = {
  counts: Record<FixtureGroup, number>
  patch: BackendPatch | null
  selected: FixtureSelection
  onSelect: (selection: FixtureSelection) => void
  onChange?: () => void
}

// 對燈模式：在 3D 或清單選一顆，實機只亮那顆（白），其他全暗；方便現場確認接線與位址。
// Locate mode: pick a fixture in 3D or the list; on the real output only that one lights white and the rest go dark.
export function FixtureTestPanel({ counts, patch, selected, onSelect, onChange }: Props) {
  const groups = (['ball', 'wash'] as const).filter((group) => counts[group] > 0)
  const group = selected?.group && counts[selected.group] > 0 ? selected.group : groups[0]
  const id = selected?.group === group ? selected.id : 1
  const [test, setTest] = useState<FixtureColorTest | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 「只亮這顆」進行中時，換選別顆就跟著換亮。 / While locating, picking another fixture moves the light to it.
  const [locating, setLocating] = useState(false)

  useEffect(() => {
    let active = true
    getFixtureColorTest().then((state) => { if (active) setTest(state) },
      (failure) => { if (active) setError(failure instanceof Error ? failure.message : '無法讀取對燈測試') })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!locating || !group) return
    const colors = Array.from({ length: counts[group] }, (_, item) => (item === id - 1 ? WHITE : BLACK))
    void setFixtureColorTest(group, colors, LOCATE_SECONDS).then((state) => { setTest(state); onChange?.() },
      (failure) => setError(failure instanceof Error ? failure.message : '測試送出失敗'))
  }, [locating, group, id])

  if (test === null) return <p className="control-note">後端版本沒有球燈／洗牆燈測試 API。</p>
  if (!group) return null
  const count = counts[group]
  const info = patch?.fixtures[group].find((fixture) => fixture.id === id)
  const anyActive = test ? Object.values(test).some((entry) => entry?.active) : false

  const run = async (request: () => Promise<FixtureColorTest>) => {
    setBusy(true)
    setError(null)
    try {
      setTest(await request())
      onChange?.()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '測試送出失敗')
    } finally {
      setBusy(false)
    }
  }
  const only = (index: number) => Array.from({ length: count }, (_, item) => (item === index ? WHITE : BLACK))
  const step = (delta: number) => onSelect({ group, id: ((id - 1 + delta + count) % count) + 1 })

  return (
    <div className="unit-test fixture-test">
      <span className="control-label">對燈模式（球燈／洗牆燈）</span>
      <p className="control-note">在 3D 畫面點選一顆，或在下方選擇；按「只亮這顆」後，實機只有這顆亮白、其他全暗。</p>
      <div className="button-row">
        {groups.length > 1 && (
          <select aria-label="群組" value={group} onChange={(event) => onSelect({ group: event.target.value as FixtureGroup, id: 1 })}>
            {groups.map((item) => <option key={item} value={item}>{GROUP_LABELS[item]}</option>)}
          </select>
        )}
        <button type="button" className="secondary" onClick={() => step(-1)} aria-label="上一顆">←</button>
        <select aria-label="編號" value={id} onChange={(event) => onSelect({ group, id: Number(event.target.value) })}>
          {Array.from({ length: count }, (_, index) => (
            <option key={index} value={index + 1}>{GROUP_LABELS[group]} {index + 1}</option>
          ))}
        </select>
        <button type="button" className="secondary" onClick={() => step(1)} aria-label="下一顆">→</button>
      </div>
      {info && (
        <p className="control-note fixture-address">
          {info.node} · {info.ip} · Universe {info.universe} · Ch {info.address}–{info.lastChannel}（{info.channels.join(' ')}）
          {info.position && <> · 座標 x {info.position.x ?? '–'} y {info.position.y ?? '–'} z {info.position.z ?? '–'} cm</>}
        </p>
      )}
      <div className="button-row">
        <button type="button" disabled={busy} onClick={() => {
          setLocating(true)
          void run(() => setFixtureColorTest(group, only(id - 1), LOCATE_SECONDS))
        }}>只亮這顆</button>
        <button type="button" className="secondary" disabled={busy} onClick={() => {
          setLocating(false)
          void run(() => setFixtureColorTest(group, Array.from({ length: count }, () => WHITE), LOCATE_SECONDS))
        }}>全部亮白</button>
        <button type="button" className="secondary" disabled={busy || !anyActive} onClick={() => {
          setLocating(false)
          void run(clearFixtureColorTest)
        }}>結束測試</button>
      </div>
      <p role="status" className="control-note">
        {locating && anyActive ? `對燈中：${GROUP_LABELS[group]} ${id}（換選別顆會跟著換亮）；` : ''}
        {anyActive ? `測試中（${groups.filter((item) => test?.[item]?.active).map((item) => GROUP_LABELS[item]).join('、')}）；`
          + `最多 ${LOCATE_SECONDS / 60} 分鐘後自動恢復播放。` : '目前沒有測試，照常播放。'}
      </p>
      {error && <p className="backend-error" role="alert">{error}</p>}
    </div>
  )
}
