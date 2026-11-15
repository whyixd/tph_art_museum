import type { AnemometerProbeKind, AnemometerProbeResult } from '../backendApi'

export type ProbeStats = {
  count: number
  ok: number
  /** 成功率 0–1；沒有讀取時 null。 / Success rate 0..1; null before any read. */
  rate: number | null
  /** 成功讀取的來回時間（ms）。 / Round trips of the good reads (ms). */
  roundTrip: { min: number; mean: number; max: number } | null
  kinds: Partial<Record<AnemometerProbeKind, number>>
}

/** 連續讀取的統計（現場清單：成功率 ≥ 95%，逾時設為來回時間最大值 × 2）。 / Stats of a run of reads. */
export function summarizeProbes(results: readonly AnemometerProbeResult[]): ProbeStats {
  const kinds: Partial<Record<AnemometerProbeKind, number>> = {}
  for (const result of results) kinds[result.kind] = (kinds[result.kind] ?? 0) + 1
  const trips = results.filter((result) => result.ok && result.roundTripMs !== null).map((result) => result.roundTripMs as number)
  const ok = results.filter((result) => result.ok).length
  return {
    count: results.length,
    ok,
    rate: results.length === 0 ? null : ok / results.length,
    roundTrip: trips.length === 0 ? null : {
      min: Math.min(...trips),
      mean: trips.reduce((sum, value) => sum + value, 0) / trips.length,
      max: Math.max(...trips),
    },
    kinds,
  }
}

/** 建議的逾時（秒）：來回時間最大值 × 2，至少 0.5 秒，取到 0.1 秒。 / Suggested timeout: max round trip × 2. */
export function suggestedTimeoutSeconds(stats: ProbeStats): number | null {
  if (stats.roundTrip === null) return null
  return Math.max(0.5, Math.ceil(stats.roundTrip.max * 2 / 100) / 10)
}
