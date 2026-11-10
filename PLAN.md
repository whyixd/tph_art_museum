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
| `fixture-firework` | 煙火蔓延：最低一顆先炸開（全亮 `flickerFrames` 影格），依高度晚 `round(height × spread)` 影格，之後以 `hash01(frame // flickerFrames, index, 31) < density` 隨機閃爍、亮度 `(1 - t/burst)²`；搭配轉燈 `firework-rise` | `spreadSeconds` 2、`burstSeconds` 2.5、`gapSeconds` 0.5、`density` 0.6、`flickerFrames` 2 | 蔓延＋持續＋全暗影格 |
| `fixture-rise` | 由低往高點亮：依高度晚 `round(height × rise)` 影格開始，`fadeInSeconds` 內以 smoothstep 淡入；`repeat` 0 亮完保持，1 則停留、淡出、全暗後重來 | `riseSeconds` 10、`fadeInSeconds` 3、`repeat` 0、`holdSeconds` 5、`fadeOutSeconds` 3、`endDarkSeconds` 1 | `repeat` 1 時為各段影格總和，否則無 |
| `pole-rise` | 每根柱子由下往上：每顆依（柱子錯開順序 × `staggerSeconds` ＋ 在柱子上由低到高的順序 × `stepSeconds`）開始，`attackSeconds` 內亮起、`decaySeconds` 內以平方曲線淡出；`order` 0 依柱子順序、1 每輪固定種子隨機（`hash01(round, pole, 43)`）；柱子依 `config.fixture_poles`／前端 `fixturePoles` | `stepSeconds` 0.2、`attackSeconds` 0.1、`decaySeconds` 2.5、`staggerSeconds` 0.3、`order` 0、`periodSeconds` 8 | `periodSeconds` 影格 |
| `base-wave` | 基本亮度＋正弦波：`baseLevel + (1 − baseLevel) × depth × (0.5 + 0.5 cos 2π(waveCount × pos − speedHz × t))`，pos 依 `waveAxis`（0 高度、1 x、2 y、3 編號），`direction` 1 反向；depth 0 = 只有基本亮度 | `baseLevel` 0.3、`depth` 0.7、`speedHz` 0.3、`waveCount` 1、`waveAxis` 0、`direction` 0 | `ceil(fps/speedHz)` |
| `horizontal-flow` | 水平流動：相位 = `waveCount × (x cos θ + y sin θ) − 流過圈數`（流速在 `rampSeconds` 內由 `speedHz` 線性加到 `speedHz × acceleration` 後維持，圈數為其積分）（x、y 為群組內 0–1，θ = `directionDegrees`）；漸層以循環方式依相位取色（不看漸層方向），亮度 `1 − depth × (0.5 − 0.5 cos 2π相位)` | `speedHz` 0.2、`waveCount` 1、`directionDegrees` 0、`depth` 0.5、`acceleration` 1、`rampSeconds` 10 | `ceil(fps/speedHz)`；加速時為 `rampSeconds` 影格 |

