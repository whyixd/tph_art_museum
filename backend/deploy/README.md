# Raspberry Pi 5 部署

Backend 與 build 好的前端以同一個「版本化、不可變 release」部署到 Raspberry Pi 5。Pi 不 clone repository、不 `git pull`；每次部署都指向固定 Git tag 的 GitHub Release（`whyixd/tph_art_museum`，公開 repo，不需要 token）。現場步驟見 [現場檢查清單](../../doc/site-checklist.md)。

| 檔案 | 責任 |
| --- | --- |
| `build_release.py` | 開發機：從 Git tag（非工作目錄）組出最小 tar.gz（backend runtime + 以 `npm ci`／`npm run build` 建置的 `frontend/`）、寫入 `VERSION`、產生 `SHA256SUMS`。前端建置會一併匯出 `backend/layout.example.json`（前端內建的範例場景）。 |
| `bootstrap.sh` | Pi 入口：安裝 OS 套件與 uv、（選填）保存 GitHub token，從同一 tag 取得 installer 並執行。 |
| `install-release.sh` | 下載並驗證 artifact、以 **PyPy** 建立 `.venv`、seed 持久設定與場景座標、原子切換、健康檢查與自動回退。 |
| `tph-art-museum.service` | systemd unit 模板；單一 service 輸出轉燈與 DMX 的 Art-Net，並提供控制頁面與 API；啟動前把 CPU 調速固定為 `performance`。 |

服務、帳號與路徑都叫 `tph-art-museum`；環境變數前綴為 `TPH_ART_MUSEUM_`。Python 套件名仍是 `moon_observe_backend`（沿用 moon_observe 的程式碼）。

## 主機

- **Raspberry Pi 5**（Raspberry Pi OS Bookworm 64-bit）＋**主動散熱器**（服務固定最高時脈）。
- **PyPy 3.12**（uv 管理，installer 自動安裝）。Pi 5 上 CPython 跟不上 24 台 × 129 顆 @ 40 FPS；PyPy 在 Apple M4 上全速執行時每格約 0.3–3 ms（24 台渲染＋打包＋送出，JIT 暖機後），Pi 5 預估慢 3–4 倍。
- **CPU 調速**：40 FPS 的間歇負載會讓 CPU 降頻（實測同一份程式每格時間變成約 7 倍），unit 以 `ExecStartPre=+…` 把 `scaling_governor` 設為 `performance`。
- 每 60 秒 log 一行輸出統計（見〈5. 執行模型〉）；跳格應為 0、每格最長時間應低於預算 25 ms。

```text
/opt/tph-art-museum/
├── current -> releases/v0.1.0         active release（原子切換）
├── releases/<VERSION>/                root 擁有、唯讀；內含 .venv（PyPy）、frontend/ 與 .install-complete
├── .uv-cache/ .uv-python/ .home/      service user 擁有，跨版本保留（PyPy 裝在 .uv-python）
/etc/tph-art-museum/                     root:tph-art-museum 0750
├── config.toml                        root:tph-art-museum 0640，只在缺少時 seed（現場 IP 已填好）
├── layout.json                        root:tph-art-museum 0640，只在缺少時 seed（轉燈、球燈、燈桿座標）
├── audio/                             root:tph-art-museum 0750；音樂 WAV 檔（不在 release 內，另外複製）
├── github-token                       選填；root 0600
└── preset/                            tph-art-museum 擁有；service 唯一可寫位置
    ├── preset.json
    ├── sequence.json                  UI「儲存串接」時建立
    ├── shows.json                     UI「演出」分頁儲存演出時建立（與 sequence.json 同目錄）
    ├── schedule.json                  UI「後端」分頁儲存每日排程時建立（M13；沒有檔案 = 排程停用）
    ├── beat-edits/                    時間軸「拍點修正」儲存時建立；優先於 release 內建的拍點檔
    ├── palette.json                   啟用 [weather] 且在色盤頁儲存時建立
    └── audio-calibration.json         「後端」分頁儲存延遲補償時建立
/etc/systemd/system/tph-art-museum.service
```

`config.toml` 首次由 `config.example.toml` 產生：`render.preset`、`render.sequence`、`audio.calibration` 改到 `preset/` 底下，`audio.directory` 改為 `audio`，並啟用 `render.layout = "layout.json"`；`layout.json` 由 `layout.example.json` 產生。升級不覆寫這些檔案；新版的範例 layout 若有更新，需手動比對合併。

