# tph_art_museum 實作計畫（初稿）

> 2026-11-02。以 `doc/system-reference.md`（Moon Observe 系統）為基礎，在美術館現場執行：
> - **轉燈 24 顆**（旋轉球形 POV 燈，取代原本的 9 組半圓燈；實體配置不同，細節待補）
> - **球燈 37 顆**（40cm 塑料燈罩，DMX 燈具，經 Art-Net 控制）——新增
> - **洗牆燈 N 顆**（DMX 燈具，經 Art-Net 控制）——新增
>
> 系統部分（effect engine、串接、天氣、API、前端、設定檔、原子寫檔、驗證規則）原則上沿用 reference；
> 本文件只寫**差異**。標 **待確認** 的項目會擋住對應里程碑。

## 0. 參考資料（`doc/`）

| 檔案 | 內容 | 本計畫用途 |
|---|---|---|
| `system-reference.md` | Moon Observe 系統規格 | 架構基礎 |
| `裝置specs.pdf` | 單支燈桿：上方 40cm 球燈（金屬抱箍）、下方旋轉 LED 球（top 94cm 含懸臂、side 93cm、front 70cm）；top view 有 6 條放射狀燈條 | 轉燈幾何（**待確認**）、球燈外型 |
| `spaces04.pdf` | 上視圖／正視圖／側視圖、轉燈 24、球燈 37、說明牌 A/B | 場景配置、模擬器排列 |
| `DMX＿線間距 (1).pdf` | 球燈 1–37 編號、各顆高度 H（cm）、紅字為相鄰球燈間的 DMX 線長 | DMX 位址順序、線路分組、模擬器高度 |
| `image (6).png` | 上視圖，空間 2833 × 953 cm | 燈桿平面座標 |
| `image (1) (1).png` | 上視圖局部（說明牌區域尺寸） | 參考 |
| `firmware/wled/`、`firmware/sketches/motor_pwm_test/`、`firmware/sketches/motor_control_io/` | 2026-11-09 從 `~/Dev/moon_observe/firmware` 複製（含該 repo 當時未 commit 的 README 修改與 `wiring-io13-d8.svg`）：WLED usermod（Art-Net ch469 → GL-C-616WL IO13 → XIAO SAMD21 D8）、刷機腳本、馬達 PWM 測試與馬達控制 sketch（2026-11-09 由 `motor_control_d8` 改名為 `motor_control_io`，輸入腳位目前仍是 D8） | M4 轉燈馬達 |

## 1. 決策（先採用的預設，可調整）

| # | 決策 | 理由 |
|---|---|---|
| 1 | 從 `~/Dev/moon_observe` 複製 `backend/`、`frontend/` 到本 repo 再修改，兩邊互不影響 | 與 `moon_observe_re` 相同做法 |
| 2 | 參考 `moon_observe_re` 已完成的通用化（`render.pixels` 可設定、`targets.universes`、`[hardware]` 色序／亮度、`hardware.build_payload`）直接搬過來 | 避免重做 |
| 3 | 系統內分成三個**燈具群組（group）**：`spinner`（轉燈）、`ball`（球燈）、`wash`（洗牆燈），共用同一條 Art-Net 輸出迴圈與同一個影格時鐘 | 三者需要同步編排、同步轉場、同步天氣 |
| 4 | 轉燈沿用 reference 的「逐 LED 純函式 effect engine」；球燈／洗牆燈每顆只有一個顏色，另做「逐燈具」的 effect engine（第 3 節） | 球燈不是像素陣列，不需要也不適合 strip/radius 幾何 |
| 5 | 串接（Sequence）每段同時指定三個群組的 preset，transition 三者一起套用 | 編排與轉場一致；也保留只改其中一個群組的可能 |
| 6 | DMX 燈具的通道排列用 config 的 **fixture profile** 描述，不寫死在程式 | 球燈、洗牆燈型號未定，日後換燈只改 config |
| 7 | 天氣色盤同時作用於三個群組（各群組可個別關閉 `weatherPalette`） | 沿用 reference 行為 |
| 8 | 前後端雙實作、逐值一致的原則同樣適用於新的 fixture effect engine（同一份 fixture vectors 測試） | 維持 reference 的一致性保證 |

## 2. 轉燈（spinner）—— 待使用者描述實體配置

reference 是「每組一條半圓 156 顆，兩條手臂共用資料」；本案 `裝置specs.pdf` 的 top view 顯示 **6 條放射狀燈條**（可能是 3 個整圓或 6 條半圓弧）。以下全部 **待確認**：

