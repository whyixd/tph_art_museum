import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAnemometerProbe, probeAnemometer, type AnemometerProbeResult } from '../src/backendApi'
import { suggestedTimeoutSeconds, summarizeProbes } from '../src/wind/probeStats'

afterEach(() => vi.unstubAllGlobals())

const REQUEST = { port: '/dev/ttyUSB0', baudrate: 9600, address: 1, timeoutSeconds: 2 }

function result(kind: AnemometerProbeResult['kind'], roundTripMs: number | null): AnemometerProbeResult {
  return {
    ...REQUEST, ok: kind === 'ok', kind, message: '', detail: null, sent: '01 03 00 00 00 04 44 09', received: '',
    bytes: 0, roundTripMs, noiseBytes: 0, registers: null, values: null,
  }
}

describe('anemometer probe stats', () => {
  it('counts the success rate, kinds and round trips of the good reads', () => {
    const stats = summarizeProbes([result('ok', 300), result('ok', 500), result('no-response', null), result('incomplete', 900)])
    expect(stats.count).toBe(4)
    expect(stats.ok).toBe(2)
    expect(stats.rate).toBe(0.5)
    expect(stats.roundTrip).toEqual({ min: 300, mean: 400, max: 500 })
    expect(stats.kinds).toEqual({ ok: 2, 'no-response': 1, incomplete: 1 })
    expect(suggestedTimeoutSeconds(stats)).toBe(1)
  })

  it('has no rate or suggestion before a good read', () => {
    expect(summarizeProbes([]).rate).toBeNull()
    expect(suggestedTimeoutSeconds(summarizeProbes([result('no-response', null)]))).toBeNull()
    expect(suggestedTimeoutSeconds(summarizeProbes([result('ok', 40)]))).toBe(0.5)
  })
})

describe('anemometer probe API', () => {
  it('returns null on an old backend and sends the probe settings', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ error: 'not found' }) }))
    expect(await getAnemometerProbe()).toBeNull()
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => result('ok', 200) })
    vi.stubGlobal('fetch', fetchMock)
    expect((await probeAnemometer(REQUEST)).kind).toBe('ok')
    expect(fetchMock).toHaveBeenCalledWith('/api/wind/probe', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(REQUEST),
    })
  })
})
