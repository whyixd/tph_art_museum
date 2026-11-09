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

## 2. 轉燈（spinner）

reference 是「每組一條半圓 156 顆，兩條手臂共用資料」。

**已確認（2026-11-09）**：每台 **3 條燈條、每條 129 顆**，每條沿垂直圓周繞一整圈，所以 top view 看到 6 條半圓（= `裝置specs.pdf` 的 6 條放射狀燈條）。馬達控制沿用 moon_observe：Art-Net **ch469**，0 = 緩停、255 = 緩啟動（`firmware/wled/` usermod → IO13 → SAMD21 D8）。

**3 條並聯、共用同一份資料**；每條**第 0 顆在最上方，往下繞一整圈**回到頂部，圓周上**沒有空缺**（2026-11-09 確認）。假設 129 顆沿整圈等距：第 i 顆與頂點夾角 θᵢ = i × 360°/129，最底部落在第 64、65 顆之間，第 128 顆與第 0 顆相鄰。

repo 現有的 `firmware/wled/unit_settings.json` 正好是 WLED 一路輸出（IO16）、129 顆、GRB（`order 0`）、Art-Net universe 0、Multi RGB、起始位址 1，不用改。對應的 backend 設定：

```toml
[render]
pixels = 129        # 1–387 = LED（WLED 自己處理 GRB，backend 照送 RGB）

[motor]
channel = 469       # 必須明寫：預設會是 129×3+1 = 388；usermod 只認第一個 universe、長度 ≥ 469 的 ch469
```

→ 一台一個 universe、一包 470 bytes（388–468 補 0），與 reference 的封包長度相同。M2 的程式已支援，不用改。

**幾何（2026-11-09 決定 B：效果中心在頂部）**：現有效果都依索引計算（`normalizedStripPosition = v/128`、`normalizedRadius = |2·strip − 1|`，許多效果依 `2/(N−1)` 的像素步長量化）。為了不動任何效果與 fixtures，M4 改在**輸出時旋轉 LED 順序**：

- `[hardware] pixel_rotation = 64`：實體第 i 顆顯示效果的第 v = (i + 64) mod 129 顆。
- 效果第 64 顆（`radius` 0，reference 半圓的頂點）→ 實體第 0 顆（頂部）；效果兩端第 0、128 顆（`radius` 1，reference 的兩腳）→ 實體第 65、64 顆（底部兩側）。
- 效果 v < 64 是圓的一側、v > 64 是另一側，與 reference 兩條手臂的拓撲相同（`world-upper-half` 依 `0.5 − strip` 分邊仍成立）。
- 預設 0；不寫時封包與 reference 逐 byte 相同。`GET /api/state` 的 `output.pixelRotation` 提供給前端（M7 模擬器）。
- 之後若要依球面高度編排，可再加 `normalizedHeight = (1 + cos θᵢ)/2`（θᵢ 為實體位置），目前未做。

效能：每台只算 129 顆（比 reference 的 156 少），依 §2 基準約為 156 顆那欄的 83%；Pi 5 + PyPy 可行、CPython 仍不夠。

**仍待確認**：亮度上限、需不需要類似「月亮校正」的位置亮度補償；RPM、旋轉方向（模擬器用）。

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
- 燈具在場景中的座標放在另一份 layout 檔（M5 已實作，見 §3.3），不放在 patch 裡。

### 3.3 Fixture effect engine（M5 已實作）

純函式，與轉燈效果一樣以影格為時間；前端 `frontend/src/fixtures/index.ts` 為參考實作，後端 `fixture_effects.py` 逐值一致（`scripts/generateFixtureVectors.ts` → `backend/fixtures/fixtures_v1.json`）。

```
sampleFixture(preset, context, raining) -> RGB（0–1）    # 顏色 × 亮度 × maxLevel
renderFixtureFrame(preset, geometry, frame, {fps, raining, palette, frameStep, cycleFrames}) -> RGB[]
```

`FixtureContext`：`controllerFrame`、`controllerFps`、`fixtureIndex`／`fixtureCount`（依編號 = DMX 鏈順序）、`normalizedHeight`／`normalizedX`／`normalizedY`（群組內 0–1）。

**FixturePreset v1**：`{schemaVersion: 1, effectId, parameters, color, maxLevel?}`；`color` 同 EffectPreset 的 solid／gradient（2–8 色、可 `weatherPalette: false`），漸層 `axis` 為 `height`／`x`／`y`／`index`；`maxLevel`（0–1）是群組亮度上限。