- 每台燈條數、每條 LED 數、兩端位置、各燈條是否共用同一份資料（決定 effect engine 一台要算幾顆）。
- 控制器型號與每台需要幾個 universe（>170 顆 RGB 就要跨 universe）。reference 是 GL-C-616WL（WLED）、ch469 馬達。
- 馬達控制方式：開／關（reference）、或速度＋方向（`moon_observe_re`）。
- 色序、亮度上限、需不需要類似「月亮校正」的位置亮度補償。
- RPM、旋轉方向。

先做的通用化（不依賴上述細節）：
- `render.pixels` 改為可設定（沿用 `moon_observe_re` M2）；effect 參數上限改依 `ledCount`（`moon_observe_re` M4）。
- `unit_count` 上限從 9 放寬到 24（或更多）；`unitVariation`、多雲錯開 `cloud_step`、`unit-chase`／`unit-random`／`roulette-ring`、`sequential-center-fill`（逐月 Unit 1→9）全部改為依 `unitCount`，並檢查 24 台時的節奏是否合理（例如多雲錯開 23 × cloud_step 影格）。
- 新增 `EffectContext` 欄位 `armIndex` / `armCount`（若各燈條不共用資料），以及燈具的 **場景座標** `unitPosition {x, y, z}`，讓效果可以依實際空間位置編排（例如由入口往內推進）。

**效能風險**：reference 每影格 9 × 156 = 1,404 次純 Python 取樣。若 24 台 × 每台數百顆獨立像素，取樣量會增加 10–40 倍，Pi 5 的純 Python 可能撐不住 40 FPS。對策依序：
1. 共用資料的燈條只算一次；
2. 以「每台一份 LED 亮度表」做快取（同影格、同 preset 的台只差 offset 時可重用）；
3. 必要時把 `effects.py` 的熱路徑改成 numpy 向量化（仍以 fixtures 比對前端結果）。

**M2 基準測試結果**（`backend/scripts/benchmark_render.py`，24 台、40 FPS，預算 25 ms／影格；取各效果預設值，影格耗時的中位數；只算單一效果，串接轉場時兩層相加約 2 倍）：

| 執行環境（Apple M4） | 156 顆 | 312 顆 | 624 顆 | 最慢的效果 |
|---|---|---|---|---|
| CPython 3.12 | 最慢 15.3 ms（61%） | 30.6 ms（123%） | 62.3 ms（249%） | `roulette-ring`、`stardust-cloud`、`moving-rings`、`random-fill` |
| PyPy 3.12（程式不改） | 5.0 ms（20%） | 10.2 ms（41%） | 20.4 ms（82%） | `roulette-ring` 明顯比其他慢 2–4 倍 |

- PyPy 3.12 下 backend 全部測試通過（含前後端一致性 fixtures），一般效果快 6–10 倍。
- Pi 5（Cortex-A76）單核心大約比 M4 慢 3–4 倍：**Pi 5 + CPython 連 156 顆都不夠**；Pi 5 + PyPy 只有 156 顆勉強可行。
- 建議（等 Q1 確定 LED 數、Q6 確定主機後定案）：
  1. 執行環境改用 **PyPy**（uv 可管理；Linux aarch64／x86-64、Windows 都有版本），不需改程式；
  2. 主機若 LED 數 ≥ 312，改用 x86 mini PC 或 Mac mini 等級，不用 Pi 5；
  3. 若仍不夠，再依序做上面的對策 1–3，以及多程序（各程序負責一部分轉燈）；`roulette-ring` 值得單獨最佳化。
- 實機一定要在目標主機重跑同一支腳本確認。

## 3. 球燈與洗牆燈（DMX fixtures）

### 3.1 已知燈具

- **球燈**：37 顆，RGBW DMX 燈，通道順序 R G B W、沒有 dimmer／strobe；位址已燒錄，編號 1–37（與 `DMX＿線間距.pdf` 的編號相同）。
- **洗牆燈**：型號、數量、通道表都**待確認**；設定格式已支援，確定後只要加一段 profile + patch。

### 3.2 設定格式（M3 已實作）