- `follow-spinner`（跟隨最近的轉燈）**未做**：要等 Q9（是否需要連動）與 Q5（座標），且需要輸出迴圈先算好轉燈顏色（M6）。
- **幾何／layout**：`[render] layout = "layout.json"`（選填；2026-11-10 由 `[dmx]` 移到 `[render]`，因為也放轉燈）。格式 `{"schemaVersion": 1, "spinners": {"1": {"x","y","z"}…}, "fixtures": {"ball": {"1": {…}}, "wash": {…}}}`，單位 cm，x／y／z 都可缺。原點 = 左牆內側線 × 前牆（上視圖下方、說明牌 A 那側）內側線，x 沿長牆往右、y 往後牆、z 離地；轉燈 z 為中心高度。轉燈編號 1–64，超過 `unit_count` 的忽略；沒有 `[dmx]` 時忽略 `fixtures`。球燈群組內每顆都有某軸時才依該軸正規化到 0–1，否則該軸改依編號排列（`config["dmx"]["geometry"]`）；轉燈座標在 `config["spinner_positions"]`，`GET /api/patch` 一併回傳。
- `backend/layout.example.json`：24 台轉燈與 37 顆球燈的 x／y／z（見 Q5）。
- **天氣**：色盤只換顏色，漸層效果保留自己的 axis，純色效果改依 `height` 分佈；`cloudy` 每顆依 x 錯開 `round(x × 8 × cloud_step)` 影格；`rain` 每顆在自己的隨機相位閃亮後衰減（0.7 Hz、閃亮佔 25%、其餘保留 20% 亮度），`off` 除外。常數在兩邊同名（`FIXTURE_RAIN`／`RAIN_*`、`CLOUD_SPREAD_STEPS`）。
- 洗牆燈的亮度安全上限：用 DMX profile 既有的 `brightness`（送出前套用，M3 已實作），不另設 `wash_max_level`。

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
- 轉場對 DMX 群組：`crossfade`、`fade-black` 與轉燈相同權重；`unit-fade-black` 依編號（DMX 鏈順序）逐顆淡出；`unit-breathe-black`（逐台呼吸後淡出）同樣依編號逐顆呼吸後淡出。
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
| Q4 | Art-Net → DMX node 型號、台數、port 數；球燈 DMX 鏈如何分段。`新北美_ip控箱.pdf`：12 個控箱各一個 node（2.0.0.101–112），各自帶一組球燈（例：105 → 1–4、101 → 5–7、109 → 24、27–29）。2026-11-10 已寫進 `config.example.toml`：11 個 node（控箱 104 沒有球燈，未列入）、universe 0，球燈位址沿用編號 4(n−1)+1（見 Q2） | M3、`--patch-report` |
| Q5 | ~~平面座標~~ 2026-11-10 由 `doc/spaces05.png` 紅框內的上視圖＋側視圖交叉比對建立（`backend/layout.example.json`，標註圖 `doc/spaces05_annotated_top.png`／`_side.png`）。比例 1.836 cm/px（37 顆球燈高度擬合，RMS 6 cm；長牆間距量得 960 vs 標示 953）；兩圖 x 對齊誤差 < 1 px；燈桿間距與 `新北美_ip控箱.pdf` 尺寸相差 ≤ 12 cm。球燈高度已確認（14 號 360、26 號 750）；同桿前後兩顆球燈依側視圖遮擋判斷；同桿前後兩台轉燈由使用者確認（較低的在前側）。B22 y = 6 cm（高於圍牆，可）。轉燈 3、10、11、13、14、18、21 號沒有 H 標籤，中心高度 = 頂端 − 35 cm 估計 | M7 模擬器、`space-wave` |
| Q6 | ~~主機平台~~ **Raspberry Pi 5**（2026-11-10 確認）。效能（`benchmark_render.py`，24 台 × 129 顆、40 FPS、預算 25 ms，Apple M4 實測）：CPython 最慢 12.6 ms（`stardust-cloud`，預算 50%）、PyPy 最慢 3.3 ms（`roulette-ring`，13%），一般效果 0.5–1.6 ms。Pi 5 單核心約慢 3–4 倍 → **CPython 約 38–50 ms，不夠；PyPy 一般效果約 2–6 ms、最慢約 10–13 ms，可行**（轉場時兩層約 ×2，`roulette-ring` 轉場接近預算）。M8 部署需改用 PyPy，並在 Pi 5 實測 | M8 部署、效能 |
| Q7 | Art-Net 網段與 IP 規劃。`新北美_ip控箱.pdf`：轉燈 2.0.0.201–224、DMX node 2.0.0.101–112。**暫定**轉燈 unit = IP − 200；每個控箱兩台中 IP 靠前的是較低那台，控箱 104 = 單獨燈桿那台 + 球燈 17–20 燈桿上最高那台（2026-11-10 使用者暫定）。2026-11-10 已寫進 `config.example.toml`（24 個 `[[artnet.targets]]`，universe 0；`fps` 由 60 改為 40）。主機在 2.0.0.0/8 網段的 IP（`source_ip`）未定 | config 範本 |
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
| M6 ✅ | `output.py` 整合三群組；結束流程；`/api/test/fixtures`、`/api/patch` | 本機 UDP listener 驗證每個 universe 封包、結束時全 0。2026-11-09：見下方「M6 實作細節」 | M3–M5 |
| M7 ✅ | 前端：全場 3D、群組編輯、對燈模式 | `npm test`、`tsc` 通過；Playwright 截圖。2026-11-10：見下方「M7 實作細節」 | M5、Q5 |
| M8 🔶 | 部署（Raspberry Pi 5、systemd、**PyPy**）與現場 checklist | 安裝 → 升級 → 回退 → 停止時燈全暗、馬達停；Pi 5 上重跑 `benchmark_render.py`。2026-11-10：程式、腳本與文件完成（見下方「M8 實作細節」），**待 Pi 5 實機安裝與 `doc/site-checklist.md`** | Q6 ✅、Q7 |
| M9 | 更新 `doc/system-reference.md` 為本系統版本 | 文件與程式一致 | 全部 |
| M10 🔶 | 音訊播放與同步校準（§9） | Pi 從 USB 音效卡播放 WAV；燈光時間跟著音訊時鐘；節拍器＋閃拍測試；前端延遲校準可儲存；拍點分析腳本 | – |
| M11 | 拍點資料與拍點效果（§9） | 效果 context 加入拍點資訊；新增拍點效果；前後端一致性向量 | M10 |
| M12 🔶 | 演出時間軸（Show）取代 Sequence 作為表演（§9.4） | 段落以秒／小節定位、轉場對拍；後端依音訊時鐘播放。2026-11-10：先於 M11 實作（拍點效果之後再補），程式與測試完成，**待 Pi 實機驗證** | M10 |
| M13 | 排程、待機效果、風力（§9） | 每日表演時段；日期 → 曲目／演出；其餘時段待機效果受風力影響；天氣分類、色盤、造訪移除 | M12 |
| M14 🔶 | 前端時間軸編輯器與拍點修正工具（§9.5） | 波形、拍點標記可增刪移動、瀏覽器試聽同步預覽。2026-11-10：先於 M11、M13 實作，程式與測試完成，**待 Pi 實機驗證與環境曲實際修正** | M12 |
| M15 | 清理：刪除不用的效果（含 `roulette-ring`）、舊串接與天氣程式；文件 | 測試與文件一致 | M12–M14 |

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

