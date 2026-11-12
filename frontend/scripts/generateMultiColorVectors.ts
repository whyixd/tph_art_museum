/**
 * 多色模式的前後端對照向量，寫到 backend/fixtures/multi_color_v1.json（前端 tests/multiColor.test.ts 與後端
 * tests/test_multi_color.py 都讀它）。
 * Writes the multi-colour parity vectors to backend/fixtures/multi_color_v1.json, read by both test suites.
 */
import { writeFileSync } from 'node:fs'
import { buildMultiColorVectors } from '../tests/multiColorVectors'

writeFileSync(
  new URL('../../backend/fixtures/multi_color_v1.json', import.meta.url),
  `${JSON.stringify(buildMultiColorVectors(), null, 1)}\n`,
)
