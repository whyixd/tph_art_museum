# tph_art_museum 實作計畫（初稿）

> 2026-11-02。以 `doc/system-reference.md`（Moon Observe 系統）為基礎，在美術館現場執行：
> - **轉燈 24 顆**（旋轉球形 POV 燈，取代原本的 9 組半圓燈；實體配置不同，細節待補）
> - **球燈 37 顆**（40cm 塑料燈罩，DMX 燈具，經 Art-Net 控制）——新增
> - **洗牆燈 25 顆**（RGBW DMX 燈具，經 Art-Net 控制，獨立一台轉換器）——新增
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

repo 現有的 `firmware/wled/unit_settings.json` 正好是 WLED 一路輸出（IO16）、129 顆、GRB（`order 0`）、Art-Net universe 0、Multi RGB、起始位址 1，不用改。**2026-11-11 更正**：現場燈條是 RGB 色序，`order` 改為 1（見文末）。對應的 backend 設定：

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
- **洗牆燈**：25 顆，RGBW，通道順序 R G B W、沒有 dimmer／strobe，位址 1, 5, 9 … 97；獨立一台 Art-Net → DMX 轉換器，IP **暫定** 2.0.0.100、universe 0（2026-11-11 確認數量與通道；IP 待現場確認）。安裝在後牆前的地面往上打牆，layout 範例暫以等距排列（見 §3.3）。

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
- `backend/layout.example.json`：24 台轉燈與 37 顆球燈的 x／y／z（見 Q5）；25 顆洗牆燈**暫定**沿後牆地面等距排列：x = 75 + 150 ×（編號 − 1），y = 933（離後牆 20 cm），z = 20，待實際安裝位置再改。
- layout 中 patch 沒有的燈具編號會被忽略（同轉燈超過 `unit_count`；例如設定還沒接洗牆燈），不是正整數的編號才報錯（2026-11-11 前為報錯）。
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
| Q3 | ~~洗牆燈數量、通道表~~ 25 顆 RGBW（R G B W）、位址 1, 5, … 97、獨立 IP（2026-11-11 確認，已寫進 `config.example.toml`）。**仍待確認**：轉換器實際 IP（暫定 2.0.0.100）、每顆的實際安裝位置（暫定沿後牆地面等距） | 現場接線前 |
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
| M13 | 排程、待機效果、風力（§9） | 每日表演時段；日期 → 曲目／演出；其餘時段為待機效果（可指定 M16d 的風力效果）；天氣分類、色盤、造訪移除 | M12 |
| M14 🔶 | 前端時間軸編輯器與拍點修正工具（§9.5） | 波形、拍點標記可增刪移動、瀏覽器試聽同步預覽。2026-11-10：先於 M11、M13 實作，程式與測試完成，**待 Pi 實機驗證與環境曲實際修正** | M12 |
| M15 | 清理：刪除不用的效果（含 `roulette-ring`）、舊串接與天氣程式；文件 | 測試與文件一致 | M12–M14 |
| M16 | 風速風向（§10）：現場 RS485 風速計（經 LoRa DTU）＋Open-Meteo 備援；新增待機專屬的風力效果（依風速變快、變色，依風向流動；既有效果不受影響）；強風自動緩停轉燈（門檻可設定）。2026-11-12 規劃，**尚未實作**；分 M16a 資料、b 前端與校正、c 強風保護、d 風力效果、e 現場（§10.13） | `GET /api/wind` 回報來源、原始值、平滑值；拔掉 FTDI → 約 25 秒內切到 Open-Meteo，接回 → 下一次讀取就切回；測試風超過門檻 → 全部緩停，降低後依控箱恢復；風力效果的前後端一致性向量；既有效果的向量逐 byte 不變；現場 checklist §10.11 | W3、W5–W7 |

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

### 2026-11-11：洗牆燈

- `config.example.toml`：`wash-rgbw` profile（R G B W、`white = "min"`、`brightness` 1.0 作為亮度上限）、node `wash`（2.0.0.100，暫定，universe 0）、25 顆 patch（位址 1–100）。`--patch-report` 列出 `wash 2.0.0.100 universe 0`。
- `layout.example.json`：25 顆洗牆燈沿後牆地面等距（見 §3.3）；高度、y 都相同，群組內正規化為 0.5，所以依高度的效果（`height-sweep`、`fixture-rise`）在洗牆燈上整排同步，依 x 的效果（`space-wave`、`horizontal-flow`、漸層 `axis = x`）才會沿牆變化。
- 3D：洗牆燈除了地上的錐體，牆面（最近的長牆）多一片寬 1.6 m、高 4 m、往上與兩側漸淡的受光區，顏色同該顆燈。
- 驗證：backend 208 個測試、frontend 163 個測試、`tsc`、build 通過；Playwright 全場與上視截圖，洗牆燈選「水平流動」時牆面受光正常，無頁面錯誤。
- Pi 升級注意：安裝程式只在第一次安裝時建立 `config.toml` 與 `layout.json`，已安裝的 Pi 要手動加上洗牆燈的 profile／node／patch（照 `config.example.toml`）與 layout 的 `fixtures.wash`。

### 2026-11-11：Art-Net 來源位址不在時暫停，而不是服務失敗

- 起因：Pi 不在現場時 eth0 沒接線，NetworkManager 不啟用 `netplan-eth0`，本機沒有 `source_ip`（2.0.0.10），服務以 `[Errno 99] Cannot assign requested address` 結束、每 3 秒重啟，控制頁面也打不開。
- `output.stream_when_source_available`：有 `source_ip` 時先試綁定（`source_available`，`EADDRNOTAVAIL` = 不在本機）；不在就印一次警告、每 2 秒重試，**不送任何封包**，網頁與 API（另一條執行緒）照常。位址出現後印「again; sending」並從第一格開始送。
- 送到一半位址消失：`stream` 每格送出前檢查，丟出 `SourceAddressLost`（建 client 時綁定失敗也一樣），不送馬達緩停與 DMX 全暗（封包送不出去），關閉 client 後回到等待。
- `GET /api/output` 多了 `artnet: {state: "sending" | "waiting", sourceIp}`（`--api-only` 時沒有）；進入等待時清掉影格統計。前端「後端」分頁在等待時以紅字顯示「Art-Net 暫停：本機沒有來源位址 …」。
- 驗證：backend 214 個測試（新增 `test_source_wait.py` 6 個：探測、等待後開始送、送到一半消失再恢復、等待清統計、建 client 時綁定失敗、沒有 source_ip 時直接送）；frontend 163 個測試、`tsc`、build 通過。開發機以 `source_ip = 2.0.0.10`（本機沒有）實際啟動：`/api/output` 為 waiting、`/api/state` 200、SIGINT 0.5 秒內正常結束（exit 0）。

### 2026-11-11：演出時音訊斷音（GIL）