### M6 實作細節

- `output.stream`：每個 DMX node 一個 client（universe、512 bytes），每影格送完轉燈後送 DMX。球燈／洗牆燈顏色：串接模式取該段 `ball`／`wash`（`render_sequence_fixtures`），單一效果模式取 `PresetStore` 的群組 preset（沒有 = 全暗）；天氣同轉燈（`rain`、色盤、`cloudy` 依 x 錯開）；`/api/test/fixtures` 的單色測試覆蓋指定燈具。
- 結束：先停轉燈馬達（同 reference），再對每個 DMX universe 送 2 次全 0（dimmer 等固定通道也是 0）；DMX 送出失敗時馬達停止仍會先完成（有測試）。
- 單一效果文件（`preset.json`、`PUT /api/preset`）：舊格式 = 只有轉燈；`{"spinner"?, "ball"?, "wash"?}` 只換給的群組，`null` 關閉。檔案沒有球燈／洗牆燈 preset 時仍寫成舊格式。
- 沒有 `sequence.json` 時自動建立的預設段落也帶入 `preset.json` 的球燈／洗牆燈 preset（實測時發現：原本只帶轉燈，DMX 全暗）。
- API：`GET /api/state` 新增 `fixtures {ball,wash: {count, preset}}`；`GET/PUT/DELETE /api/test/fixtures`；`GET /api/patch`；`GET /api/playback` 在測試中另附 `fixtureColors`。`--check` 與啟動訊息會列出 DMX 燈具數與 universe 數。
- `[hardware].wash_max_level` 不做：洗牆燈上限用 profile `brightness`。
- 測試：`test_output_dmx.py` 7 個 + `test_sequence_v2.py` 1 個，backend 共 135 個通過。
- 本機實送（真的 stupidArtnet → 127.0.0.1）：轉燈 universe 77 共 80 包 × 470 bytes；球燈 universe 78、洗牆燈 79 各 82 包 × 512 bytes（80 影格 + 結束 2 包）；`height-sweep` 第 40 影格亮的是高度 264–500 cm 的球燈；洗牆燈 dimmer 255 + 綠；最後 2 包全 0。
- 前端尚未使用這些 API（M7）。

### M7 實作細節

