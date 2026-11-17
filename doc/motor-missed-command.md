# 不重刷 XIAO 的漏訊補救方案

狀態：**評估，尚未實作**（2026-11-18）。

## 問題

現場群控時，從全部靜止按「全部馬達緩啟動」，仍有幾台沒有啟動；手動按緩停再按緩啟動就會正常運轉。WLED 的 IO13 有正確輸出。

根本原因與已完成的韌體修正見 `PLAN.md` 的「2026-11-14：XIAO 漏掉啟動／停止（D4 是 NMI），韌體改為輪詢」：D4 是 SAMD21 的 NMI 腳位，舊韌體在中斷裡設旗標，NMI 不受 `noInterrupts()` 遮蔽，邊緣會遺失。新韌體（`638e688`）改成輪詢，只在桌上一台實測過，現場其他 XIAO 仍是舊韌體。重刷 XIAO 需要到現場逐台接 USB，這份文件評估**不動 XIAO**，只用 WLED OTA 或 backend 的補救方式。

## 舊韌體的行為（決定補救方式的關鍵）

舊版（`638e688^` 的 `motor_control_io.ino`）：

1. `CHANGE` 中斷只設 `commandEdgePending`。
2. loop 看到旗標才開始去彈跳；電位維持 30 ms（`MOTOR_COMMAND_DEBOUNCE_MS`）後讀取。
3. **只有讀到的電位和記錄的 `commandStableHigh` 不同時才動作**：HIGH → 緩停；LOW 且 `commandStartArmed` 且在 `POWER_OFF` → 啟動。

所以：

- 啟動的邊緣遺失時，D4 已是 LOW，但 `commandStableHigh` 仍是 HIGH。之後沒有新的邊緣，XIAO 就不會再讀腳位，一直停在 `POWER_OFF`。手動「緩停 → 緩啟動」會產生新的邊緣，所以就恢復了。
- **任何一個邊緣都會讓 XIAO 重新讀一次電位**，而且只有在記錄和實際電位不一致時才會動作。

## 做法 A：WLED usermod 送「重讀脈衝」（建議）

指令維持不變時，WLED 定時讓 IO13 短暫反轉約 5 ms 再恢復，遠短於 XIAO 的 30 ms 去彈跳：

- 指令為運轉（IO13 輸出 LOW）：改成 `INPUT`（Hi-Z）5 ms，再回 `OUTPUT` LOW。
- 指令為停止（IO13 Hi-Z）：改成 `OUTPUT` LOW 5 ms，再回 `INPUT`。

建議時機：指令改變後 1 s、3 s 各送一次，之後每 5 s 送一次。

### 各狀態推演（舊韌體）

| XIAO 狀態 | 收到脈衝後 |
|---|---|
| 狀態正確（運轉中、停止、緩停中、預充電中） | 30 ms 後讀到的電位和 `commandStableHigh` 相同，不動作 |
| 漏了啟動（`POWER_OFF`，腳位 LOW，記錄 HIGH） | 讀到 LOW，和記錄不同 → 啟動 |
| 漏了停止（還在轉，腳位 HIGH，記錄 LOW） | 讀到 HIGH → 緩停 |
| 脈衝的邊緣本身也被 NMI 吃掉 | 不發生任何事，下一次脈衝再補 |
| 已刷新韌體（`638e688`，輪詢） | 電位要維持 30 ms 才算數，5 ms 脈衝被忽略（`firmware/tests/motor_control_io_host.cpp` 已測「短於去彈跳的 HIGH 不停止」） |

漏掉的馬達最晚幾秒內會自己補上啟動或停止，啟動和停止兩種漏訊都能補。

### 修不了的情況

- **緩停中就收到啟動指令**：舊韌體已記錄 LOW，脈衝讀到 LOW 等於沒變化，停完後不會啟動。backend 的 `restart_delay_seconds`（12 s）已經避開這種情況，必須保留。
- **XIAO 開機時腳位已是 LOW**：刻意設計，開機時的 LOW 不算新的啟動指令。

### 風險與對策

