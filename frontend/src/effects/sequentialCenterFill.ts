import {
  EFFECT_SCHEMA_VERSION,
  clamp01,
  globalEffectTimeSeconds,
  type EffectModule,
} from './contract'
import { rainStripe } from './rain'

const BLACK = { r: 0, g: 0, b: 0 }
const WHITE = { r: 1, g: 1, b: 1 }

/**
 * 第幾段點亮這台，以及總段數。從 1 號開始：第 i 台在第 i 段；兩頭開始：1 與 N 同一段、2 與 N−1 下一段…往中間。
 * The stage that lights this unit and the stage count. From unit 1: unit i in stage i; from both ends: 1 and N
 * together, then 2 and N−1, … towards the middle.
 */
export function centerFillStage(unitIndex: number, unitCount: number, fromBothEnds: boolean): { stage: number; stages: number } {
  const units = Math.max(1, unitCount)
  if (!fromBothEnds) return { stage: unitIndex, stages: units }
  return { stage: Math.min(unitIndex, units - 1 - unitIndex), stages: Math.ceil(units / 2) }
}

export const sequentialCenterFill: EffectModule = {
  completion: {
    label: '所有 Unit 完成由中心向外點亮',
    frames: (parameters, fps, unitCount) => {
      const expansion = parameters.expansionSeconds ?? 2.5
      const hold = parameters.holdSeconds ?? 0.4
      const { stages } = centerFillStage(0, unitCount, parameters.fromBothEnds === 1)
      return Math.ceil(((stages - 1) * (expansion + hold) + expansion) * fps) + 1
    },
  },
  manifest: {
    schemaVersion: EFFECT_SCHEMA_VERSION,
    id: 'sequential-center-fill',
    label: '逐月',
    description: '從 Unit 1 起逐組由中心向外點亮；已亮起的組別保持全亮，直到全部點亮。起點設為 1 時從兩頭同時開始（1 和最後一台一起，再往中間），一輪時間約減半。',
    parameters: [
      {
        key: 'expansionSeconds',
        label: 'Expansion duration',
        unit: 's / unit',
        min: 0.5,
        max: 8,
        step: 0.25,
        defaultValue: 2.5,
      },
      {
        key: 'holdSeconds',
        label: 'Full hold',
        unit: 's',
        min: 0,
        max: 2,
        step: 0.1,
        defaultValue: 0.4,
      },
      {
        key: 'fromBothEnds',
        label: '起點',
        unit: '0 從 1 號／1 兩頭往中間',
        min: 0,
        max: 1,
        step: 1,
        defaultValue: 0,
      },
    ],
  },

  sample(context, parameters) {
    const expansionSeconds = Math.max(0.01, parameters.expansionSeconds ?? 2.5)
    const holdSeconds = Math.max(0, parameters.holdSeconds ?? 0.4)
    const stageSeconds = expansionSeconds + holdSeconds
    const { stage, stages } = centerFillStage(context.unitIndex, context.unitCount, parameters.fromBothEnds === 1)
    const time = Math.max(0, globalEffectTimeSeconds(context))
    const activeStage = Math.min(Math.floor(time / stageSeconds), stages - 1)

    if (stage < activeStage) return WHITE
    if (stage > activeStage) return BLACK

    const stageTime = time - activeStage * stageSeconds
    const progress = clamp01(stageTime / expansionSeconds)
    const radialPixelStep = context.ledCount <= 1
      ? 1
      : 2 / (context.ledCount - 1)
    const lightFront = progress * (1 + radialPixelStep)
    const brightness = clamp01(
      (lightFront - context.normalizedRadius) / radialPixelStep,
    )

    return {
      r: brightness,
      g: brightness,
      b: brightness,
    }
  },
  // 雨天：填滿過程不變，只亮同心條紋。 / Rain: same fill, only the concentric stripes light.
  rain: (context, _parameters, base) => base() * rainStripe(context),
}