- **3D**（`SiteScene.tsx` 取代 `LightTrail`／半圓燈）：視角〔單台｜全場｜上視〕取代〔單組｜1×9〕；轉燈為 3 條整圈燈條（相隔 60°、頂部起繞、`pixelRotation` 對應效果像素），含旋轉殘影；球燈為發光球（暗時仍顯示外殼）、洗牆燈為錐體；地板只鋪在房間內（前牆 y=0、後牆 953 cm）。轉燈／球燈／洗牆燈可分別隱藏。效果顏色以「台 × 控制器影格」快取；轉燈與燈具共用一個場景時鐘（暫停、重設、跟隨後端影格）。預設 32 個時間取樣、各台不錯開（與後端相同，模擬用的 Effect frame step 仍可調）。
- **座標**：`src/site/layout.ts` 讀 `backend/layout.example.json`（Vite `server.fs.allow`）；連上後端時改用 `/api/patch` 的 `spinnerPositions` 與燈具座標，後端沒有座標時用範例。LED 數、`pixelRotation`、燈具數讀 `/api/state`。
- **編輯**：「效果」「色彩」分頁加〔轉燈｜球燈｜洗牆燈〕；球燈／洗牆燈效果、參數（方向為正向／反向）、亮度上限、純色／漸層（依高度／x／y／編號）、天氣色盤開關；「關閉」= 沒有 preset。單一效果模式「套用到後端」送 `{spinner, ball, wash}`（舊後端只送轉燈）。串接每段可編輯球燈／洗牆燈，段落清單顯示其效果；「播放方式」可依時間或依轉燈／球燈／洗牆燈次數（`playback.clock`，前端 `compileSequence` 與後端相同規則，fixture-chase 依燈具數）。
- **後端鏡像**：後端模式 3D 顯示後端的球燈／洗牆燈 preset 與 `/api/playback` 的 `fixtureColors`。
- **對燈模式**（`FixtureTestPanel`，「後端」分頁）：3D 點選或清單（←／→）選一顆；「只亮這顆」→ 實機只有那顆亮白、其他全暗，之後換選會跟著換亮；「全部亮白」、「結束測試」；最多 30 分鐘自動恢復。顯示該顆的 node／IP／universe／通道／座標（`/api/patch`）。
- 後端：`/api/patch` 新增 `spinnerPositions`（全部 unit，含沒有 target 的）。
- 參數上限、`各燈台不同數值` 改依實際 LED 數（129）與台數（24）。
- 驗證：frontend 124 個測試（新增 `site.test.ts` 8，移除 1×9 排列相關測試）、`tsc`、build 通過；backend 136 個測試通過。Playwright（`--api-only` + 37 球燈／2 洗牆燈 + layout）擷取全場、上視、單台、球燈編輯、串接、對燈面板，無頁面錯誤；對燈「只亮這顆」後 `/api/test/fixtures` 為只有 5 號亮白。
- **燈桿**（2026-11-10 追加）：由 `doc/spaces05.png` 量出 15 根有掛燈的燈桿。x 取側視圖的燈桿線，y 取上視圖的燈桿符號（中心白色的小圓或方框），頂端高度取側視圖燈桿線的頂端（允許被前方燈具遮住的空隙）。量得 504、603、652、807、1008、1310、1512 cm，正好是 5、6、6.5、8、10、13、15 m 乘 1.008（和長牆 960 vs 953 是同一個 0.8% 比例誤差），所以取標準長度。風力感測器桿（x 約 3166）和 x 約 4219 的桿子沒有燈，未列入。layout 新增 `poles: [{x, y, z(頂端)}]`，後端驗證並由 `/api/patch` 回傳，只供前端繪製。3D 在「骨架」開啟時畫燈桿（直徑 20 cm）和懸臂：每台轉燈（接在頂端）、每顆球燈或洗牆燈（接在中心）連到平面 1.2 m 內最近的燈桿；範例 layout 的 24 台轉燈和 37 顆球燈都找得到燈桿。上視改用 12° 窄視角並拉遠（接近正投影，燈桿不會被透視拉成放射線），近平面加大避免地板格線閃爍。
- 未做：`/3d.html` 仍是 moon_observe 的半圓燈測試頁；說明牌未繪製；`follow-spinner` 效果（Q9）。

### 2026-11-10：現場 IP 與預設參數

- `config.example.toml` 改為現場：24 台轉燈 2.0.0.201–224、11 個 DMX node 2.0.0.101–112（各帶該控箱的球燈）、`fps = 40`。測試檢查每台 IP、每顆球燈所在 node 與位址。
- 修正：`periodic-leds` 的預設（亮 12、間隔 12）需要 143 顆，129 顆放不下，後端會拒絕。前端 `createDefaultEffectPreset(id, ledCount)`、後端 `default_preset(id, led_count)` 會依燈數夾住（129 顆：間隔 12 → 10）；不給燈數時與 reference 相同，一致性向量不變。前端選效果時與單一效果的預設都套用；`benchmark_render.py` 也改用。