- 現象：Pi 上播演出時，音樂在某些時間點延遲或出現雜音；在「後端」分頁單獨播音樂沒問題。日誌顯示演出期間每格最長 48–209 ms、有跳格（待機最長 28 ms）。
- 原因：音訊 callback 是 Python，要拿到 GIL 才能補資料；音效卡緩衝是 `latency="low"`（約 11 ms）。Pi 的 PyPy 實測（一條執行緒忙著算、另一條每 5 ms 醒來）：預設 `sys.setswitchinterval` 5 ms 時，等待的執行緒約 **34 ms** 才輪到一次（每次都超過 11 ms）；改成 1 ms 時最長 8.8 ms。演出的運算比待機重，callback 等不到 GIL，緩衝就用完了。
- 修正：
  - `main` 啟動時 `sys.setswitchinterval(0.001)`。
  - 音效卡緩衝改為 `[audio] buffer_ms`（預設 100，10–1000）。燈光的時間是音效卡實際播出的時間（`outputBufferDacTime`），緩衝加大不影響同步，只讓開始播放晚約 0.1 秒。
  - callback 統計 `output_underflow`：`GET /api/audio` 的 `underflows`、`bufferMs`；每分鐘的 `output:` 日誌行加上 `audio underflows N`；前端音樂區顯示緩衝大小與斷音次數。
- 驗證：backend 216 個測試、frontend 163 個測試、`tsc`、build 通過。

### 2026-11-11：轉燈燈條色序改為 RGB

- 現場單色測試：送紅亮綠、送綠亮紅（WLED `order` 0 = GRB 時紅綠對調），燈條實際色序是 **RGB**。燈條類型維持 WS281x（type 22，每顆 3 bytes）；曾誤以為是 G R B W 而把 2.0.0.201 改成 SK6812 RGBW，送紅出現紅綠藍相間，已改回。
- `firmware/wled/unit_settings.json` 的 `order` 0 → 1（`COL_ORDER_RGB`）。backend 照送 RGB、`[hardware] color_order` 不用改，Art-Net 內容不變。
- 由 Pi 以 `flash.py config`（只寫設定、不刷韌體，寫入後重開機並讀回比對）套用到網路上的 21 台（2.0.0.201–206、208–222），先改 201 經使用者單色測試確認紅綠藍正確後再改其餘 20 台。寫入前的設定備份在 Pi 的 `~/wled-config/backups/`。
- **未套用**：2.0.0.207、223、224 不在網路上（ARP 無回應），接回後要跑 `uv run flash.py config 2.0.0.207 2.0.0.223 2.0.0.224`（服務要先停）。

### 2026-11-11：每台轉燈的馬達、效果分頁直接套用

- **每台馬達**：`PresetStore.motor_units`（轉動中的編號；後端重啟後全部停止）。`PUT /api/motor` 接受 `{"enabled"}`（全部）或 `{"enabled", "units": [編號…]}`（只動這些台，其他不變）；`GET /api/state` 的 `motor` 多了 `units`（每台 true／false），`enabled`／`value` 改為「有任何一台在轉」。輸出迴圈的 `motor_provider` 可回傳每台的值（`store.motor_values(unit_count)`），每台 Art-Net ch469 各自送 0／255；結束時只對轉動中的台送緩停。
- 前端「後端」分頁的馬達控制：〔全部緩停〕〔全部緩啟動〕＋ 24 個編號按鈕（亮色 = 轉動中，點一下切換該台），顯示「轉動中 N／24 台」。舊後端沒有 `units` 時只顯示全部的按鈕。
- **效果分頁套用**：後端控制時，「效果」「色彩」分頁頂端有〔套用並覆蓋播放〕：送出轉燈、球燈、洗牆燈的 preset（同「後端」分頁的套用到後端），後端停止演出／串接、改播這個效果。狀態列說明後端目前播放什麼（演出／串接 → 會被停止；尚未套用；已在播放）；已在播放且沒有變更時按鈕停用。
- 驗證：backend 新增 `MotorPerUnitTests`（個別啟停、無變更不加 revision、錯誤的編號清單）與每台馬達的輸出測試；frontend 163 個測試、`tsc`、build 通過。Playwright（`--api-only`）：點 3、7 號 → 後端 `units` 只有 3、7 轉動，「轉動中 2／24 台」；再點 3 → 只剩 7；全部緩停 → 全停。效果分頁改效果後按套用 → 後端 preset 換成該效果、播放模式由串接變成 single。


### 2026-11-12：全部關燈

- `PresetStore.blackout`（只在記憶體，後端重啟後恢復亮燈）。`PUT /api/blackout` `{"enabled": true|false}`；`GET /api/state` 多了 `blackout`。
- 輸出迴圈 `blackout_provider`：開啟時轉燈與球燈、洗牆燈全部送全黑，蓋過效果、演出、單色測試與閃拍；底下的演出／串接照常前進，關閉後接著播放。**馬達不受影響**（另用馬達控制）。
- `GET /api/playback` 在關燈時回傳 `blackout: true`，並把 `unitColors`／`fixtureColors` 設為全黑，3D 鏡像跟著全暗，左上角顯示「全部關燈中」。
- 前端「後端」分頁連線資訊下方：〔關閉全部燈光〕／〔恢復燈光〕；關燈時「效果」「色彩」分頁的套用列提示套用後仍全暗。
- 驗證：backend 221 個測試（新增 `BlackoutOutputTests`、`test_blackout_api_and_playback_mirror`）；frontend 163 個測試、`tsc`、build 通過。開發機以 Art-Net 全改送 127.0.0.x 的設定實際啟動，Playwright 按〔關閉全部燈光〕→ `/api/playback` 為 blackout、24／37／25 全黑、3D 全暗；〔恢復燈光〕→ `blackout: false`，無頁面錯誤。

### 2026-11-12：馬達啟動被 XIAO 忽略（後端延後啟動）

- 現象：「全部緩啟動」／「全部緩停」後總有幾台沒起動；等幾十秒單獨關再開就正常。
- 原因（`firmware/sketches/motor_control_io`）：XIAO 只在 POWER_OFF 時、D8 由 HIGH 變 LOW 那一下才啟動；**緩停中（duty 82 約 10 秒，上限 90 約 11.1 秒）收到的啟動被忽略且不再補**。WLED 對 IO13 是持續的準位（Pi 一直送 255），不會再有第二次 HIGH→LOW，所以停止後約 10 秒內再啟動的那幾台會一直停著（畫面卻顯示轉動）；單獨關再開剛好補上一次 HIGH→LOW。主機 harness 重現（`firmware/tests` 的 `motor_control_io_host.cpp` 框架）。UDP 丟包、同時送太多訊號都不是原因：馬達值每格（40 FPS）都送，按全部啟動不會增加封包。停止一定有效（HIGH 在任何運轉狀態都會停）。
- 不能用「連送三次啟動」：中間的 0 會讓已啟動的台進入緩停，接著的啟動又被忽略，結果反而停下。
- 修正（後端，不用刷 XIAO）：`motor.MotorGate` 在輸出迴圈送出前，每台送 0 滿 `[motor] restart_delay_seconds`（預設 12）才改送 255；第一格與輸出中斷 ≥ 1 秒（WLED 逾時，IO13 已放開）都算停止。`PresetStore.motor_output()` 給輸出迴圈；`GET /api/state` 的 `motor.waiting` = 每台還要等幾秒（null = 沒在等）。前端編號按鈕顯示「等 N 秒」（黃色），狀態列「其中 M 台等前一次緩停結束」，有在等時每秒更新。
- 未處理：XIAO 開機時 D8 已是 LOW 不會啟動（開機安全規則）；若同時啟動造成電源瞬降讓 XIAO 重開，仍要單獨關再開。現場若再發生，查該台 WLED `/json/info` 的 `moonMotor`（`output: run`、`reason: artnet-run` 但馬達不轉 = XIAO 端）。
- 驗證：backend 新增 `test_motor.py`（8 個），全部通過；frontend 163 個測試、`tsc`、build 通過。開發機（Art-Net 只送 127.0.0.x）API 實測：停止後 3 秒再啟動 → `waiting` 9.0，12.15 秒後送出 255；Playwright：停止後 7.4 秒再啟動 → 按鈕「等 5 秒」，時間到自動變「轉動」，無頁面錯誤。