| effectId | 說明 | 參數（預設） | 週期 |
|---|---|---|---|
| `off` | 全暗（雨天也不亮） | – | – |
| `fixture-solid` | 恆亮 | – | – |
| `fixture-breathing` | 整體呼吸，最暗保留 `minLevel` | `breatheHz` 0.2、`minLevel` 0.1 | `ceil(fps/breatheHz)` |
| `height-sweep` | 光帶依高度由下往上（`direction` 1：由上往下）掃過 | `speedHz` 0.25、`width` 0.3、`direction` 0 | `ceil(fps/speedHz)` |
| `space-wave` | 亮度波沿 x 推進 | `speedHz` 0.2、`waveCount` 1、`direction` 0 | `ceil(fps/speedHz)` |
| `fixture-twinkle` | 每顆各自隨機亮起並淡出（固定種子） | `holdSeconds` 1、`density` 0.3 | – |
| `fixture-chase` | 依編號逐顆巡行，拖 `tailCount` 顆漸暗 | `stepSeconds` 0.3、`tailCount` 2、`direction` 0 | 每步影格 × 顆數 |

- `follow-spinner`（跟隨最近的轉燈）**未做**：要等 Q9（是否需要連動）與 Q5（座標），且需要輸出迴圈先算好轉燈顏色（M6）。
- **幾何／layout**：`[dmx] layout = "layout.json"`（選填）。格式 `{"schemaVersion": 1, "fixtures": {"ball": {"1": {"x":…, "y":…, "z":…}}, "wash": {…}}}`，單位 cm，x／y／z 都可缺；群組內每顆都有某軸時才依該軸正規化到 0–1，否則該軸改依編號排列。設定載入後放在 `config["dmx"]["geometry"][group]`。
- `backend/layout.example.json`：37 顆球燈的高度 z，取自 `DMX＿線間距.pdf`（依圖面比例換算後與 H 標籤配對、再以紅字線長交叉檢查）；x／y 待 Q5。**請確認高度**。
- **天氣**：色盤只換顏色，漸層效果保留自己的 axis，純色效果改依 `height` 分佈；`cloudy` 每顆依 x 錯開 `round(x × 8 × cloud_step)` 影格；`rain` 每顆在自己的隨機相位閃亮後衰減（0.7 Hz、閃亮佔 25%、其餘保留 20% 亮度），`off` 除外。常數在兩邊同名（`FIXTURE_RAIN`／`RAIN_*`、`CLOUD_SPREAD_STEPS`）。
- 洗牆燈另需 config 安全上限 `[hardware].wash_max_level`：留到 M6 接輸出時做。

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