### M8 實作細節

- **打包**：`build_release.py` 建置前端時一併匯出 `backend/layout.example.json`（M7 起前端會匯入它，原本只匯出 `frontend/` 會建置失敗）；以 `HEAD` 實際建置驗證。release 內含 `layout.example.json`。
- **PyPy**：installer 以 `uv sync --python pypy@3.12`（`TPH_ART_MUSEUM_PYTHON` 可改）建 `.venv`，並確認環境真的是 PyPy。開發機以同一份 `uv.lock` 建 PyPy 環境，backend 全部測試通過（CPython 36 s → PyPy 15 s）。
- **repo 與 token**：預設 `whyixd/tph_art_museum`；公開 repo 不需要 token（bootstrap 詢問時 Enter 略過，installer 沒有 token 就不帶認證）。
- **首次安裝**：seed `config.toml` 時啟用 `render.layout = "layout.json"`，並 seed `/etc/tph-art-museum/layout.json`。測試以 installer 自己的 `sed` 規則產生設定並實際載入。
- **輸出統計**：`OutputStats`（每 60 秒：影格、跳格、每格渲染＋送出平均／最長、預算），log 一行、`GET /api/output`、前端「後端」分頁顯示。
- **效能**：
  - 量測（Apple M4、PyPy、24 台＋11 個 DMX universe 送到本機）：實際迴圈每格平均 5.9 ms；同一份程式全速跑只要 0.8 ms，加上 40 FPS 的 sleep 節奏就變成約 7 倍 → 是 CPU 降頻／排到省電核心，不是程式。對策：service 啟動前把 `scaling_governor` 設為 `performance`（`ExecStartPre=+`，`$$g` 避免 systemd 展開；需主動散熱器）。Pi 5 上以 log 的輸出統計確認。
  - 畫面相同的轉燈只渲染一次：`effects.UNIT_INDEPENDENT_EFFECTS`（12 個不讀燈台編號／相位的效果）在沒有雨、沒有各台錯開、顏色不依燈台、沒有 unitVariation 時，每格共用一次渲染。`test_unit_sharing.py` 逐台渲染確認名單內效果 24 台完全相同、名單外效果確實不同，並比對輸出迴圈的封包與逐台各算逐 byte 相同。
  - `_virtual_height` 只在 `world-upper-half` 計算。
- **韌體測試**：從 moon_observe 補搬 `firmware/tests/`（`test_flash.py` 40 個通過；`moon_motor_logic_host`、`motor_control_io_host` 通過；`motor_pwm_test_host` 在 moon_observe 原本就失敗：測試預期 PWM 每 20 ms +5，sketch 是每 80 ms +1，待決定）與 `firmware/docs/`。
- **文件**：`backend/deploy/README.md` 改為本案（Pi 5、PyPy、governor、網路兩種接法與 nmcli、輸出統計）；新增 `doc/site-checklist.md`（網路、每台轉燈、編號對位置、方向測試、球燈對燈、服務、停止／重開機／拔線、升級回退、待回填）。
- 2026-11-10：服務、帳號、路徑、release 檔名改為 `tph-art-museum`（`/opt/tph-art-museum`、`/etc/tph-art-museum`、`tph-art-museum.service`、`tph-art-museum-<版本>.tar.gz`），環境變數前綴 `TPH_ART_MUSEUM_`；Python 套件名仍是 `moon_observe_backend`。


## 9. 音樂演出（2026-11-10 方向調整）

效果改為配合音樂：拍點效果，以及以音樂時間軸串接。原本的效果先保留（M15 再刪）；`roulette-ring`（輪盤收束）確定不採用，它在 Pi 5 上跳格的問題擱置。

### 9.1 決策