### 2026-11-12：馬達依控箱分區啟動

- 同時啟動多台時，`MotorGate` 每 `[motor] start_interval_seconds`（預設 1，0 = 不分區）最多放行一組，依 `[[motor.start_groups]]` 的順序（較前的先）；避免多台的預充電／主繼電器與電容充電同時衝擊電源（也降低 XIAO 因電源瞬降重開而不起動的機會）。停止不分區。沒列入的轉燈各自一組排在最後；沒有 `start_groups` = 全部同時。規則在送出端（每格），所以「全部緩啟動」、連點多台、Art-Net 中斷後恢復都會分區；單獨一台且前一秒沒有其他啟動時立即放行。與「停止後 12 秒」一起作用：每組在兩者都滿足時才放行。
- `config.example.toml`：12 個控箱（`doc/新北美_ip控箱.pdf`，unit = IP − 200）依編號 101 → 112：101 = 3、4；102 = 7、8；103 = 11、12；104 = 13、14；105 = 1、2；106 = 5、6；107 = 9、10；108 = 15、16；109 = 18、19；110 = 17、20；111 = 21、22；112 = 23、24。**對應與順序待現場確認**。
- `GET /api/state` 的 `motor.waiting` 改為模擬放行規則推算的剩餘秒數；前端「等 N 秒」、狀態列「其中 M 台排隊啟動」。
- Pi 升級注意：既有的 `config.toml` 不會自動加入 `start_interval_seconds` 與 `[[motor.start_groups]]`，要手動照 `config.example.toml` 加；沒加時只有「停止後 12 秒」，全部同時啟動。
- 驗證：backend 234 個測試（`test_motor.py` 新增分區 4 個、設定 1 個；`test_hardware` 檢查範例 24 台各在一個控箱）；frontend 163 個測試、`tsc`、build 通過。開發機（Art-Net 只送 127.0.0.x）API 實測：全停時按全部緩啟動 → 控箱 101…112 依序在 0、1、2 … 11 秒放行；停止 4 秒後再按 → 從 8 秒起每秒一個控箱。

### 2026-11-12：每根柱子由上往下編號

- 現場測試：球燈、轉燈在同一根柱子上的編號與 `layout.example.json` 上下相反（3D 由上到下 3-2-1，實際 1-2-3）。原本由 `DMX＿線間距` 與「IP 靠前 = 較低那台」（暫定）建立，同柱都是編號越大越高。
- `layout.example.json`：每根柱子（最近的柱子、手臂 ≤ 1.2 m，同 `_nearest_pole`）上的轉燈與球燈，位置不動、編號上下對調（最小號給最高的位置）；單獨一顆的柱子不變；洗牆燈、柱子不變。Pi 的 `layout.json`（2026-11-12 備份）內容與舊範例相同，部署時要換成新的。
- 結果（由上到下）：柱 1 轉燈 1、2，球燈 1、2、3、4；…；球燈 17–20 那根的轉燈為 11、12、14 —— **14 號變成該柱最低**，與先前「14 號是那根最高」的暫定說法不同，待現場確認。
- 依位置的資料跟著變：`pillar_ranks`（pillar-fill 順序）、`pole-rise` 柱內順序、`wave-split`／中心擴散水平流動的轉燈位置；前後端向量 `effects_v2.json`（`wavePositions`、`flowPositions`）、`fixtures_v1.json`（`poleRise`）以 `--write` 重產，其餘段落逐 byte 不變。
- `doc/spaces05_annotated_top.png`／`_side.png` 仍是舊編號（未重畫），現場檢查清單改為以 3D 畫面為準。
- 驗證：backend 234、frontend 163 個測試（更新寫死舊位置的 8 個測試期望值）、`tsc`、build 通過。

## 10. 風速風向（M16，2026-11-12 規劃，尚未實作）

現場的風會影響**待機時專屬的風力效果**和**轉燈馬達**：
- **風力效果**：另外製作待機專用的效果，只有這些效果會讀取風力資料，既有效果不受影響。風越大流動越快、顏色跟著改變；風向決定流動方向。
- **強風**：自動緩停轉燈馬達，門檻可以設定。

架構參考 `~/Dev/hsz_libary`（新竹縣總圖，`host/api/services/wind_sensor.py`、`modbus.py`、`cwa_wind.py`），一樣有兩個來源：**現場 RS485 風速計為主，Open-Meteo 為備援**。hsz 的網路備援是氣象署 CWA（要 API key）；本案改用 Open-Meteo（不用 key，`weather.py` 已經在用）。

### 10.1 已確認事項（2026-11-12）

| 項目 | 內容 |
|---|---|
| 風速計 | 跟 hsz 同型號（Modbus RTU，見 10.2） |
| 無線 | 兩台 **SX1262-LoRa-DTU-HF**，以透傳模式成對使用：一台在風速計端，一台在 Pi 端 |
| USB 轉 RS485 | **Dtech FTDI 晶片系列**。Linux 核心內建 `ftdi_sio` 驅動，Pi 插上就是 `/dev/ttyUSB*` |
| 安裝 | 風速計約 **4 m 高**。**風向的 0° 沒有對準正北**，要做角度校正（10.5） |
| 場地方位 | 場地座標 +x 軸的方位角**還不知道，要到現場量** |
| 網路 | 現場 Pi 可以上網（wlan0），Open-Meteo 備援有效 |
| 效果 | 風越大越快、顏色跟著改變；風向決定流動方向。**做成待機專屬的風力效果，不套用到既有的所有效果**（2026-11-12 修改） |
| 馬達 | 強風時**要**自動緩停，門檻要可以設定 |

### 10.2 硬體與接線

```
風速計 ─RS485─ LoRa DTU（風速計端） ~~~ 915 MHz 頻段 ~~~ LoRa DTU（Pi 端） ─RS485─ Dtech FTDI USB 轉 RS485 ─USB─ Pi 5
```