M5 已實作（`sequence.py`）：
- `spinner` 必填；`ball`／`wash` 選填，**省略 = 全暗**（採用建議）。轉燈要暗可用 `full-on` 配黑色。
- `playback.count` 可加 `clock`（`spinner`／`ball`／`wash`，預設 `spinner`），以該群組效果的週期計次；只有 clock 群組每次重播時回到第 0 影格，其他群組在段內自由運行。clock 群組必須有 preset 且有週期，否則報錯。`fixture-chase` 的週期依顆數，`compile_sequence`／`SequenceStore` 接受 `fixture_counts`（M6 由 config 傳入）。
- 讀到 Sequence v1 時升級為 v2（`spinner` = 原 preset，無 `ball`／`wash`）；`SequenceStore` 讀到 v1 檔不會改寫，下次儲存才寫成 v2。`GET /api/sequence` 一律回傳 v2。
- 轉場對 DMX 群組：`crossfade`、`fade-black` 與轉燈相同權重；`unit-fade-black` 依編號（DMX 鏈順序）逐顆淡出。
- 前端在 `backendApi.ts` 轉換：讀入 v2 時轉成內部 v1 形狀（`step.preset` = 轉燈，`step.fixtures` 原樣保留 ball／wash），儲存時轉回 v2；現有編輯器只編轉燈，球燈／洗牆燈設定不會遺失（編輯 UI 在 M7）。前端串接預覽仍以轉燈週期計次，`clock` 只在後端生效。

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
| Q1 | ~~轉燈實體配置~~ 每台 3 條 × 129 顆、各繞垂直圓周一整圈（6 條半圓）；馬達沿用 moon_observe ch469（2026-11-09 確認）。3 條並聯共用資料、第 0 顆在頂部往下繞一圈、無空缺。效果中心在頂部（`pixel_rotation = 64`，見 §2） | M4、M6 |
| Q2 | ~~球燈通道表~~ RGBW、37 顆、位址已燒錄（1–37）；通道順序 R G B W，沒有 dimmer／strobe（2026-11-09 確認，與目前範例設定相同）。**仍待確認**：「位址 1–37」是燈具編號（實際起始通道 1, 5, 9, …, 145，目前的預設），還是起始通道本身 | 現場接線前 |
| Q3 | 洗牆燈數量、型號、通道表、安裝位置與照射面（設定格式已支援，確定後只改 config） | M6 |
| Q4 | Art-Net → DMX node 型號、台數、port 數；球燈 DMX 鏈如何分段（`DMX＿線間距.pdf` 的線長是否就是串接順序 1→37） | M3、`--patch-report` |
| Q5 | 所有燈桿與燈具的平面座標（x, y）：能否提供 CAD/DXF 匯出，或由 `image (6).png` 量。球燈高度 z 已由 `DMX＿線間距.pdf` 讀出（`backend/layout.example.json`），**待確認** | M6 模擬器、`space-wave` |
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
| M4 🔶 | 轉燈幾何與 payload 依實體配置實作 | payload golden test；現場一台實測。2026-11-09：程式與測試完成（見下方「M4 實作細節」），**待現場一台實測** | Q1 |
| M5 ✅ | Fixture effect engine（TS + Python）、Sequence v2、天氣套用到 DMX 群組 | 前後端 fixture vectors 一致；v1 → v2 升級測試。2026-11-09：見 §3.3、§3.4；backend 127 個測試（新增 `test_fixture_effects.py` 9、`test_sequence_v2.py` 8、`test_dmx.py` layout 3）、frontend 122 個測試（新增 `fixtures.test.ts` 4、`backendApi.test.ts` 1）通過，`tsc`、build 通過；轉燈 `effects_v2.json` 重產逐 byte 相同。**尚未接進輸出迴圈與 API**（M6） | M3 |
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

### M4 實作細節

- `hardware.physical_order()` 與 `build_packets` 依 `pixel_rotation` 重排；`config.py` 驗證 0 ≤ rotation < pixels；`/api/state` 新增 `output.pixelRotation`。
- `config.example.toml` 改為美術館轉燈：`pixels = 129`、`unit_count = 24`、`[hardware] pixel_rotation = 64`、`[motor] channel = 469`（必須明寫，否則預設 388）。WLED 的 `firmware/wled/unit_settings.json`（129 顆、universe 0）不用改；色序 GRB 由 WLED 處理，backend 照送 RGB。
- `test_hardware.py` 新增 4 個測試（backend 共 107 個通過）：golden payload（470 bytes、LED 0 = 效果第 64 顆、LED 64/65 = 效果兩端、ch388–468 為 0、ch469 馬達）；依 `radius` 紅→藍漸層渲染後，頂部全紅、底部兩顆全藍、圓的兩側左右對稱；範例設定可載入；`pixel_rotation` 越界報錯。
- 本機實送：範例設定 → 127.0.0.1，2 秒 80 包（40 FPS）、ArtDmx 長度 470，LED 0 = (255,0,0)、LED 32 = (128,0,128)、LED 64/65 = (0,0,255)、ch469 = 0。
- 效果參數上限（`litCount` 等）在 backend 已依 `pixels`（129）驗證；**前端 ControlPanel 仍以 156 顆計算上限**，可能允許 backend 會拒絕的值，留到 M7 改讀 `output.pixels`。

**現場一台實測步驟**：
1. 該台 WLED 依 `firmware/wled/` 刷機並套用 `unit_settings.json`（129 顆、universe 0）。
2. backend 用範例設定（填入該台 IP），`PUT /api/preset` 送方向測試：
   `{"schemaVersion":2,"effectId":"full-on","parameters":{},"color":{"mode":"gradient","axis":"radius","colors":[{"r":1,"g":0,"b":0},{"r":0,"g":0,"b":1}]}}`
3. 馬達停止時確認：3 條燈條都由**頂部紅**漸變到**底部藍**、左右兩側對稱、整圈沒有錯位或斷點。若紅色在底部，表示第 0 顆不在頂部（改 `pixel_rotation`）；若兩側不對稱，表示繞行起點有偏移。
4. `PUT /api/motor {"enabled":true}` 確認馬達緩啟動，`false` 緩停（約 10 秒）；結束 backend 時馬達收到 0。