| # | 決策 |
|---|---|
| 1 | **Pi 自己播放音樂**，接 USB 音效卡。燈光的時間以音訊實際播放到的位置為準（PortAudio 回報的 DAC 時間），不是系統時鐘，長時間循環也不會漂移 |
| 2 | 延遲補償 `offsetMs`：燈光用「音訊位置 + offset」的時間渲染（正值 = 燈提早）。前端「後端」分頁調整、可儲存（`preset/audio-calibration.json`）|
| 3 | 音檔不進 git，release 另外附上，放 `/etc/tph-art-museum/audio/`；拍點檔（小 JSON）進 repo（`backend/tracks/`）|
| 4 | 曲目依日期：聖誕曲在指定日期播放，其他日子播環境曲；之後會加入其他節慶曲目與表演 |
| 5 | 整體改為**特定時段播放表演**，其餘時段為**待機效果** |
| 6 | 天氣只取**風力**，在非表演時段影響待機效果；天氣分類（雨、陰天錯開）、色盤、每小時造訪移除 |
| 7 | 拍點：離線自動分析（開發機，librosa）＋前端手動修正工具 |

### 9.2 音檔

| 檔案 | 長度 | 格式 | 自動分析 |
|---|---|---|---|
| `光林_聖誕5.wav` | 76.9 s | 48 kHz、24-bit、立體聲 | 136 BPM，全曲穩定，第一拍 0.14 s → 可用規則格線 |
| `光林_環境_3mins.wav` | 181.5 s | 同上 | 估出 103 BPM，但各段 103–136 不一，16 s 才抓到第一拍 → 需要人工標記 |

### 9.3 M10 實作細節

- 相依：`sounddevice`（PortAudio 的 cffi 綁定；PyPy 可用）。Pi 需要 apt 的 `libportaudio2`（`bootstrap.sh` 已加入）。沒有 PortAudio 時音訊功能停用、燈光照常。
- `[audio]`（選填）：`device`（PortAudio 裝置名稱的一部分，例如 `"USB"`；空字串 = 系統預設）、`directory`（WAV 目錄）、`calibration`（延遲校準檔）。
- 服務帳號加入 `audio` 群組（unit 的 `SupplementaryGroups=audio`），直接使用 ALSA。
- `audio.py`：`AudioPlayer` 在 PortAudio callback 裡記錄「第幾個取樣在何時被聽到」，輸出迴圈每格讀取目前的音樂時間；`click-120` 節拍器（內建，120 BPM、4/4、60 秒循環）可精確校準，不受拍點偵測誤差影響。
- 拍點檔 `backend/tracks/<id>.json`：`{schemaVersion: 1, id, title, file, durationSeconds, tempo, beatsPerBar, firstDownbeat, beats: [秒…], source}`，由 `backend/scripts/analyze_audio.py` 產生（`--grid` 依穩定速度擬合成等距格線）。
- 閃拍測試：播放時勾選「閃拍」，全部轉燈與燈具在每拍閃白（小節第一拍全亮、其他拍 40%），蓋過目前效果；馬達不受影響。
- API：`GET /api/audio`、`PUT /api/audio/play {trackId, startSeconds?, loop?, flash?}`、`DELETE /api/audio/play`、`PUT /api/audio/calibration {offsetMs, save?}`。
- 前端：「後端」分頁〈音樂播放 · 延遲校準〉（`AudioPanel.tsx`）：曲目、閃拍、循環、播放／停止與進度（第幾拍）；延遲補償滑桿 ±300 ms＋數字輸入（±1000），即時套用、「儲存」寫檔、「還原為已儲存」。
- 拍點檔（2026-11-10 產生）：`christmas-5` 擬合 137.96 BPM（165 拍中 152 拍在 40 ms 內，殘差 RMS 8.3 ms；自動分析的 136 BPM 是 hop 量化造成的誤差）；`ambient-3min` 為原始偵測（387 拍，不可靠，待手動修正）。兩首的小節起點都暫定第 0 拍。
- 驗證：backend 164 個測試（新增 `test_audio.py` 16 個：拍點檔、節拍器、DAC 時鐘換算、循環、曲終、校準存檔、API、輸出迴圈閃拍；`test_release.py` 加音訊 seed 規則與 `tracks/`），CPython 與 PyPy 都通過；frontend 126 個測試、`tsc`、build 通過；Playwright 截圖面板。開發機（macOS）在這個終端環境開不了 CoreAudio 串流（`Pa_OpenStream` 卡住），實際出聲與時鐘**待 Pi 上驗證**。

### 9.4 M12 實作細節（演出）

2026-11-10 決定跳過 M11、先做 M12：演出只需要拍點檔定位段落，不需要拍點效果。