- **風速計的暫存器**（跟 hsz 相同）：9600 8N1，設備位址 1，功能碼 0x03，從 reg 0 讀 4 個：

  | reg | 內容 | 換算 |
  |---|---|---|
  | 0 | 風速 | ÷ 10 = m/s |
  | 1 | 蒲福風級 | 0–12（不使用，改由風速自己算，兩個來源才會一致） |
  | 2 | 風向角度 | ÷ 10 = 度（風**吹來**的方向，以風速計自己的 0° 為準） |
  | 3 | 16 方位代碼 | 0–15（只用來做合理性檢查，看跟角度是否一致） |

- **LoRa DTU 設定**（兩台要一致，用廠商工具設定，設定值記到 `doc/site-checklist.md`）：
  - 串口參數 9600 8N1，與風速計相同。
  - 透傳模式。
  - 兩台的頻道、網路 ID、空中速率相同。
  - 頻段要在台灣可用的 920–925 MHz 內。
  - 空中速率越低，傳得越遠，但來回時間越長。一次讀取是要求 8 bytes、回應 13 bytes，加上 LoRa 前導碼與 DTU 的封包間隔，低空速時來回可能要數百 ms。所以 **Modbus 逾時預設 2 秒**（hsz 是 1 秒），現場用 `wind_probe.py` 量實際的來回時間後再調整。
  - DTU 會把一段資料切成多個 LoRa 封包，或把多段資料合成一個封包送出。所以讀回應時不能只等一次 `read`，要**累積讀到預期長度或逾時**（hsz 用 `serial.read(expected)` 搭配 timeout，效果相同，保留這個做法）。
- **風速計端的電源**：風速計與 DTU 都要供電，4 m 高的桿子上要有電源與防水盒。**待確認**電源從哪裡來。
- **FTDI 的裝置路徑**：用 `/dev/serial/by-id/usb-FTDI_…-if00-port0`。Pi 還有 USB 音效卡，`ttyUSB` 的編號不保證固定。FTDI 晶片有唯一序號，by-id 路徑不會變。
- **讀序列埠的權限**：服務帳號要在 `dialout` 群組。

### 10.3 後端架構

```
WindReader 執行緒（每 poll_seconds）          OpenMeteo 執行緒（每 openmeteo_minutes）
   │ Modbus 讀取、合理性檢查                     │ HTTP 取得 current 風速／風向／陣風
   └──────────────┬─────────────────────────────┘
                  ▼
           WindStore（lock）
           ├─ 原始讀值、來源判定（hw → openmeteo → 離線）
           ├─ 角度校正：sensor_offset_deg、north_deg
           ├─ 平滑、陣風視窗
           ├─ 測試風 override
           └─ snapshot()（輸出迴圈每格呼叫，只複製數值）
                  │
        ┌─────────┴──────────────┐
        ▼                        ▼
 WindMotorGuard（10.7）     輸出迴圈：風力效果的相位累積（10.6）
 強風 → 馬達緩停            → 只傳給 wind-* 效果
```

**檔案**

| 檔案 | 內容 |
|---|---|
| `modbus.py` | 照搬 hsz 的 CRC16、`build_read_request`、`parse_read_response`，改成同步。`ModbusRTUClient.read_holding_registers()` 會累積讀取到預期長度或逾時；讀之前清空輸入緩衝；開埠失敗時，下次讀取再試開（風速計可以熱插拔） |
| `wind.py` | `WindStore`、兩條執行緒、平滑、校正、override、`WindSettingsStore`（10.8） |
| `effects.py`／`fixture_effects.py` 與前端對應檔 | 新增 `wind-*` 效果（前後端雙實作，見 10.6）。相位累積器 `WindPhase` 放在 `wind.py`，由輸出迴圈呼叫 |
| `motor.py` | 新增 `WindMotorGuard`，接在 `PresetStore.motor_output()` 之前 |
| `scripts/wind_probe.py` | 現場工具（10.10） |

**來源判定**

- 每 `poll_seconds`（預設 5）讀一次風速計。以下情形都算這次讀取失敗：
  - 逾時、CRC 錯誤、回應長度不對、Modbus 例外碼。
  - 合理性檢查不通過：風速 0–60 m/s、角度 0–360、方位代碼與角度相差 ≤ 1 格。
- **連續失敗 `fail_limit`（預設 5，約 25 秒）次**，就改用 Open-Meteo；只要讀成功一次，就立刻切回 hw。偶爾一次逾時不會切換來源（LoRa 偶爾掉包是常態）。
- **風速卡住偵測**（hsz 沒有這項）：連續 `stuck_minutes`（預設 30）分鐘風速一直是 0.0，同時 Open-Meteo 的風速 ≥ 3 m/s，就判定風杯卡住或斷線，改用 Open-Meteo，並在 API 與前端標示「風速計疑似異常」。風向固定不變不算卡住，因為無風時風向本來就不動。
- **Open-Meteo 不論 hw 是否正常都定期抓取**，一方面當備援，一方面拿來比較，作為 10.5 換算係數的依據。
  - 參數：`current=wind_speed_10m,wind_direction_10m,wind_gusts_10m`、`wind_speed_unit=ms`（預設是 km/h，一定要指定）、`timezone=auto`。
  - 頻率：每 10 分鐘一次。它的 current 是模型值，約 15 分鐘更新一次。
  - 抓取失敗時沿用上一筆，超過 `stale_minutes`（60）分鐘就視為沒有資料。
- 兩個來源都沒有資料時，`online = false`，效果改用 `fallback_speed_ms`（預設 0），風向保留最後一個值；馬達保護依 10.7 的 `offline` 設定處理。
- 每次切換來源都記一行 log，例如 `wind: source hw → openmeteo (5 failures)`。

**平滑**（在 `snapshot()` 裡依經過的時間計算，前後端只收到結果）

| 用途 | 做法 | 預設 |
|---|---|---|
| 風力效果的速度 | 風速一階低通 | `speed_smoothing_seconds` 10 |
| 風力效果的顏色 | 風速一階低通（較慢，顏色不會隨陣風閃動） | `color_smoothing_seconds` 30 |
| 風向 | **單位向量**一階低通，再加上轉向速率上限。不能直接平均角度數字：359° 和 1° 的平均是 0°，不是 180° | `direction_smoothing_seconds` 20、`max_turn_deg_per_second` 3 |
| 無風 | 風速 < 0.3 m/s 時，風向保持不動 | – |
| 陣風（馬達保護用） | 最近 `gust_window_seconds`（60）秒 hw 讀值的最大值；Open-Meteo 來源直接用 `wind_gusts_10m` | – |

轉向速率上限的原因：`horizontal-flow` 的相位 = 沿流動方向的距離 × 波數 − 已流動距離，方向一變，離中心越遠的燈相位跳得越多。限制每秒最多轉 3°，看起來就是慢慢轉向。

### 10.4 輸出的資料

`WindStore.snapshot()`：