```toml
[[dmx.profiles]]
name = "ball-rgbw"
channels = ["r", "g", "b", "w"]   # 燈具的通道順序；r/g/b/w 以外的名稱（dimmer、strobe…）都要在 static 給固定值
white = "min"                      # min：W = min(R,G,B)，並從 RGB 扣掉；none：W 不亮。有 w 通道時預設 min
brightness = 1.0                   # 0..1
static = {}                        # 例：{ dimmer = 255, strobe = 0 }

[[dmx.nodes]]                      # Art-Net → DMX 轉換器的一個 port = 一個 universe
name = "ball-node"
ip = "192.0.2.200"
universe = 100

[[dmx.patch]]                      # 一段連續編號的燈具
group = "ball"                     # ball / wash
first_id = 1
count = 37
profile = "ball-rgbw"
node = "ball-node"
address = 1                        # 第一顆的起始位址
# address_step = 4                 # 預設 = profile 的通道數 → 1, 5, 9, … 145
```

- 驗證：多餘欄位、同一 universe 內位址重疊、超過 512、編號重複、profile／node 不存在、DMX universe 與轉燈的 IP/universe 衝突，都會報錯。
- `run.py --patch-report`：印出每個 node universe 的位址表（群組、編號、通道範圍、通道名稱），給現場接線與對燈用。
- 每個 node universe 每影格送一包完整 512 bytes；沒用到的通道為 0。
- 燈具在場景中的座標（給前端模擬器、依空間位置的效果）留到 M5／M7，另外放一份 layout 檔，不放在 patch 裡。

### 3.3 Fixture effect engine

純函式，與 reference 同樣以影格為時間：

```
sampleFixture(preset, context, elapsedFrame, raining) -> RGB（0–1）
```

`FixtureContext`：`controllerFrame`、`globalControllerFrame`、`controllerFps`、`fixtureIndex`／`fixtureCount`、`position {x,y,z}`、`normalizedHeight`（z 在群組內的 0–1）、`normalizedX`／`normalizedY`、`nearestSpinner`（最近的轉燈 unitIndex，供跟隨）。

建議的第一批效果（之後依需求增減）：

| effectId | 說明 | 有週期 |
|---|---|---|
| `fixture-solid` | 全部同色（可配合 color/gradient 依高度或 x 軸分佈） | – |
| `fixture-breathing` | 整體呼吸 | ✓ |
| `height-sweep` | 光帶依高度由下往上（或反向）掃過 | ✓ |
| `space-wave` | 波由空間一端（入口）推進到另一端，依 x 位置 | ✓ |
| `fixture-twinkle` | 隨機閃爍（固定種子，前後端一致） | – |
| `fixture-chase` | 依編號／DMX 鏈順序逐顆巡行 | ✓ |
| `follow-spinner` | 取最近一台轉燈當下的平均顏色 | 跟隨轉燈 |
| `off` | 全暗 | – |

- 色彩模型沿用 EffectPreset v2 的 `color`（solid / gradient），`axis` 改為 `height`／`x`／`y`／`index`。
- 天氣：色盤套色同 reference；`cloudy` 依位置錯開；`rain` 有雨天版本（例如短暫下落的亮點，由高往低）。
- 洗牆燈通常較少顆且亮度高：preset 另有 `maxLevel`（0–1）作為群組上限，config 也有 `[hardware].wash_max_level` 安全上限。

### 3.4 Sequence v2

```json
{
  "schemaVersion": 2,
  "ending": { "kind": "loop" },
  "steps": [
    {
      "id": 1,
      "spinner": { "schemaVersion": 2, "effectId": "center-out-sine", "...": "EffectPreset v2" },
      "ball":    { "schemaVersion": 1, "effectId": "height-sweep", "parameters": {}, "color": {} },
      "wash":    { "schemaVersion": 1, "effectId": "fixture-breathing", "parameters": {}, "color": {} },
      "playback": { "kind": "count", "count": 2, "clock": "spinner" },
      "transition": { "kind": "crossfade", "seconds": 1 }
    }
  ]
}
```

- `playback.count` 以哪個群組的週期為準由 `clock` 指定（預設 `spinner`）；其他群組在該段內自由運行。
- `ball`／`wash` 可省略 = 維持上一段（不重播）或 `off`，**待決定**（建議：省略 = `off`，明確比較好預測）。
- 讀到 reference 的 Sequence v1 時自動升級為 v2（`spinner` = 原 preset，`ball`／`wash` = `off`）。
- `fade-black`、`unit-fade-black` 對 DMX 群組：前者整體淡出，後者依 DMX 鏈順序逐顆淡出。

## 4. 輸出迴圈（`output.py`）

每影格：