- 脈衝如果被拉長到 30 ms 以上，會讓運轉中的馬達停下來。ESP32 上 WLED 的 `loop()` 跑在 core 1，WiFi 在 core 0；用 `delayMicroseconds(5000)` busy-wait 不太會被拉長，約有 6 倍餘量。實作時應量測實際脈衝寬度，並放進 `/json/info` 的 `moonMotor`。
- 電氣：Hi-Z 時靠外部 10 kΩ 加 XIAO 內部上拉回到 HIGH，經 1 kΩ 串聯電阻，RC 時間常數在 µs 等級，5 ms 足夠。
- 脈衝期間持有 usermod 的 `mutex`，Art-Net callback 最多晚 5 ms。

### 實作範圍

- `firmware/wled/usermod_moon_motor/usermod_moon_motor.h`：`applyLocked()` 之外加脈衝計時；`writePin()` 旁加 `pulsePin()`。
- `firmware/wled/usermod_moon_motor/moon_motor_logic.h`：脈衝時機寫成可在 host 測試的純邏輯，並加測試。
- 用 OTA 更新 24 台 WLED，不用拆 XIAO。

### 驗證

把舊版韌體（`git show 638e688^:firmware/sketches/motor_control_io/motor_control_io.ino`）刷回桌上那台 XIAO，跑：

```
uv run --locked --project backend --with pyserial python backend/scripts/motor_cycle_test.py --ip 192.168.0.222 --source-ip 192.168.0.72 --xiao-port COM9 --cycles 100
```

預期：原本 `no state change at all` 的 MISS 變成延遲幾秒後才啟動或停止。`motor_cycle_test.py` 的判定時間要拉到涵蓋一次脈衝週期，或另外記錄「遲到」次數。

## 做法 B：只改 backend（只能部分改善）

限制：WLED usermod 要連續 3 個 255 封包（`RUN_FRAMES`，40 FPS 約 75 ms）才會切到運轉，所以 backend 做不出短於 30 ms 的脈衝，送出的每次反轉都會被 XIAO 當成真正的指令。backend 也沒有 XIAO 狀態回報，不知道哪幾台漏了。

### 啟動時多按一次

送 LOW 約 150 ms → HIGH 約 150 ms → LOW 保持：

- 第一次就收到的馬達還在預充電階段（啟動後 700 ms 內 PWM 尚未開始），HIGH 會讓它直接回 `POWER_OFF`，可以安全中止；第二次 LOW 再啟動。
- 第一次漏掉的馬達多一次機會。
- 漏啟動率從 p 降到約 p²（桌上實測約 5% → 約 0.25%）。

代價：

- 每次啟動，每台的 KPRE 繼電器多吸合一次（主繼電器不動）。
- 時序受 Art-Net 封包到達與 24 台之間的 jitter 影響；HIGH 必須超過 30 ms，而且整段要在 700 ms 預充電時間內。
- 如果 HIGH 的邊緣被漏掉，那台會照第一次的啟動繼續跑，結果仍正確；兩個 LOW 都漏才會失敗。

### 補停止：不安全

要補發停止就得先送 LOW，而已經停好的馬達（包含漏了啟動、停在 `POWER_OFF` 的那台）收到 LOW 會被啟動。只有在全部馬達都還在緩停中（約 6.8 s 內）時送 LOW 是無害的，但 backend 無法確認每台的實際狀態，不建議。

## 比較

| | A：WLED 重讀脈衝 | B：backend 多按一次 |
|---|---|---|
| 補漏啟動 | 幾秒內補上，可重複補 | 機率 p → p²，只補一次 |
| 補漏停止 | 可以 | 不安全 |
| 要更新的設備 | 24 台 WLED（OTA） | 只有 Pi |
| 副作用 | 脈衝寬度須 < 30 ms | 每次啟動 KPRE 多吸合一次 |
| 與新 XIAO 韌體相容 | 相容，可留著當保險 | 相容，但新韌體下多餘 |

## 建議

1. 實作做法 A，先用舊韌體在桌上驗證，再 OTA 到現場。
2. backend 的 12 s `restart_delay_seconds` 保留。
3. 之後有機會到現場刷新 XIAO（`638e688`），根本原因就解決了；脈衝可以留著當保險。