```jsonc
{
  "online": true,
  "source": "hw",              // "hw" | "openmeteo" | "override" | null
  "speedMs": 4.2,              // 平滑後（風力效果的速度）
  "colorSpeedMs": 3.9,         // 較慢的平滑（風力效果的顏色）
  "gustMs": 7.8,               // 陣風（馬達保護用）
  "beaufort": 3,
  "fromDeg": 225.0,            // 真北方位，風吹來的方向（校正後）
  "flowDeg": 117.0,            // 場地座標中，風吹往的方向（風力效果的流動方向）
  "sensorSuspect": false,
  "updatedAt": "…"
}
```

`GET /api/wind` 除了 snapshot，還會回傳：
- `raw`：hw 最近一筆讀值，含風速計原始角度與時間。
- `hw`：`{port, connected, consecutiveFailures, lastOkAt, okCount, failCount, lastRoundTripMs}`。
- `openmeteo`：`{speedMs, fromDeg, gustMs, observedAt, fetchedAt, error}`。
- `guard`：見 10.7。
- `history`：最近 60 分鐘、每 10 秒一筆，給前端畫折線圖。只存在記憶體裡。

### 10.5 角度校正與 Open-Meteo 換算

三種方向要分清楚：
1. **風速計角度**：風速計自己的 0°，沒有對準正北。
2. **真北方位**：氣象慣例，風吹來的方向，Open-Meteo 就是這個。
3. **場地方向**：layout 平面上的角度，0° = +x、90° = +y，跟 `horizontal-flow` 的 `directionDegrees` 一樣。

換算方式：
- `fromDeg = (風速計角度 + sensor_offset_deg) mod 360`
- `flowDeg = (north_deg − (fromDeg + 180)) mod 360`
- **`north_deg` 要先確認 layout 的 y 軸朝哪邊**：+y 是朝向畫面上方還是下方，會影響角度是順時針還是逆時針，正負號可能要反過來。實作前要先確認 layout 的座標慣例，並寫測試固定下來。

**現場量測步驟**（寫進 `doc/site-checklist.md`）：
1. **`north_deg`**：手機指南針貼著長牆，從 x 小的一端朝 x 大的一端（+x 方向）量方位角。台灣的磁偏角約 4–5° 西，要修正成真北；或改用 Google 地圖衛星圖量長牆的方位，誤差比較小。
2. **`sensor_offset_deg`**：把風向標轉到指向一個已知方位（例如 +x 方向，真北方位 = `north_deg`），在前端「後端」分頁按〔以目前風向標校正〕，輸入那個已知方位。後端會用「已知方位 − 目前風速計角度」算出 offset 並儲存。
3. **驗證**：有風時比較 `fromDeg` 和 Open-Meteo 的風向，大致相同就好，兩者本來就會有差異。

**Open-Meteo 的風速換算**：Open-Meteo 給的是 10 m 高、網格平均的模型值；現場風速計在 4 m 高，周圍有建築物，通常比較小。
- Open-Meteo 的風速、陣風會先乘上 `openmeteo_scale`（預設 0.7，暫定）再使用，切到備援時效果才不會突然變快。
- hw 正常時兩邊的資料都有記錄（每分鐘 log 一行，含兩邊的值），運轉幾天後依比值調整 `openmeteo_scale`。
- 前端顯示目前的比值（過去 24 小時 hw 平均 ÷ Open-Meteo 平均）作為參考。

### 10.6 風力效果（待機專屬）

**原則**：
- 另外製作**待機專用、會讀取風力資料的效果**（效果 ID 以 `wind-` 開頭）。
- 只有這些效果使用風力資料；**既有效果完全不改**，不會因為風而變快或變色，前後端一致性向量也逐 byte 不變。
- 效果仍是純函式：風力資料由輸出迴圈算好後當作輸入傳入，前後端可以用同一份向量比對。

#### 傳給效果的風力輸入

效果 context 新增 `wind`。只有 `wind-*` 效果會讀取；其他效果收不到這個欄位。

```jsonc
"wind": {
  "phase": 12.84,        // 已流動的圈數，由輸出迴圈累積（見下）
  "strength": 0.31,      // 依效果參數 calmMs／fullMs 換算的 0–1
  "colorSpeedMs": 3.9,   // 選顏色用（較慢的平滑）
  "flowDeg": 117.0       // 場地座標中，風吹往的方向
}
```

**為什麼要累積相位（`WindPhase`）**：
- 既有效果的位置都是用「影格 ÷ FPS × 速度」直接算出來的。如果把速度換成會變動的風速，風速一變，整段相位就會跳一下。
- 所以由輸出迴圈每格累積：`phase += speedHz × rate ÷ fps`，其中 `rate = minRate + (maxRate − minRate) × strength`。風速變化時，流動只會慢慢加速或減速。
- 何時歸零：效果開始時、串接每一段開始時。
- 轉場時，前後兩層各自累積。
- 前端預覽用同一套累積方式。

**`strength` 的換算**：`strength = clamp((speedMs − calmMs) / (fullMs − calmMs), 0, 1) ^ curve`。這些是效果自己的參數，見下表。

#### 第一個風力效果：`wind-flow`（風流）

轉燈、球燈、洗牆燈三個群組都有，畫面連成一片。

- **畫面**：光帶沿著風吹往的方向（`flowDeg`）流過整個場地，做法類似 `horizontal-flow`。
  - 位置 = 燈在場地平面上沿 `flowDeg` 的距離 × 波數 − `phase`。
  - 轉燈用每台的位置，可以再加上像素在球面上的位置，讓光帶在轉燈上也有流動感（實作時決定）。
- **速度**：風越大，`phase` 累積越快，流動越快。
- **顏色**：用效果自己的**風速色盤**，依 `colorSpeedMs` 在相鄰兩段之間連續內插。不像溫度色盤直接換段，因為風速常在門檻附近變動，直接換段顏色會一直跳。

**參數**（存在 preset，「效果」分頁可以調整）：

| 參數 | 預設 | 說明 |
|---|---|---|
| `speedHz` | 0.2 | 基準流速（rate = 1× 時） |
| `minRate`／`maxRate` | 0.6／3 | 無風與 `fullMs` 時的倍率 |
| `calmMs`／`fullMs` | 1／12 | 低於 `calmMs` 視為無風；達到 `fullMs` 時 `strength` = 1 |
| `curve` | 1 | `strength` 曲線（> 1 = 小風時變化較小） |
| `waveCount` | 1 | 場地內同時有幾道光帶 |
| `depth` | 0.5 | 亮暗起伏的深度（同 `horizontal-flow`） |

**風速色盤**：放在 preset 的 `color`，新的 mode `wind-gradient`，只有 `wind-*` 效果可以選。

```jsonc
"color": { "mode": "wind-gradient", "axis": "radius",
           "stops": [ {"atMs": 0,  "colors": ["#2444AA", "#72D8FF"]},
                      {"atMs": 5,  "colors": ["#328BD2", "#8FFFC8"]},
                      {"atMs": 12, "colors": ["#FFB347", "#FF477E"]} ] }
```