1. 時鐘、SequenceStore、WeatherStore 處理同 reference §1.5。
2. 轉燈：每台算 LED → `hardware.build_payload`（色序、亮度、馬達通道）→ 依 target 的 universe(s) 送出。
3. DMX：每顆球燈／洗牆燈算一個 RGB → 依 profile 轉成通道值 → 寫入所屬 universe 的 512-byte buffer → 每個 DMX universe 送一包。
4. 單色測試延伸成三個群組：`/api/test/units`（轉燈）、`/api/test/fixtures`（指定 `group` + `id` 或全部）。
5. 結束時：轉燈全暗＋馬達停、DMX universes 全 0（dimmer 0），各送兩次。

封包量估計：24 台轉燈 × 每台 1–N universe + DMX 2–4 universe，40 FPS 下約 1,000–3,000 pps，一般有線網路沒有問題；需要確認 Art-Net node 與轉燈控制器能接受 40 FPS（WLED 可以；部分 DMX node 建議 ≤ 44 FPS）。

## 5. API 變更（其餘照 reference §3.2）

| Method | Path | 變更 |
|---|---|---|
| GET | `/api/state` | 新增 `fixtures: {ball:{count, preset}, wash:{count, preset}}`、`patch`（位置與編號，給前端模擬器） |
| PUT | `/api/preset` | Body 改為 `{"spinner"?, "ball"?, "wash"?}`，只更新給的群組；舊格式（單一 EffectPreset v2）視為 `spinner` |
| GET/PUT | `/api/sequence` | Sequence v2 |
| GET | `/api/playback` | 單色測試時另附 `fixtureColors` |
| GET/PUT/DELETE | `/api/test/fixtures` | `{"group":"ball"\|"wash","colors":[{r,g,b}\|null × count],"seconds":…}`；只在記憶體 |
| GET | `/api/patch` | 全部燈具的 universe／address／位置，給現場對燈 |

## 6. 前端

- 3D 場景改為依 config `position` 擺放：24 台轉燈（球形 POV 幾何，待 §2 確認）、37 顆球燈（發光球體）、洗牆燈（錐形光＋牆面受光示意）、燈桿、牆面（2833 × 953 cm）與說明牌。
- 視角選項：〔單台｜全場｜上視〕；可以分別隱藏三個群組。
- ControlPanel：
  - 「效果」「色彩」分頁加群組切換〔轉燈｜球燈｜洗牆燈〕，各自編 preset。
  - 「串接」每段三列 preset。
  - 「後端」：馬達控制、三群組單色測試、**對燈模式**（點選模擬器裡的某顆 → 實機那顆亮白，其餘全暗，方便現場確認接線與位址）。
- 前端新增 `src/fixtures/`（fixture effect 的 TS 參考實作）與 `scripts/generateFixtureVectors.ts`。

## 7. 待確認清單

| # | 問題 | 影響 |
|---|---|---|
| Q1 | 轉燈實體配置（燈條數、LED 數、共用資料與否、控制器、universe 數、馬達協定）——使用者後續描述 | M4、M6 |
| Q2 | ~~球燈通道表~~ RGBW、37 顆、位址已燒錄（1–37）；通道順序 R G B W，沒有 dimmer／strobe（2026-11-09 確認，與目前範例設定相同）。**仍待確認**：「位址 1–37」是燈具編號（實際起始通道 1, 5, 9, …, 145，目前的預設），還是起始通道本身 | 現場接線前 |
| Q3 | 洗牆燈數量、型號、通道表、安裝位置與照射面（設定格式已支援，確定後只改 config） | M6 |
| Q4 | Art-Net → DMX node 型號、台數、port 數；球燈 DMX 鏈如何分段（`DMX＿線間距.pdf` 的線長是否就是串接順序 1→37） | M3、`--patch-report` |
| Q5 | 所有燈桿與燈具的平面座標（x, y）：能否提供 CAD/DXF 匯出，或由 `image (6).png` 量 | M6 模擬器 |
| Q6 | 主機平台：Raspberry Pi 5（reference）或 Windows（`moon_observe_re`）？ | M8 部署、效能 |
| Q7 | Art-Net 網段與 IP 規劃（轉燈、DMX node、主機） | config 範本 |
| Q8 | 是否仍要天氣功能、造訪地點清單 | M5 |
| Q9 | 球燈／洗牆燈是否需要與轉燈「跟隨」連動，或主要獨立編排 | 效果優先順序 |
| Q10 | ~~建立 git repo、`doc/` 圖檔是否公開~~ 已決定：repo `git@github.com:whyixd/tph_art_museum.git`（public），`doc/` 圖檔一併公開 | – |

