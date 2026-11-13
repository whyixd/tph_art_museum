# 現場檢查清單

美術館現場安裝與驗收。依序做，每項完成後打勾；有出入的數字記在最後的〈待回填〉。

設定與 IP 以 `backend/config.example.toml` 為準（來源：`doc/新北美_ip控箱.pdf`）；座標以 `backend/layout.example.json` 為準（來源：`doc/spaces05.png`）。部署細節見 [backend/deploy/README.md](../backend/deploy/README.md)。

## 0. 出發前（開發機）

- [ ] 所有測試通過：`uv run --project backend python -m unittest discover -s backend/tests`、`cd frontend && npm test`
- [ ] 打 tag、`build_release.py`、`gh release create`（見部署說明〈1〉）
- [ ] WLED 韌體：`firmware/wled/build.sh` 產生 `dist/WLED_v0.15.4_moon_eth.bin`（`firmware/wled/README.md`）
- [ ] XIAO 馬達控制 sketch：`firmware/sketches/motor_control_io`，確認 `TARGET_PWM_DUTY`
- [ ] 帶：Pi 5（含主動散熱器、電源）、Ethernet 線、筆電（Node 不需要）、USB-C 線（刷 WLED／XIAO）

## 1. 網路

- [ ] Art-Net 交換器：轉燈 2.0.0.201–224、DMX 轉換器 2.0.0.101–112、洗牆燈轉換器（暫定 2.0.0.100）都接上
- [ ] Pi Ethernet 固定 2.0.0.10/8、無閘道（部署說明〈網路〉）
- [ ] 控制頁面的連法（A：Wi-Fi／第二張網卡；B：筆電也接 2.0.0.x）
- [ ] `ping 2.0.0.201`…`2.0.0.224`、`ping 2.0.0.101`…`2.0.0.112`（104 沒有球燈，可略）、`ping 2.0.0.100`（洗牆燈）

## 2. 轉燈（每台，unit n = 2.0.0.(200 + n)）

- [ ] 刷 WLED（`firmware/wled/flash.py`），套用 `unit_settings.json`：Art-Net、universe 0、Multi RGB、起始位址 1、IO16 共 129 顆、色序 RGB（已刷過的板子用 `flash.py config <IP>` 只更新設定）
- [ ] 色序：「後端」分頁單色測試送紅／綠／藍，燈條顏色相同（紅綠對調 = WLED `order` 不對）
- [ ] WLED 網路設定為 2.0.0.(200 + n)
- [ ] 馬達接線 IO13 → XIAO **D4**（接法同 `firmware/wled/wiring-io13-d8.svg`，圖上仍標 D8），XIAO 燒 `motor_control_io`（2026-11-14 之後的輪詢版）
- [ ] **編號對位置**：「後端」分頁的單色測試逐台點亮，確認實體位置與控制頁面全場 3D 的編號一致（每根柱子由上往下編號，2026-11-12 現場確認；`doc/spaces05_annotated_*.png` 仍是舊的由下往上編號，不要照圖）
- [ ] **方向測試（M4）**：馬達停止時，套用

  ```json
  {"schemaVersion":2,"effectId":"full-on","parameters":{},"color":{"mode":"gradient","axis":"radius","colors":[{"r":1,"g":0,"b":0},{"r":0,"g":0,"b":1}]}}
  ```

  三條燈條都應頂部紅、底部藍、左右對稱。紅在底部 → 第 0 顆不在頂部（改 `[hardware] pixel_rotation`）；不對稱 → 繞行起點偏移。
- [ ] 馬達：「開啟緩啟動」→ 轉起來；「緩停關閉」→ 約 10 秒停下

## 3. 球燈（DMX）

- [ ] 每個控箱的 DMX 轉換器 IP 2.0.0.(101–112)、Art-Net universe 0
- [ ] `--patch-report` 與現場位址對照（**Q2**：目前假設第 n 顆起始通道 4(n−1)+1；若每個控箱從 1 重新編，改 `[[dmx.patch]]` 的 `address`）
- [ ] **對燈模式**（「後端」分頁）：從 1 號開始「只亮這顆」，用 → 逐顆走完 37 顆，確認每顆位置與控制頁面全場 3D 的編號一致（每根柱子由上往下：同柱最上面是最小號）
- [ ] 「全部亮白」→ 37 顆全亮；「結束測試」→ 恢復播放

## 3.1 洗牆燈（DMX）