## 音訊

音樂由 Pi 從 **USB 音效卡**播放，燈光的時間跟著音訊實際播放到的位置（PortAudio 回報的 DAC 時間）。

- OS 套件 `libportaudio2`（`bootstrap.sh` 會安裝）；沒有時服務照常輸出燈光，只是音訊停用（log 有 `warning: audio disabled`）。已裝過的 Pi：`sudo apt-get install -y libportaudio2`。
- 服務帳號經 unit 的 `SupplementaryGroups=audio` 直接使用 ALSA（`/dev/snd`）。
- `config.toml` 的 `[audio] device` 填 USB 音效卡名稱的一部分（例如 `"USB"`），空字串用系統預設（Pi 5 預設可能是 HDMI）。查名稱：

  ```bash
  aplay -l                                   # ALSA 卡號與名稱
  sudo -u tph-art-museum /opt/tph-art-museum/current/.venv/bin/python -c 'import sounddevice as sd; print(sd.query_devices())'
  ```

- 音檔不在 release 內，另外複製到 `/etc/tph-art-museum/audio/`，檔名須與拍點檔（`backend/tracks/*.json` 的 `file`）一致：

  ```bash
  scp audio/*.wav pi@<PI_HOST>:/tmp/
  sudo install -m 0640 -o root -g tph-art-museum /tmp/*.wav /etc/tph-art-museum/audio/
  ```

- 音量用音效卡的混音器調（`alsamixer -c <卡號>`，`sudo alsactl store` 保存），程式不調音量。
- 延遲補償在控制頁面「後端」分頁〈音樂播放 · 延遲校準〉調整並儲存（`preset/audio-calibration.json`）。
- 服務啟動時把所有音檔預載進記憶體（讀大檔時輸出迴圈會停頓一下，所以不在播放時才讀）；「後端」分頁的曲目清單可看是否載入完成。記憶體約等於 WAV 大小總和（目前兩首約 74 MB）。
- 演出（M12）在控制頁面「演出」分頁編輯、儲存（`preset/shows.json`）並在 Pi 上播放：音樂從音效卡出聲，燈光影格由音樂時間決定；演出播放時取代串接／單一效果、不套用天氣；套用效果、串接或手動播放音樂會停止演出。沒有音效卡（音訊停用）時演出以系統時鐘無聲播放。

## 網路

Art-Net 網段是 `2.0.0.0/8`：轉燈 2.0.0.201–224、DMX 轉換器 2.0.0.101–112（見 `config.example.toml`）。Pi 的 Ethernet 要設成 **2.0.0.10**（與 `artnet.source_ip` 相同），**不要**設預設閘道。

`source_ip` 同時是安全閥：2.0.0.0/8 是公用網段，Pi 沒有 2.0.0.10 時（例如不在現場網路、eth0 沒接線）Art-Net 暫停、一個封包都不送，不會經 Wi-Fi 的預設閘道送上網際網路。服務與控制頁面照常運作，頁面「後端」分頁與日誌會顯示「Art-Net 暫停」；每 2 秒檢查一次，位址出現（接上網路線）後自動開始送，送到一半位址消失也會再暫停。（2026-11-11 前是整個服務啟動失敗、每 3 秒重啟。）不在現場測試時可以加一張虛擬網卡，封包送進去就丟掉：`sudo ip link add dummy0 type dummy && sudo ip addr add 2.0.0.10/8 dev dummy0 && sudo ip link set dummy0 up`（重開機後消失）。

```bash
nmcli con show            # 有線連線名稱：Pi OS（Debian 13）為 "netplan-eth0"，舊版可能是 "Wired connection 1"
sudo nmcli con mod netplan-eth0 connection.autoconnect yes ipv4.method manual ipv4.addresses 2.0.0.10/8 \
  ipv4.gateway "" ipv4.dns "" ipv4.never-default yes ipv6.method disabled
sudo nmcli con up netplan-eth0
ip route                  # default 仍走 wlan0；2.0.0.0/8 走 eth0（沒接線時顯示 linkdown，封包在 eth0 丟棄）
```

設定會寫進 `/etc/netplan/90-NM-*.yaml`，重開機後保留。沒接線時 NetworkManager 不會啟用這個連線，eth0 沒有 2.0.0.10（2026-11-11 在 Pi 上實測），Art-Net 會暫停（見上）；接上線後 NetworkManager 套用位址，輸出自動開始。