- 依 `colorSpeedMs` 找出前後兩個 stop，兩組漸層色逐色內插（sRGB 線性內插，前後端一致）。低於第一個 stop 用第一組，高於最後一個用最後一組。
- 「色彩」分頁加一個風速色盤編輯器：stop 可以增刪、修改風速與顏色，旁邊有預覽風速滑桿。
- 驗證：`atMs` 嚴格遞增、0–60；至少 2 個、最多 8 個 stop；每個 stop 的顏色數量相同。
- 溫度色盤（`weatherPalette`）不會套用到 `wind-*` 效果。

**之後可以增加的風力效果**（等 `wind-flow` 在現場看過後再決定，W5）：
- `wind-gust`：陣風時一道亮帶沿風向掃過全場。需要「陣風事件」（陣風超過平均風速一定比例的時間點），也由輸出迴圈當作輸入傳入。
- 轉燈專屬：風越大，光點在球面上拖得越長，用流線的感覺表現。

#### 使用範圍

- **待機**：M13 的待機時段可以指定播放風力效果，播放方式跟一般單一效果或串接相同。
- **「效果」分頁**：風力效果另外歸為「待機（風力）」一類。後端控制時可以套用，方便現場調整。
- **演出不提供**：演出編輯器不列出 `wind-*`，音樂演出維持每次相同的畫面。
- **沒有風力資料時**（沒有設定 `[wind]`，或兩個來源都離線）：效果使用 `fallbackSpeedMs`（預設 0），也就是用 `minRate` 慢慢流動、用色盤第一組顏色；`flowDeg` 保留最後一個值，從來沒有資料時用 0°（沿 +x）。
- **效能**：風力效果依位置運作，不在「各台畫面相同只算一次」的名單（`UNIT_INDEPENDENT_EFFECTS`）內；計算量和 `horizontal-flow` 相近，Pi 上要用 `benchmark_render.py` 確認。

#### 預覽

- 「效果」「色彩」分頁編輯風力效果時，顯示「預覽風力」：風速滑桿、風向轉盤，或〔使用後端即時風力〕。本機預覽也用 `WindPhase` 累積相位。
- 後端控制時，3D 畫面照現在的方式顯示後端回傳的實際顏色。

### 10.7 強風保護：自動緩停轉燈

`WindMotorGuard`（在 `motor.py`）：
- 位置：放在使用者的馬達選擇（`motor_units`）與 `MotorGate` 之間。
- **只蓋過、不修改**使用者的選擇：強風時每台都送 0，解除後恢復成使用者原本選擇的狀態。
- `MotorGate` 的「停止後 12 秒才能啟動」與控箱分區啟動照常運作。
- **演出時也生效**（安全優先）。

**狀態**：

```
normal ──（陣風 ≥ stop_gust_ms，或平均 ≥ stop_speed_ms，持續 trigger_seconds）──▶ stopped
stopped ──（陣風 < resume_gust_ms，且平均 < resume_speed_ms，持續 resume_seconds）──▶ normal
```

**判斷用的值**：
- 「陣風」是 `gust_window_seconds` 內 hw 讀值的最大值；「平均」是 `speedMs`。
- **停止門檻要用陣風**，反應才夠快：效果用的平滑風速有 10 秒延遲，不適合做安全判斷。
- 恢復要等**持續一段時間**低於門檻（例如 10 分鐘），而且恢復門檻要低於停止門檻（遲滯），避免陣風一來一去讓馬達反覆啟停。

**可設定項目**（存在 `preset/wind-settings.json`，前端可修改；第一次啟動時的預設值來自 `config.toml`）：

| 欄位 | 預設（暫定，**要依轉燈廠商的耐風規格決定**） | 說明 |
|---|---|---|
| `enabled` | true | 關閉 = 不做強風保護 |
| `stop_gust_ms` | 12 | 陣風達到就停 |
| `stop_speed_ms` | 8 | 平均風速達到就停 |
| `trigger_seconds` | 0 | 持續多久才停（0 = 一超過就停） |
| `resume_gust_ms` | 9 | 低於這個值才開始計時恢復 |
| `resume_speed_ms` | 6 | – |
| `resume_seconds` | 600 | 連續低於恢復門檻多久才恢復 |
| `sources` | `["hw"]` | 哪些來源可以觸發。Open-Meteo 是模型值，預設不觸發停止；勾選後也會依它判斷 |
| `offline` | `"keep"` | 兩個來源都沒資料時：`keep` = 維持現狀；`stop` = 停止 |

**前端「後端」分頁**：
- 馬達區塊顯示「強風停止中：陣風 13.2 m/s ≥ 12，風速降低後 8 分鐘恢復」。
- 24 個編號按鈕仍可以切換，但只會改變恢復後的狀態。
- 〔強風保護〕設定表單、〔暫停保護 30 分鐘〕：給現場維修時使用，時間到自動恢復保護，並記 log。

**log**：記錄停止與恢復，含觸發的值、來源，以及停止了哪幾台。

**`GET /api/state` 的 `motor`** 多一個欄位 `windGuard: {state, reason, resumeInSeconds, pausedUntil}`。

### 10.8 設定

**`config.toml`**：硬體和第一次啟動時的預設值。

```toml
[wind]
# 省略 port = 只用 Open-Meteo
port = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_XXXXXXXX-if00-port0"
baudrate = 9600
address = 1
timeout_seconds = 2.0       # 經 LoRa 來回，現場量測後再調
poll_seconds = 5
fail_limit = 5
stuck_minutes = 30
latitude = 0.0              # 新北市美術館（待填）
longitude = 0.0
openmeteo_minutes = 10
stale_minutes = 60
settings = "wind-settings.json"
```

**`preset/wind-settings.json`**：現場會調整的值，前端可以修改，用原子寫檔，`GET/PUT /api/wind/settings`。

```jsonc
{
  "schemaVersion": 1,
  "calibration": { "sensorOffsetDeg": 0, "northDeg": 0, "openmeteoScale": 0.7 },
  "smoothing": { "speedSeconds": 10, "colorSeconds": 30, "directionSeconds": 20, "maxTurnDegPerSecond": 3, "gustWindowSeconds": 60 },
  "fallbackSpeedMs": 0,
  "motorGuard": { "enabled": true, "stopGustMs": 12, "stopSpeedMs": 8, "triggerSeconds": 0,
                  "resumeGustMs": 9, "resumeSpeedMs": 6, "resumeSeconds": 600,
                  "sources": ["hw"], "offline": "keep" }
}
```

- 驗證規則：
  - 數值範圍檢查；恢復門檻必須小於停止門檻。
  - 風速對應效果的參數（`calmMs`、`fullMs`、倍率、色盤）不放這裡，是各個風力效果自己的參數，存在 preset（10.6）。
  - 不認得的欄位直接報錯。
- `[weather]`：依 §9.1 決策 6，要在 M15 移除（雨天、陰天分類、溫度色盤、造訪）。過渡期間兩個區段並存；`[wind]` 有自己的座標。
- installer 不會修改已有的 `config.toml`，Pi 升級時要手動加 `[wind]`；`wind-settings.json` 沒有檔案時用預設值，不需要手動建立。