- **Show v1**（`show.py`、`frontend/src/simulation/show.ts`）：`{schemaVersion: 1, id, title, trackId, ending, sections}`。
  - 段落 `{id, start, spinner, ball?, wash?, overrides?, transition}`；`start` 是 `{seconds}` 或 `{bar, beat?}`（第 1 小節從拍點檔的 `firstDownbeat` 起算，第 0 小節是弱起；`beat` 從 1 起）。第一段固定 `{seconds: 0}`；每段播到下一段開始，最後一段播到曲尾。
  - `overrides`（選填，2026-11-10 加入）：`[{units: [燈台編號，從 1 起], spinner: EffectPreset}]`，段落中這幾台轉燈改用別的效果；同一段裡一台只能屬於一組（後端驗證，`units` 排序後儲存）。前端 `layerPresetForUnit`、後端 `render_sequence_frame` 逐台選效果；轉場照常逐台套用；依次數播放時覆寫照自己的週期循環。編輯：演出分頁的段落效果上方選「覆寫 n」，效果／顏色分頁就改那組；勾燈台編號或在 3D 畫面點轉燈（整顆光球大小的點選範圍）加入／移除。串接目前不支援。
  - `transition` 是**進入這一段**的轉場：`{kind, seconds}` 或 `{kind, beats}`（拍數依該處實際拍距換算，可用小數）；種類同串接（`cut`／`crossfade`／`fade-black`／`unit-fade-black`／`unit-breathe-black`），從段落開始的拍點起算。
  - `ending`：`{kind: "loop"}`（音樂循環、第一段的轉場用在接回開頭）或 `{kind: "fade-black", seconds}`（曲終淡出，之後回到待機輸出）。
- **編譯**：演出編成與串接相同的影格表（曲長 × FPS 向上取整），沿用同一套渲染與轉場；影格取整用 `floor(x + 0.5)`，前後端一致（同一組節拍器測試向量）。
- **播放**（`ShowPlayer`）：影格 = 音樂時間（音訊時鐘＋延遲補償）× FPS；循環時每一輪依曲長重新對齊，不累積誤差。出聲前停在起點；曲子播完後以最後讀到的時間外推，跑完結尾淡出。沒有音訊時以系統時鐘無聲播放。演出播放時取代串接／單一效果、不套用天氣；結束後待機輸出從頭播放。儲存正在播放的演出會直接換上新內容（同曲目、同循環設定時音樂不中斷）。
- **儲存**：`shows.json`（與 `sequence.json` 同目錄；Pi 為 `preset/shows.json`），最多 32 個演出、每個 256 段。拍點檔不見的演出列在 `errors`，不影響服務啟動。
- **音檔預載**：服務啟動時在背景執行緒把所有音檔讀進記憶體（rc.3 在 Pi 上第一次播放時讀 22 MB 的 WAV，讓輸出迴圈跳了 6 格）。
- **API**：`GET /api/shows`（演出、錯誤、播放中）、`PUT /api/shows/<id>`、`DELETE /api/shows/<id>`、`PUT /api/show/play {showId, startSeconds?}`、`DELETE /api/show/play`；`GET /api/audio/tracks/<id>`（拍點檔）、`GET /api/audio/tracks/<id>/wav`（音檔，給瀏覽器預覽）。`/api/playback` 的 `mode` 多了 `"show"`（位置欄位同串接，另附 `show`）。套用效果、串接、重播或手動播放音樂會停止演出。
- **前端「演出」分頁**（`ShowEditor.tsx`、`useShowEditor.ts`）：選擇／新增演出（選曲目）、段落清單、開始位置（小節＋拍或秒）、進場轉場（拍或秒）、結尾方式、儲存／刪除；勾選「在模擬畫面預覽」時，「效果」「顏色」分頁改的是選取的段落。
  - **瀏覽器音樂預覽**（`trackPlayer.ts`）：從後端下載 WAV、Web Audio 解碼後播放（節拍器在瀏覽器合成），模擬時鐘每格直接取音樂時間；不含現場延遲。暫停畫面或離開模擬會停止音樂。
  - 「在 Pi 上播放這個演出」／「停止演出」；後端控制模式下 3D 畫面依 `/api/playback` 鏡像演出。
