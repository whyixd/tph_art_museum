# Moon Observe — Motor Power Sequencing Firmware Specification

## 1. 目的

本文件提供給 Codex 作為 SAMD21 韌體實作依據。

目前硬體已加入 **48V 馬達驅動器順序通電（Power Sequencing）**，採用：

- 主繼電器 `KMAIN`
- 預充繼電器 `KPRE`
- 預充電阻 `RPRE`
- SAMD21 控制兩顆 Relay
- SAMD21 PWM 控制 Motor Driver

SAMD21 的責任只限於馬達控制與安全狀態；LED pattern 與 Art-Net 由 Raspberry Pi backend 負責，LED strip output 由 Gledopto GL-C-616WL 負責。

本階段目標：

1. 上電時先對 Motor Driver 輸入端預充電。
2. 預充完成後閉合主繼電器。
3. 主繼電器穩定後解除預充支路。
4. 最後才開始 Motor PWM soft-start。
5. 關機時先將 Motor PWM 降到 0，再斷開主繼電器。
6. MCU boot/reset 時兩顆 Relay 必須預設為 OFF。

本階段**不實作獨立硬體 fail-safe / external watchdog / emergency load break**。

---

## 2. 系統硬體架構

```text
                           +48V PSU
                              |
                    +---------+---------+
                    |                   |
                 [FUSE]              [48V -> 12V]
                    |                   |
                    |                   +----> +12V
                    |                           |
                    |                           +----> [12V -> 5V] -> SAMD21
                    |                           |
                    |                           +----> ARTNET Controller -> LED Strip
                    |
          +---------+------------------------------+
          |                                        |
      [KMAIN]                                  [RPRE]
  主功率 Relay                                  47R
          |                                        |
          |                                    [KPRE]
          |                                  預充 Relay
          |                                        |
          +-------------------+--------------------+
                              |
                         MOTOR_48V
                              |
                       Motor Driver
                              |
                        Brushless Motor


SAMD21
  |
  +---- MAIN_RELAY  ----> AO3400A ----> KMAIN coil
  +---- PRE_RELAY   ----> AO3400A ----> KPRE coil
  +---- MOTOR_PWM   -------------------> Motor Driver PWM
```

---

## 3. 主要硬體元件

### KMAIN

目前設計：

- Panasonic `ALFG2PF12`
- 12V coil
- 主 48V Motor Driver 電源開關

功能：

- 正常運轉時承載 Motor Driver 的 48V 電源
- Motor Driver 工作電流約 `2 ~ 5 A`
- KMAIN 閉合之前，Motor Driver 輸入電容應先經過 pre-charge

### KPRE

目前設計：

- Omron `G5LE-1`
- 12V coil

功能：

- 僅在預充階段導通
- 與 `RPRE` 串聯
- 預充完成且 KMAIN 閉合後必須關閉

### RPRE

目前設計：

```text
47R / 10W Pulse-rated
```

用途：限制 Motor Driver 輸入電容的充電湧浪。

理想初始預充電流約：

```text
48V / 47R ≈ 1.02A
```

第一版韌體先採固定延遲；未來可改為 ADC 偵測 `MOTOR_48V`。

### Relay Driver

兩顆 Relay 都使用：

```text
SAMD21 GPIO 3.3V
        |
       100R
        |
     AO3400A Gate
        |
       10k
        |
       GND
```

AO3400A 作為 low-side relay coil driver。

Relay coil：

```text
+12V
  |
Relay Coil
  |
AO3400A Drain
  |
AO3400A
  |
 GND
```

每顆 Relay coil 並聯一顆 `SS14` 作為 flyback diode。

SS14 極性：

```text
Cathode (K) -> +12V
Anode   (A) -> Relay Coil low side / AO3400A Drain
```

SS14 僅位於 12V coil domain，不承受 48V Motor bus。

---

## 4. MCU 與訊號

MCU：

```text
SAMD21
```

MCU 供電：

```text
5V rail
```

GPIO logic：

```text
3.3V
```

韌體請使用以下邏輯名稱，不要在核心邏輯中硬編碼實體 pin number：

```text
MOTOR_PWM
PRE_RELAY
MAIN_RELAY
```

實際 pin mapping 應由現有 board definition / schematic 決定。

如需新增 pin 定義，集中放在單一硬體設定檔或 board config 中。

---

## 5. Relay GPIO 邏輯

AO3400A 為 low-side switching，因此：

```text
GPIO HIGH -> AO3400A ON -> Relay Coil energize -> Relay ON
GPIO LOW  -> AO3400A OFF -> Relay OFF
```

定義：

```text
PRE_RELAY HIGH  = KPRE ON
PRE_RELAY LOW   = KPRE OFF

MAIN_RELAY HIGH = KMAIN ON
MAIN_RELAY LOW  = KMAIN OFF
```

---

## 6. Boot 初始狀態

MCU 啟動後必須優先設定：

```text
MOTOR_PWM = 0
PRE_RELAY = LOW
MAIN_RELAY = LOW
```

要求：