### 10.9 API 一覽

| 方法 | 路徑 | 內容 |
|---|---|---|
| GET | `/api/wind` | snapshot＋`raw`、`hw`、`openmeteo`、`guard`、`history` |
| PUT／DELETE | `/api/wind/override` | 測試風 `{speedMs, fromDeg}`（真北方位），維持 10 分鐘後自動恢復；`source = "override"`。風力效果與強風保護都會跟著測試風反應，方便現場驗證 |
| GET／PUT | `/api/wind/settings` | 10.8 |
| POST | `/api/wind/calibrate` | `{knownFromDeg}` → 用目前的 hw 原始角度算出 `sensorOffsetDeg` 並儲存。沒有 hw 讀值時回傳 409 |
| PUT | `/api/motor/wind-guard` | `{pauseMinutes}`：暫停保護（0 = 立即恢復保護） |

### 10.10 前端（「後端」分頁新增「風力」區塊）

- **即時**：
  - 風速、蒲福級數、陣風。
  - 風向：上視圖的箭頭，畫在場地方向上，同時標示真北。
  - 來源標籤：現場／Open-Meteo／測試／離線。
  - 資料多久前更新、hw 成功率、最近一次的來回時間。
  - 異常提示：風速計疑似異常、正在使用備援。
- **折線圖**：最近 60 分鐘的 hw 與 Open-Meteo（換算後）風速，兩條線一起畫，可以看出換算係數是否合理。
- **測試風**：風速滑桿、風向轉盤、剩餘時間。
- **校正**：〔以目前風向標校正〕、`northDeg`、`openmeteoScale`。
- **平滑與強風保護的設定表單**：儲存時呼叫 `PUT /api/wind/settings`。風力效果的參數在「效果」分頁調整（10.6）。
- **馬達區塊**：顯示強風停止的狀態（10.7）。
- **3D**：在風速計的位置畫一根小桿子和風向箭頭。layout 加 `windSensor: {x, y, z}`，後端驗證，由 `/api/patch` 回傳。

### 10.11 部署與現場工具

- `pyproject.toml` 加 `pyserial`（純 Python，PyPy 可以用），`uv.lock` 跟著更新。**升級到這一版時 Pi 要能連 PyPI**，現場可以上網，沒問題。
- `tph-art-museum.service` 的 `SupplementaryGroups=audio` 改成 `audio dialout`。
- `scripts/wind_probe.py`（照 hsz `test_wind.py`）：
  - 持續讀取，印出原始暫存器、換算值、來回時間。
  - 結束時印出成功率、來回時間的中位數與最大值。
  - 參數：`--port`、`--addr`、`--timeout`、`--interval`。
  - **序列埠同一時間只能給一個程式用，要先停服務再跑。**
- 每分鐘的 `output:` 統計行加上 `wind 4.2 m/s hw (om 5.1)`。
- **`doc/site-checklist.md` 新增「風速計」一節**：
  1. `ls -l /dev/serial/by-id/`：確認有 FTDI 的路徑，填進 `config.toml`。
  2. 記錄 LoRa DTU 兩台的設定值（頻段、頻道、空中速率、串口參數）。
  3. `wind_probe.py` 讀 2 分鐘：成功率 ≥ 95%，記下來回時間，並依此設定 `timeout_seconds`（建議來回時間最大值 × 2）。
  4. 轉動風杯，確認風速會變；把風向標轉一圈，確認角度連續變化。
  5. 量 `northDeg`，校正 `sensorOffsetDeg`（10.5）。
  6. 待機播放風力效果，用測試風驗證：風速 0 → 15，流動變快、顏色變化；風向轉一圈，流動方向跟著轉；切到其他效果時不受風力影響。
  7. 強風保護：用測試風超過停止門檻 → 全部緩停、前端顯示原因；降回門檻以下 → 等 `resumeSeconds`（現場可以暫時調短）→ 依控箱分區恢復。
  8. 拔掉 FTDI → 約 25 秒內來源切到 Open-Meteo；接回 → 下一次讀取就切回 hw。
  9. 關掉風速計端的 DTU 電源，結果應該跟第 8 項相同（驗證 LoRa 斷線的處理）。

### 10.12 測試

| 檔案 | 內容 |
|---|---|
| `test_modbus.py` | CRC16（拿 hsz 的已知封包比對）、組讀取要求；解析回應：正常、太短、例外碼、CRC 錯、byte count 不符；**回應分兩段到達時能累積讀完**；逾時 |
| `test_wind.py` | 用假的 reader、fetch 與可注入的時鐘測試：<br>• 來源切換：第 4 次失敗時仍是 hw，第 5 次失敗切到 openmeteo，一次成功就切回<br>• 卡住偵測；Open-Meteo 快取、過期、單位<br>• `online = false`；角度校正與 `flowDeg` 換算（含 y 軸方向）<br>• 平滑：時間常數、風向跨 0°、轉向速率上限、無風時保留風向<br>• 陣風視窗；測試風的到期；設定與色盤的驗證、原子寫檔；`calibrate`；API |
| `test_wind_serial.py` | 用 `os.openpty()` 建一對 pty 當假的風速計（含故意延遲、分段送出），測真正的 pyserial 讀取路徑。不需要硬體，Linux 與 macOS 都能跑 |
| `test_wind_guard.py` | 停止、遲滯、恢復計時；中途又超過門檻時重新計時；來源篩選；`offline` 的兩種模式；暫停保護；不修改 `motor_units`；與 `MotorGate` 一起運作：恢復時仍依控箱分區、停止後 12 秒的規則 |
| `test_wind_effects.py`＋前端 `windEffects.test.ts` | `WindPhase` 累積（風速變化時相位連續、效果開始與每段開始時歸零、轉場時兩層各自累積）；倍率與 `strength` 換算；色盤內插；風向與流動位置；**前後端一致性向量**：給定 `wind` 輸入（相位、colorSpeedMs、flowDeg），兩邊逐值相同 |
| 既有效果 | 不修改，既有一致性向量逐 byte 不變（確認風力資料沒有漏進其他效果） |

CPython、PyPy 都要通過；frontend 加上 `tsc`、build。

### 10.13 實作順序

| 階段 | 內容 | 需要現場？ |
|---|---|---|
| **M16a 資料** | `modbus.py`、`wind.py`（兩個來源、平滑、校正、override）、設定、`GET /api/wind`、`wind_probe.py`、log、測試（含 pty） | 否。可以先在開發機接 FTDI＋風速計（不經 LoRa）實測 |
| **M16b 前端與校正** | 風力區塊、折線圖、測試風、校正、設定表單、3D 風速計位置 | 否 |
| **M16c 強風保護** | `WindMotorGuard`、設定、API、前端狀態、測試 | 否。門檻值等廠商規格 |
| **M16d 風力效果** | `WindPhase`、效果 context 的 `wind` 輸入、第一個風力效果 `wind-flow`（三個群組）、風速色盤（`wind-gradient`）與編輯器、效果分頁的預覽風力、演出編輯器排除 `wind-*`、一致性向量；之後再依現場觀察增加其他風力效果 | 否 |
| **M16e 現場** | LoRa 安裝與設定、checklist 10.11、`northDeg`、`sensorOffsetDeg` 校正、`timeout_seconds` 調整；運轉數天後調整 `openmeteoScale` 與效果對應的參數 | 是 |