- [ ] 洗牆燈轉換器的實際 IP（設定暫定 2.0.0.100）、Art-Net universe 0；不同時改 `[[dmx.nodes]]` 的 `wash`
- [ ] 25 顆位址 1, 5, 9 … 97（每顆 R G B W 四通道）與 `--patch-report` 一致
- [ ] **對燈模式**選洗牆燈：逐顆「只亮這顆」，記下每顆的實際位置，回填 `layout.json` 的 `fixtures.wash`（目前暫定沿後牆地面等距、x = 75 + 150 ×（編號 − 1））
- [ ] 「全部亮白」→ 25 顆全亮、牆面亮度可接受（太亮就調 `wash-rgbw` 的 `brightness`）；「結束測試」→ 恢復播放

## 4. Pi 與服務

- [ ] `sudo bash bootstrap.sh <版本>`（token 直接 Enter）
- [ ] `config.toml`：多網卡時設 `artnet.source_ip`
- [ ] 「後端」分頁沒有「Art-Net 暫停」（有的話：eth0 沒接上或位址不是 `source_ip`；`ip -br addr show eth0`）
- [ ] `sudo systemctl start tph-art-museum`；`journalctl -u tph-art-museum -f`
- [ ] 60 秒後 `output:` 行：**0 skipped**，`max` 低於 25 ms（播放最慢的效果「輪盤收束」與轉場時再看一次）
- [ ] 控制頁面全場 3D：24 台轉燈、37 顆球燈、15 根燈桿位置正確

## 4.1 音訊與延遲校準

- [ ] USB 音效卡接上 Pi 與音響；`aplay -l` 看得到；`config.toml` 的 `[audio] device` 填卡名的一部分
- [ ] 音檔複製到 `/etc/tph-art-museum/audio/`（部署說明〈音訊〉）；「後端」分頁的曲目清單沒有「音檔不在主機上」
- [ ] 音量：`alsamixer -c <卡號>` 調好後 `sudo alsactl store`
- [ ] **延遲校準**：〈音樂播放 · 延遲校準〉選「節拍器」、勾「閃拍」與「循環」→ 播放。站在觀眾位置，燈比聲音慢就往正調、快就往負調，對齊後按「儲存」
- [ ] 用聖誕曲再聽一次閃拍（拍點由分析而來，可能有個別誤差；小節第一拍目前假設為第 0 拍）
- [ ] 重新啟動服務後，延遲補償仍是儲存的值
- [ ] **演出**：「演出」分頁選一個已儲存的演出 →「在 Pi 上播放這個演出」。段落在指定的小節切換、轉場對拍；「停止演出」後回到串接
- [ ] 演出播放中看 `GET /api/output`：沒有跳格（音檔已在啟動時預載）

## 5. 安全與恢復

- [ ] `sudo systemctl stop tph-art-museum`：馬達緩停、球燈全暗
- [ ] 重新開機：服務自動啟動、恢復串接播放（馬達預設關閉，需手動開啟）；啟用每日排程後燈光依當時時間套用（待機效果／關燈），馬達仍不會轉，要在「後端」分頁「每日排程」按一次確認
- [ ] 每日排程（「後端」分頁）：把啟動／關閉時間與表演時間暫時設在幾分鐘後，確認開門（馬達啟動、待機效果）、表演（演出播放、播完回待機）、關門（馬達緩停、勾選的燈組關閉）；`timedatectl` 確認時間與時區（Asia/Taipei）
- [ ] 拔掉 Pi 的 Ethernet：各台馬達在約 1 秒後開始緩停（WLED usermod 逾時）
- [ ] 升級演練：裝新版 → 健康檢查通過；回退演練：`install-release.sh <舊版>`

## 6. 待回填

| 項目 | 目前值 | 現場值 |
|---|---|---|
| 燈桿高度（`layout.json` `poles`） | 5／6／6.5／8／10／13／15 m（由圖面量測取標準長度） | |
| 轉燈中心高度（3、10、11、13、14、18、21 號為估計） | 見 `layout.json` | |
| 轉燈 RPM、亮度上限 | `rpm = 190`（模擬用）、`brightness` 未設 | |
| 球燈位址方式（Q2） | 編號 n → 起始通道 4(n−1)+1 | |
| 洗牆燈轉換器 IP | 2.0.0.100（暫定） | |
| 洗牆燈位置（`layout.json` `fixtures.wash`） | 沿後牆地面等距（暫定） | |
| Pi 的 Art-Net IP | 2.0.0.10（建議） | |
| USB 音效卡名稱（`[audio] device`） | 未定 | |
| 延遲補償 | 0 ms | |
| 控箱內轉燈 IP 對應 | 同柱 IP 靠前 = 較高那台（2026-11-12 現場確認由上往下編號）；14 號在球燈 17–20 柱上最低（待確認） | |