控制頁面（`http://<Pi>:8765/`）的連法二選一：

- **A. 兩個網路**：Ethernet 接 Art-Net，控制用 Wi-Fi（`wlan0`）或 USB 網卡接館方網路。`source_ip = "2.0.0.10"` 讓 Art-Net 從 Ethernet 送出。
- **B. 同一個網路**：控制用的筆電也接到 Art-Net 交換器，設 2.0.0.x 的固定 IP（避開 101–112、201–224）。

API 沒有驗證，只接在受信任的網路。

## 1. 建置與發布（開發機）

Artifact 只從 tag 內容產生，未 commit 的修改不會進入 release。開發機需要 Node.js／npm 與 uv。

```bash
git tag v0.1.0
uv run --project backend python backend/deploy/build_release.py v0.1.0   # → dist/tph-art-museum-v0.1.0.tar.gz、dist/SHA256SUMS
git push origin main
git push origin v0.1.0
gh release create v0.1.0 dist/tph-art-museum-v0.1.0.tar.gz dist/SHA256SUMS \
  backend/deploy/bootstrap.sh backend/deploy/install-release.sh \
  --repo whyixd/tph_art_museum --title v0.1.0 --generate-notes
```

版本格式為 `vMAJOR.MINOR.PATCH`，可加 `-rc.1` 之類後綴；builder 與兩個 shell script 使用同一 regex（由測試檢查）。同版本重建須加 `--force`，且同一 tag 重建的檔案位元相同。

## 2. 首次安裝（Pi）

```bash
# 從開發機複製（bootstrap.sh 不含秘密）
scp backend/deploy/bootstrap.sh pi@<PI_HOST>:/tmp/
# 在 Pi 上（需要網際網路下載 uv、PyPy 與 release）
sudo bash /tmp/bootstrap.sh v0.1.0          # 詢問 GitHub token 時直接 Enter（公開 repo）
```

首次安裝會 enable service，但**不啟動**。確認設定後再啟動：

```bash
sudo nano /etc/tph-art-museum/config.toml     # 多網卡時設 artnet.source_ip；IP、patch 已是現場值
sudo -u tph-art-museum /opt/tph-art-museum/current/.venv/bin/python /opt/tph-art-museum/current/run.py \
  --config /etc/tph-art-museum/config.toml --patch-report        # DMX 位址表（對照現場）
sudo systemctl start tph-art-museum
journalctl -u tph-art-museum -f               # 啟動訊息；60 秒後出現第一行 "output: ..."
```

### 離線安裝（不經 GitHub）

現場沒有網際網路，或還沒發布 release 時：在開發機 `build_release.py`，把壓縮檔與 `SHA256SUMS` 複製到 Pi 的同一個目錄，以 `TPH_ART_MUSEUM_ARCHIVE` 指定。installer 一樣會比對 SHA-256。Pi 仍需要 uv 與 PyPy（首次安裝需網路下載；之後的版本若 PyPy 已在 `.uv-python` 則不需要）。

```bash
scp dist/tph-art-museum-v0.1.0.tar.gz dist/SHA256SUMS backend/deploy/install-release.sh pi@<PI_HOST>:/tmp/
# Pi 上（首次：先裝 uv，見 bootstrap.sh）
sudo TPH_ART_MUSEUM_ARCHIVE=/tmp/tph-art-museum-v0.1.0.tar.gz bash /tmp/install-release.sh v0.1.0
```

## 3. 升級

```bash
sudo bash /tmp/bootstrap.sh v0.2.0
# 或已安裝過時直接用 active release 內的 installer：
sudo bash /opt/tph-art-museum/current/deploy/install-release.sh v0.2.0
```

順序：下載 → SHA-256 驗證 → 解壓 → `uv sync --frozen --python pypy@3.12`（確認是 PyPy）→ 以新版執行 `run.py --check` 驗證現有設定 → 原子切換 `current` → restart → 健康檢查。切換前任何一步失敗，服務仍跑舊版。中斷而殘留的不完整 release 目錄（缺 `.install-complete`）會在下次安裝時自動清除。

健康檢查：在 20 秒內 service 為 active、`http://127.0.0.1:8765/api/state` 與控制頁面 `http://127.0.0.1:8765/` 都回應，且 3 秒後仍正常、沒有自動重啟。失敗時自動切回前一版並 restart，installer 以非 0 結束。成功後保留最近 3 個 release（另外一定保留 current 與前一版）。