- M16a–c 可以先做，跟 M13（排程、待機時段）互不影響。
- M16d 只新增效果、不改既有效果，跟 M15 的清理沒有先後關係。M13 的待機排程完成後，待機時段就可以指定播放風力效果。

### 10.14 待確認

| # | 問題 | 影響 |
|---|---|---|
| ~~W1~~ | LoRa：SX1262-LoRa-DTU-HF；USB 轉 RS485：Dtech FTDI（2026-11-12 確認）。**仍待確認**：DTU 的頻段、空中速率設定；風速計端的電源 | `timeout_seconds` |
| ~~W2~~ | 風速計約 4 m 高，風向沒有對準正北（2026-11-12 確認）→ 用 `sensorOffsetDeg` 校正 | – |
| W3 | 場地 +x 軸的方位角 `northDeg`：要到現場量（10.5）；**layout 的 y 軸朝向**實作前要先確認 | 流動方向 |
| ~~W4~~ | 現場可以上網（2026-11-12 確認） | – |
| ~~W5~~ | 風越大越快並改變顏色、風向決定方向（2026-11-12 確認）；做成待機專屬的風力效果，不套用到所有效果（2026-11-12 修改）。**仍待確認**：風力效果的畫面（`wind-flow` 之外還要哪些）、預設色盤的顏色、速度倍率範圍 | M16d |
| W6 | 強風自動緩停：要做，門檻可以設定（2026-11-12 確認）。**仍待確認**：轉燈廠商的耐風規格，用來決定預設的停止與恢復門檻；Open-Meteo 是否也可以觸發停止 | M16c 預設值 |
| W7 | 風速計確切的型號／規格書，要確認暫存器是否與 hsz 相同、風速的解析度，以及**有沒有陣風暫存器**（有的話，陣風直接讀風速計的值） | M16a |

### 2026-11-12：風流（`wind-flow`）前端原型

為了先確認畫面，在前端做了 `wind-flow` 的預覽原型（**後端尚未實作**，「後端控制」模式不能套用，演出不要用）。行為經現場討論後與 10.6 的初稿不同，以這裡為準，後端實作時照這個版本：

- **轉燈**：柔和的亮帶（高斯形）沿風吹往的方向流過全場，經過每台時由上往下掃過球面（「球面上下錯開」，0 = 整顆一起亮）；風越大流得越快（基準流速 × 無風倍率 → 強風倍率）。風速**達到**「亮點門檻 X」（預設 3 m/s）時，另有一道隨機亮點帶沿風向週期性掃過全場：開始時鎖定當下風向、一定掃到底，經過的轉燈變成隨機閃爍的亮點（同色偏白），離開後恢復亮帶；風越大掃得越快（門檻風速時 2 秒、最大風速時 0.6 秒，速度線性內插），掃完隔「間隔」再來一道。低於門檻只有亮帶。
- **顏色**：沿用現有的漸層編輯器，漸層 = 風速（最左 = 無風、最右 = 最大風速），波峰再往風較大那側偏「波峰色偏」。10.6 的 `wind-gradient`（以 m/s 分段的色盤）先不做。
- **時間**：風流依**實際經過的時間**推進，不看影格編號（`frontend/src/wind/windPreview.ts` 的 `advanceWind`）。串接段落重複、效果週期歸零、3D 殘影回頭取舊影格都會讓影格變小，用影格累積會倒退或卡住。後端實作時同樣要用獨立於播放影格的時間累積（10.6 的 `WindPhase` 改為不在段落開始時歸零）。
- **預覽風力**面板（效果／色彩分頁，任一群組選了風流才出現）：風速、風往（場地方向）、平滑時間（預設 2 秒，顏色 ×3）、陣風擾動；風向每秒最多轉 3°。
- **球燈、洗牆燈**：和轉燈同一套取樣（`sampleWindBand`）：亮帶沿風向流過、達到門檻時亮點帶掃過（每顆一個顏色，所以是一顆顆隨機閃爍；亮點比例預設 0.5）。「高度錯開」> 0 時較高的燈先亮。流速、倍率、門檻、掃過時間、間隔都相同的群組共用同一個狀態，所以轉燈和球燈的亮帶、亮點帶同步；各群組的平面位置仍以自己的分布範圍為 0–1。
- **效能**：風力狀態每次畫面更新只算一次（同一個 JS 工作內共用，microtask 作廢），每顆 LED 的亮度與顏色共用一次取樣。24 台 × 129 顆每格 1.65 ms → 0.46 ms（Apple M4；中心擴散 0.37 ms），iPad mini 上預覽不再卡頓（使用者確認）。
- **待機按鈕**（模擬模式，畫面上方）：全場改播風流，不管單一效果／串接／演出；再按一次回到原本的內容。轉燈用「風流」目前的設定，球燈、洗牆燈用各群組最後調過的風流設定。只在前端；現場的待機排程仍待 M13 與後端的風流。
- 驗證：frontend 163 個測試（`effects.test.ts` 的效果 ID 清單加入 `wind-flow`）、`tsc` 通過；風流本身還沒有單元測試與前後端一致性向量。

### 2026-11-12：「風力」分頁、全部燈光亮度

- **「風力」分頁**（前端）：待機切換、預覽風力、三個群組的風流設定（參數、亮度上限、漸層）都集中在這裡。風流從一般的效果下拉選單拿掉（只當待機效果，串接、演出不能選）；待機用的風流設定獨立保存（`windPresets`），待機中可以直接調。後端仍沒有風流，「後端控制」模式不能切換待機。
- **全部燈光亮度**（後端＋前端）：`PresetStore.master_level`（0..1，只在記憶體，後端重啟後回到 1）。`PUT /api/master` `{"level": 0..1}`；`GET /api/state`、`GET /api/playback` 多了 `masterLevel`。輸出迴圈 `master_provider`：轉燈在量化成位元組時乘上（`[hardware] brightness × master`），球燈、洗牆燈乘在顏色上；效果、演出、單色測試、閃拍都算，馬達不受影響。前端「後端」分頁（關燈按鈕下方）：滑桿 0–100% 與 0%／40%／80%／100% 按鈕；3D 鏡像跟著變暗。
- 驗證：backend 236 個測試（新增 `test_master_level_scales_every_group_and_keeps_motors`、`test_master_level_api_and_playback_mirror`）；frontend 163 個測試、`tsc` 通過。開發機（`--api-only`）Playwright：按 40%／0%／80%、滑桿 65 → `/api/state` 的 `masterLevel` 為 0.4／0／0.8／0.65。**未接燈具實測**。