1. GPIO 初始化時 relay output 必須立即為 LOW。
2. PWM output 必須立即為 0 duty。
3. 未收到有效的 explicit motor start command，或任何 motor-local 初始化失敗時，不得自動讓 Motor Relay ON。
4. 只有 explicit motor-power-start sequence 才能開啟 KPRE / KMAIN。

---

## 7. 上電流程

第一版使用固定時間。

預設參數：

```text
PRECHARGE_TIME_MS      = 500
MAIN_RELAY_SETTLE_MS   = 100
POST_PRECHARGE_OFF_MS  = 100
```

以上參數需集中定義，方便硬體測試後調整。

### Sequence

```text
START
  |
  v
Motor PWM = 0
  |
  v
KMAIN OFF
  |
  v
KPRE ON
  |
  | 500ms
  v
KMAIN ON
  |
  | 100ms
  v
KPRE OFF
  |
  | 100ms
  v
Motor PWM soft-start
  |
  v
RUN
```

對應操作概念：

```text
motor_pwm_set(0)

main_relay_off()
pre_relay_on()

wait PRECHARGE_TIME_MS

main_relay_on()

wait MAIN_RELAY_SETTLE_MS

pre_relay_off()

wait POST_PRECHARGE_OFF_MS

motor_soft_start()
```

---

## 8. Motor PWM Soft-start

Power sequencing 完成以前：

```text
PWM duty = 0
```

只有在：

```text
KMAIN = ON
KPRE  = OFF
```

狀態下才允許開始 PWM ramp。

若專案已有 PWM soft-start，保留既有實作。

若目前沒有，建議將 soft-start 抽象成：

```text
motor_set_target_duty(target)
```

由內部逐步 ramp 至 target duty。

Ramp 參數應集中設定，例如：

```text
MOTOR_PWM_RAMP_STEP
MOTOR_PWM_RAMP_INTERVAL_MS
```

---

## 9. 正常關機流程

```text
RUN
 |
 v
PWM ramp -> 0
 |
 v
確認 PWM = 0
 |
 v
等待 Motor 停止
 |
 v
KMAIN OFF
 |
 v
確認 KPRE OFF
 |
 v
POWER OFF STATE
```

第一版：

```text
MOTOR_STOP_SETTLE_MS = 300
```

操作概念：

```text
motor_soft_stop()

wait until PWM == 0

wait MOTOR_STOP_SETTLE_MS

main_relay_off()
pre_relay_off()
```

正常 shutdown 不應先切 KMAIN 再停止 PWM。

---

## 10. 建議狀態機

Power sequence 優先做成 non-blocking state machine，不要在 main loop 大量使用 blocking delay。

建議狀態：

```text
POWER_OFF
PRECHARGING
MAIN_RELAY_SETTLING
PRECHARGE_RELEASE
MOTOR_READY
MOTOR_RUNNING
MOTOR_STOPPING
POWER_DOWN
```

推薦轉移：

```text
POWER_OFF
   |
   | start request
   v
PRECHARGING
   |
   | PRECHARGE_TIME_MS elapsed
   v
MAIN_RELAY_SETTLING
   |
   | MAIN_RELAY_SETTLE_MS elapsed
   v
PRECHARGE_RELEASE
   |
   | POST_PRECHARGE_OFF_MS elapsed
   v
MOTOR_READY
   |
   | target PWM > 0
   v
MOTOR_RUNNING
   |
   | stop request
   v
MOTOR_STOPPING
   |
   | PWM == 0
   v
POWER_DOWN
   |
   v
POWER_OFF
```

---

## 11. 狀態輸出規則

| State | KPRE | KMAIN | PWM |
|---|---:|---:|---:|
| POWER_OFF | OFF | OFF | 0 |
| PRECHARGING | ON | OFF | 0 |
| MAIN_RELAY_SETTLING | ON | ON | 0 |
| PRECHARGE_RELEASE | OFF | ON | 0 |
| MOTOR_READY | OFF | ON | 0 |
| MOTOR_RUNNING | OFF | ON | >0 |
| MOTOR_STOPPING | OFF | ON | ramp to 0 |
| POWER_DOWN | OFF | OFF | 0 |

任何未定義 / invalid state：

```text
PWM = 0
KPRE = OFF
KMAIN = OFF
state -> POWER_OFF
```

---

## 12. API 建議

硬體層：

```text
main_relay_on()
main_relay_off()

pre_relay_on()
pre_relay_off()

motor_pwm_set(...)
motor_pwm_get(...)
```

Power sequence 層：

```text
motor_power_init()
motor_power_start()
motor_power_stop()
motor_power_update()
motor_power_get_state()
```

Motor command：

```text
motor_set_target_pwm(...)
motor_stop()
```

若現有專案已有 naming convention，優先沿用既有架構。

---

## 13. Blocking / Non-blocking 要求

優先：

```text
millis()/system tick + state machine
```

不要在主要執行流程中直接使用：

```text
delay(500)
delay(100)
```

等長時間 blocking delay。

原因：

- MCU 仍需處理 motor command、sensor sampling 與 safety state
- 未來容易加入 RPM、ADC 或 fault detection
- LED / Art-Net servicing 不在 SAMD21 執行