- 驗證：backend 181 個測試（新增 `test_show.py` 17 個：編譯、拍數轉場、錯誤、與串接相同的渲染、音樂時間 → 影格、儲存、播放／結尾／無聲／儲存時替換、輸出迴圈切換與待機重播、API、拍點檔與 WAV 端點、預載），CPython 與 PyPy 都通過；frontend 132 個測試（新增 `show.test.ts`）、`tsc`、build 通過；Playwright 實際操作：新增演出、加段落、改段落效果、從頭／從段落播放（音樂時間 3 秒 → 畫面 3.1 秒）、儲存。headless Chromium 沒有音訊裝置，同步邏輯以「跟系統時鐘走的 AudioContext」驗證。
- 尚未做：Pi 實機（音效卡＋燈具）驗證；拍點效果（M11）；排程（M13）；波形與拍點修正（M14）。

### 9.5 M14 實作細節（時間軸與拍點修正）

2026-11-10 決定跳過 M13、先做 M14。

- **時間軸**（`ShowTimeline.tsx`）：勾選「在模擬畫面預覽這個演出」後顯示在 3D 畫面下方。由上而下是時間尺標、段落色塊（轉場以漸層標示，選取的段落有外框）、波形，以及拍點線（小節第一拍為黃色並標小節號）。紅線是播放位置；可縮放（10–400 px/s），播放時可自動跟隨。
  - **段落模式**：點時間軸跳到該位置；音樂播放中就從那裡繼續播。點段落可選取；拖曳段落開頭會吸附到最近的拍點，存成 `{bar, beat}`；按住 Option 拖曳則不吸附，存成秒數。空白鍵播放或停止。
  - **拍點修正模式**：
    - 點拍點選取、拖曳移動（夾在前後拍之間，至少相隔 20 ms）。
    - 雙擊空白處新增一拍；播放時按 B，在播放位置新增一拍（跟著音樂打拍）。
    - Delete 刪除選取的拍；←／→ 微調 5 ms，加 Shift 為 50 ms。
    - 「設為小節第一拍」改 `firstDownbeat`；可設定每小節拍數。
    - 內建節拍器不能修改。
  - 修正中的拍點立刻套用到預覽和編譯，以小節定位的段落會跟著移動。「儲存拍點」時重算速度（拍距中位數），`source` 記為「前端手動修正（M14）」。
  - 波形在瀏覽器解碼音檔後計算，每秒 200 個峰值，和音樂預覽共用解碼結果。
- **演出分頁直接改段落效果**：選取段落的設定下方有「效果／顏色」與「轉燈／球燈／洗牆燈」切換，內容與「效果」「顏色」分頁相同。新增或開啟演出時自動勾選預覽。
- **播放與停止**：演出預覽時，模擬畫面只在播放時前進，停止後停在當下位置（像剪輯軟體）；左上角按鈕變成「播放／停止」。沒有音檔時改為無聲播放，按停止結束。
- **拍點修正檔**：
  - 存在串接檔旁的 `beat-edits/<id>.json`（Pi：`preset/beat-edits/`，開發機：`backend/beat-edits/`，已加入 gitignore），優先於 release 內建的 `tracks/<id>.json`。
  - 修正檔的 `id`、`file`、`durationSeconds` 必須與內建版本相同；release 已不含的曲目，其修正檔會被忽略。
  - 「還原內建拍點」會刪除修正檔；「下載拍點檔」可取得 JSON，放回 repo 時覆蓋 `backend/tracks/` 的同名檔。
  - 修正目錄不能和內建目錄相同（避免還原時刪掉 repo 的檔案）。
- **API**：`PUT /api/audio/tracks/<id>`（儲存修正）、`DELETE /api/audio/tracks/<id>`（還原）；`GET /api/audio` 的曲目多了 `edited`。拍點改變後，播放中使用同一首曲目的演出會立刻改用新拍點。
- 驗證：
  - backend 184 個測試（新增 3 個：修正與還原、拒絕改動曲目身分、API 與播放中演出跟著更新），CPython 與 PyPy 都通過。
  - frontend 136 個測試（新增 `beatEdit.test.ts`），`tsc` 與 build 通過。
  - Playwright 實際操作：點選跳轉、拖曳段落開頭吸附到第 6 小節、播放跟隨、選拍並按 → 兩次（9.0602 → 9.0702 秒）後儲存，修正檔只改了那一拍；之後已還原。
- 尚未做：用工具實際修正環境曲（`ambient-3min`）的拍點；Pi 實機驗證。
