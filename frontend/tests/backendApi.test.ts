import { afterEach, describe, expect, it, vi } from 'vitest'
import { applyBackendPreset, fromBackendSequence, getBackendSequence, getBackendState, saveBackendSequence, setBackendMotor, toBackendSequence } from '../src/backendApi'
import { createDefaultFixturePreset } from '../src/fixtures'
import { createDefaultEffectPreset } from '../src/effects'

afterEach(() => vi.unstubAllGlobals())

describe('backend preset API', () => {
  it('loads state through the same-origin API path', async () => {
    const preset = createDefaultEffectPreset('random-leds')
    const state = { revision: 2, preset, motor: { channel: 469, enabled: false, value: 0 }, output: { ip: '192.0.2.5', universe: 0, fps: 40, rpm: 360, unitId: 1, unitCount: 9 } }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => state })
    vi.stubGlobal('fetch', fetchMock)
    expect(await getBackendState()).toEqual(state)
    expect(fetchMock).toHaveBeenCalledWith('/api/state', { cache: 'no-store' })
  })

  it('sends a complete preset only when apply is invoked', async () => {
    const preset = createDefaultEffectPreset('center-out-sine')
    const state = { revision: 3, preset, motor: { channel: 469, enabled: false, value: 0 }, output: { ip: '192.0.2.5', universe: 0, fps: 40, rpm: 360, unitId: 1, unitCount: 9 } }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => state })
    vi.stubGlobal('fetch', fetchMock)
    expect(await applyBackendPreset(preset)).toEqual(state)
    expect(fetchMock).toHaveBeenCalledWith('/api/preset', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(preset),
    })
  })

  it('loads and saves the same sequence document through the backend API', async () => {
    const sequence = {
      schemaVersion: 1 as const,
      ending: { kind: 'hold' as const },
      steps: [{
        id: 1,
        preset: createDefaultEffectPreset('center-out-sine'),
        playback: { kind: 'count' as const, count: 1 },
        transition: { kind: 'cut' as const, seconds: 0.5 },
      }],
    }
    const state = { revision: 3, sequence, playbackMode: 'sequence', output: { fps: 40, rpm: 360, unitCount: 9 } }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => state })
    vi.stubGlobal('fetch', fetchMock)
    expect(await getBackendSequence()).toEqual(state)
    expect(fetchMock).toHaveBeenCalledWith('/api/sequence', {
      cache: 'no-store',
    })
    expect(await saveBackendSequence(sequence)).toEqual(state)
    // 後端只收 Sequence v2：轉燈放在 spinner。 / The backend stores Sequence v2: the spinner preset goes in `spinner`.
    expect(fetchMock).toHaveBeenCalledWith('/api/sequence', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        schemaVersion: 2,
        ending: sequence.ending,
        steps: [{ id: 1, spinner: sequence.steps[0].preset, playback: sequence.steps[0].playback, transition: sequence.steps[0].transition }],
      }),
    })
  })

  it('keeps v2 ball and wash presets through an edit round trip', async () => {
    const ball = createDefaultFixturePreset('height-sweep')
    const wash = { ...createDefaultFixturePreset('fixture-breathing'), maxLevel: 0.5 }
    const backend = {
      schemaVersion: 2 as const,
      ending: { kind: 'loop' as const },
      steps: [
        { id: 1, spinner: createDefaultEffectPreset('full-on'), ball, wash,
          playback: { kind: 'count' as const, count: 2, clock: 'ball' as const },
          transition: { kind: 'crossfade' as const, seconds: 1 } },
        { id: 2, spinner: createDefaultEffectPreset('center-out-sine'),
          playback: { kind: 'time' as const, seconds: 4 }, transition: { kind: 'cut' as const, seconds: 0.5 } },
      ],
    }
    const internal = fromBackendSequence(backend)
    expect(internal.schemaVersion).toBe(1)
    expect(internal.steps[0].preset).toEqual(backend.steps[0].spinner)
    expect(internal.steps[0].fixtures).toEqual({ ball, wash })
    expect(internal.steps[1].fixtures).toBeUndefined()
    // 編輯轉燈 preset（保留其他欄位）後存回，球燈／洗牆燈不變。 / Editing the spinner keeps ball / wash.
    const edited = { ...internal, steps: [{ ...internal.steps[0], preset: createDefaultEffectPreset('sine-swell') }, internal.steps[1]] }
    const saved = toBackendSequence(edited)
    expect(saved.steps[0]).toEqual({ ...backend.steps[0], spinner: createDefaultEffectPreset('sine-swell') })
    expect(saved.steps[1]).toEqual(backend.steps[1])
    expect(fromBackendSequence(internal)).toBe(internal)  // v1 passes through
  })

  it('surfaces backend validation errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 422, json: async () => ({ error: 'invalid effect parameters' }),
    }))
    await expect(applyBackendPreset(createDefaultEffectPreset('random-leds')))
      .rejects.toThrow('invalid effect parameters')
  })

  it('sends a separate boolean motor command', async () => {
    const state = { motor: { channel: 469, enabled: true, value: 255 } }
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => state })
    vi.stubGlobal('fetch', fetchMock)
    expect(await setBackendMotor(true)).toEqual(state)
    expect(fetchMock).toHaveBeenCalledWith('/api/motor', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: '{"enabled":true}',
    })
  })
})
