# 韌體測試

2026-11-10 從 `~/Dev/moon_observe/firmware/tests` 補搬（當初匯入韌體時漏了）。編譯產物（`*.exe`）不進版控。

| 測試 | 對象 | 執行 | 狀態 |
|---|---|---|---|
| `test_flash.py` | `wled/flash.py` 刷機工具 | `uv run --project backend python -m unittest firmware/tests/test_flash.py`（需要 Python 3.10 以上） | 40 個通過 |
| `moon_motor_logic_host.cpp` | WLED usermod 的馬達判斷（`wled/usermod_moon_motor/moon_motor_logic.h`） | `c++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/moon_motor_logic_host.cpp -o /tmp/t && /tmp/t` | 通過 |
| `motor_control_io_host.cpp` | XIAO 馬達控制 sketch（`sketches/motor_control_io`）。2026-11-14 改為 D4、輪詢（沒有中斷）：去彈跳、上電順序、緩停中的啟動在停完後補上、開機 LOW 不啟動 | `g++ -std=c++17 -Wall -Wextra -pedantic firmware/tests/motor_control_io_host.cpp -o firmware/tests/motor_control_io_host.exe && firmware/tests/motor_control_io_host.exe` | 通過 |
| `motor_pwm_test_host.cpp` | `sketches/motor_pwm_test` | 同上 | **失敗（已知）**：測試預期 PWM 每 20 ms 加 5，sketch 目前是 `PWM_RAMP_INTERVAL_MS = 80`、`PWM_RAMP_STEP = 1`。moon_observe 原 repo 一樣失敗，是測試沒跟著 sketch 更新；要改測試還是 sketch 待決定 |

`moon_observe` 的 `motor_control_host.cpp`（舊的 `motor_control` sketch）沒有搬，因為本 repo 沒有那個 sketch。