## 8. 里程碑

| # | 內容 | 完成條件 | 依賴 |
|---|---|---|---|
| M1 ✅ | 建 repo；複製 moon_observe backend/frontend；`uv sync --locked`、`npm ci` | 原有 backend、frontend 測試全部通過；fixture 重產逐 byte 相同（2026-11-03：來源 `moon_observe@b736485`（v0.1.12+1）以 `git archive` 匯出；backend 86、frontend 117 個測試通過；`tsc` + build 通過；`npx vite-node scripts/generateBackendVectors.ts` 的輸出與原檔逐 byte 相同（注意：不加 `--write` 只會印到 stdout，不會寫檔）） | – |
| M2 ✅ | 通用化：`pixels`、`unit_count`、多 universe、`hardware.py`；效能基準測試 | 2026-11-03：見下方「M2 實作細節」；backend 96 個測試（新增 `test_hardware.py` 10 個）、frontend 117 個測試通過，`tsc` 通過；fixtures 加入 24 台的整台效果（`unit-chase`、`unit-random`、`roulette-ring`、`sequential-center-fill`、`random-fill`），前後端一致；24 台 × 312 顆實際送到本機 UDP listener 2 秒：48 個 universe、每個 79 包（≈ 40 FPS）、封包 510／428 bytes；效能結果見 §2 | – |
| M3 ✅ | DMX：fixture profile、patch 解析與驗證、`dmx.py`（RGB → 通道、universe buffer）、`--patch-report` | 2026-11-03：見 §3.2；新增 `test_dmx.py` 7 個測試，backend 共 103 個測試通過；`config.example.toml` 加入球燈範例。**尚未接進輸出迴圈**（M6） | – |
| M4 | 轉燈幾何與 payload 依實體配置實作 | payload golden test；現場一台實測 | Q1 |
| M5 | Fixture effect engine（TS + Python）、Sequence v2、天氣套用到 DMX 群組 | 前後端 fixture vectors 一致；v1 → v2 升級測試 | M3 |
| M6 | `output.py` 整合三群組；結束流程；`/api/test/fixtures`、`/api/patch` | 本機 UDP listener 驗證每個 universe 封包、結束時全 0 | M3–M5 |
| M7 | 前端：全場 3D、群組編輯、對燈模式 | `npm test`、`tsc` 通過；Playwright 截圖 | M5、Q5 |
| M8 | 部署（Pi systemd 或 Windows WinSW）與現場 checklist | 安裝 → 升級 → 回退 → 停止時燈全暗、馬達停 | Q6、Q7 |
| M9 | 更新 `doc/system-reference.md` 為本系統版本 | 文件與程式一致 | 全部 |

### M2 實作細節

- `render.pixels` 1..2048（預設 156）；`render.unit_count` 1..64（預設 9）；參數 `litCount` 等效果上限暫不動（依 LED 數調整留到 M4）。
- 新增選填 `[hardware]`：`color_order`（預設 RGB）、`brightness`（0..1，預設 1）、`channels_per_universe`（3 的倍數、≤ 510，預設 510）。
- 新增選填 `[motor]`：`channel`（預設 `pixels × 3 + 1`，必須在 LED 通道之後）；指令維持 reference 的 0（緩停）／255（緩啟動）與 `PUT /api/motor {"enabled"}`。
- 每台是一條邏輯通道串（LED 依色序、再接馬達通道），依 `channels_per_universe` 切成**連續的 universe**（WLED 慣例），從 target 的 `universe` 開始；一顆 LED 不會跨 universe；每包補到偶數長度；每台最多 8 個 universe。
- 同一 IP 上各台用到的 universe 不可重疊（錯誤訊息會提示「連續 universe」）。
- 不寫 `[hardware]`、`[motor]` 時，封包與 reference 逐 byte 相同（一包 470 bytes，ch469 馬達），原有測試全部不改照樣通過。
- `GET /api/state`：`motor.channel` 改讀設定；`output` 新增 `pixels`；`output.targets[]` 多了 `universes`。
- 程式結束時，若馬達為 255，保留最後畫面、對每台每個 universe 再送一次馬達 0（同 reference）。
- 前端尚未修改（仍是 1×9 排列、156 顆），留到 M7。