## 4. 回退

對已安裝的版本重跑 installer，不需網路，同樣會驗證設定並做健康檢查：

```bash
sudo bash /opt/tph-art-museum/current/deploy/install-release.sh v0.1.0
ls /opt/tph-art-museum/releases        # 可用版本
```

## 5. 執行模型

- 以無登入權限的 system user `tph-art-museum` 執行 `/opt/tph-art-museum/current/.venv/bin/python run.py --config /etc/tph-art-museum/config.toml`（PyPy），不經 `uv run`。
- 啟動前（root）把每顆 CPU 的 `scaling_governor` 設為 `performance`；沒有 cpufreq 的機器略過。
- `KillSignal=SIGINT`：stop/restart 時程式走正常退出：先對馬達補送 `0`（若先前為 `255`），再對每個 DMX universe 送兩次全 0。此功能不是緊急停止；斷電或當機時，WLED usermod 在 1 秒沒收到 Art-Net 後停馬達（緩停約 10 秒）。
- **輸出統計**：每 60 秒 log 一行，例如 `output: 2400 frames in 60 s, 0 skipped, render 2.1 ms avg / 7.9 ms max (budget 25.0 ms)`。`GET /api/output` 回傳同一份資料，前端「後端」分頁也會顯示。跳格（skipped）不為 0 表示主機跟不上。

  ```bash
  journalctl -u tph-art-museum | grep 'output:' | tail
  ```

- `Restart=on-failure`、`After=network-online.target`、開機自動啟動；log 進 journald。
- `ProtectSystem=strict`，只有 `/etc/tph-art-museum/preset` 可寫。

## 6. 可覆寫參數

在 `sudo` 後以環境變數傳入，例如 `sudo TPH_ART_MUSEUM_KEEP_RELEASES=5 bash install-release.sh v0.2.0`。

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| `TPH_ART_MUSEUM_REPOSITORY` | `whyixd/tph_art_museum` | GitHub owner/repo |
| `TPH_ART_MUSEUM_PYTHON` | `pypy@3.12` | uv 的 Python 版本；`pypy*` 時會確認環境真的是 PyPy |
| `TPH_ART_MUSEUM_ARCHIVE` | 無 | 本機 release 壓縮檔（旁邊要有 `SHA256SUMS`），不從 GitHub 下載 |
| `TPH_ART_MUSEUM_INSTALL_ROOT` | `/opt/tph-art-museum` | release 與 uv cache 根目錄 |
| `TPH_ART_MUSEUM_CONFIG_ROOT` | `/etc/tph-art-museum` | 設定、layout、preset 根目錄 |
| `TPH_ART_MUSEUM_GITHUB_TOKEN_FILE` | `<CONFIG_ROOT>/github-token` | 選填的 token 檔（私有 repo 或 API 次數限制時） |
| `GITHUB_TOKEN` | 無 | 直接執行 installer 時暫時提供 token |
| `TPH_ART_MUSEUM_NO_TOKEN` | 無 | bootstrap 不詢問 token |
| `TPH_ART_MUSEUM_SERVICE_USER` | `tph-art-museum` | service 帳號 |
| `TPH_ART_MUSEUM_KEEP_RELEASES` | `3` | 保留的 release 數 |
| `TPH_ART_MUSEUM_HEALTH_URL` | `http://127.0.0.1:8765/api/state` | 健康檢查 endpoint |
| `TPH_ART_MUSEUM_PAGE_URL` | `http://127.0.0.1:8765/` | 健康檢查的前端頁面 |
| `TPH_ART_MUSEUM_HEALTH_TIMEOUT` | `20` | 健康檢查秒數 |

Unit 由模板產生，路徑與帳號跟著上述變數一起代入；改 root 時 installer 與 service 不會不一致。若改過 root，之後升級須帶相同變數。

## 7. 現況限制

- 尚未在 Pi 5 實機跑過（PyPy 與 governor 的效果以 Apple M4 量測推估）；第一次安裝後請看輸出統計。
- 沒有 CI 自動建置與發布，也沒有獨立簽章；checksum 與 archive 來自同一個 GitHub Release，只能偵測傳輸損壞。
- uv 由官方 install script 安裝，未固定版本；PyPy 版本由 uv 決定（3.12 系列）。
- 健康檢查只確認程式與本機 API 存活；`stupidArtnet` 不提供接收確認，燈與馬達實際行為仍需現場確認。