---

## 14. PWM Interlock

韌體必須保證：

```text
if state != MOTOR_READY && state != MOTOR_RUNNING:
    Motor PWM 不得輸出非 0 duty
```

特別是以下狀態：

```text
PRECHARGING
MAIN_RELAY_SETTLING
PRECHARGE_RELEASE
```

期間 PWM 強制為 0。

即使外部 command 要求非零 PWM，也應保存 target，等 `MOTOR_READY` 後才開始 ramp。

---

## 15. Relay 不允許的組合

正常運作時：

```text
KMAIN OFF + KPRE ON
```

只允許出現在 `PRECHARGING`。

正常運轉：

```text
KMAIN ON
KPRE OFF
```

KMAIN 與 KPRE 同時 ON 只允許在 `MAIN_RELAY_SETTLING` 的短暫 transition。

不應長時間保持：

```text
KMAIN ON
KPRE ON
```

---

## 16. MOTOR_48V

硬體目前有：

```text
MOTOR_48V
```

作為 KMAIN / KPRE 後的 Motor Driver power rail。

第一版韌體：

```text
不需要 ADC feedback
```

未來可增加：

```text
MOTOR_48V voltage sense
```

將流程升級成：

```text
KPRE ON
   |
   v
等待 MOTOR_48V > threshold
   |
   v
KMAIN ON
```

取代固定 `PRECHARGE_TIME_MS`。

本次不要自行加入尚未存在的 ADC 硬體功能。

---

## 17. Debug / Logging

目前硬體已有：

- MAIN relay coil indicator LED
- MOTOR_48V indicator LED

韌體不需要控制這兩顆 LED。

若專案已有 serial logging，可在 state transition 輸出：

```text
[MOTOR_PWR] POWER_OFF -> PRECHARGING
[MOTOR_PWR] PRECHARGING -> MAIN_RELAY_SETTLING
[MOTOR_PWR] MAIN_RELAY_SETTLING -> PRECHARGE_RELEASE
[MOTOR_PWR] PRECHARGE_RELEASE -> MOTOR_READY
```

Logging 不應造成 power sequence blocking。

---

## 18. 不在本次範圍

本次不要自行加入：

- LED pattern generation
- Art-Net / ArtSync transmission
- Gledopto GL-C-616WL / WLED controller logic
- External watchdog
- Emergency stop
- Hardware safety relay
- MCU-independent fail-safe
- Current sensing
- 48V ADC sensing
- Automatic relay fault detection
- SSR control
- Motor Driver power-good feedback
- Dynamic pre-charge timing
- Pre-charge resistor thermal model

除非現有 codebase 已經有相關功能，否則不要擴大 scope。

---

## 19. Codex 實作要求

Codex 執行時請：

1. 先閱讀現有 codebase。
2. 找出既有：
   - GPIO abstraction
   - PWM implementation
   - main loop / scheduler
   - timing API
   - configuration style
3. 優先沿用現有架構。
4. 不要重寫與本功能無關的模組。
5. 新增 power-sequencing module。
6. 將 timing parameters 集中設定。
7. 保持 GPIO pin mapping 與 sequencing logic 分離。
8. Boot 時確保 relay LOW / PWM 0。
9. 實作 non-blocking state machine。
10. 若已有 logging，加入 state transition logging。
11. 完成後列出修改檔案與主要邏輯。
12. 若實際 schematic / board pin mapping 與本文件不同，以專案內最新版硬體定義為準，不要猜 pin number。

---

## 20. 驗收條件

### Boot

```text
KPRE  = OFF
KMAIN = OFF
PWM   = 0
```

### Start

```text
KPRE ON
↓
500ms
↓
KMAIN ON
↓
100ms
↓
KPRE OFF
↓
100ms
↓
PWM ramp
```

### Running

```text
KPRE = OFF
KMAIN = ON
PWM = requested/ramped duty
```

### Stop

```text
PWM ramp -> 0
↓
300ms
↓
KMAIN OFF
KPRE OFF
```

### Invalid state

```text
PWM = 0
KPRE = OFF
KMAIN = OFF
state -> POWER_OFF
```

---

## 21. 初始 Timing Constants

第一版：

```text
PRECHARGE_TIME_MS       = 500
MAIN_RELAY_SETTLE_MS    = 100
POST_PRECHARGE_OFF_MS   = 100
MOTOR_STOP_SETTLE_MS    = 300
```

全部需集中定義，方便實機測試後修改。

---

## 22. 硬體 / 軟體責任分工

```text
RPRE
  -> 限制 Motor Driver input capacitor inrush

KPRE
  -> 建立預充支路

KMAIN
  -> 正常 Motor Driver 48V 主供電

AO3400A
  -> SAMD21 3.3V GPIO 驅動 12V relay coil

SS14
  -> Relay coil flyback suppression

SAMD21
  -> Power sequence
  -> Relay control
  -> Motor PWM soft-start / soft-stop
```

韌體核心原則：

> **Motor Driver 在完成 pre-charge 並閉合 KMAIN 之前，不得收到非零 PWM。**
