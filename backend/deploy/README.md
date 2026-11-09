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
├── github-token                       選填；root 0600
└── preset/                            tph-art-museum 擁有；service 唯一可寫位置
    ├── preset.json
    ├── sequence.json                  UI「儲存串接」時建立
    └── palette.json                   啟用 [weather] 且在色盤頁儲存時建立
/etc/systemd/system/tph-art-museum.service
```

`config.toml` 首次由 `config.example.toml` 產生：`render.preset`、`render.sequence` 改到 `preset/` 底下，並啟用 `render.layout = "layout.json"`；`layout.json` 由 `layout.example.json` 產生。升級不覆寫這些檔案；新版的範例 layout 若有更新，需手動比對合併。

## 網路

Art-Net 網段是 `2.0.0.0/8`：轉燈 2.0.0.201–224、DMX 轉換器 2.0.0.101–112（見 `config.example.toml`）。Pi 的 Ethernet 要有這個網段的固定 IP（例：2.0.0.10），**不要**設預設閘道：

```bash
nmcli con show                                         # 找出有線連線名稱，例如 "Wired connection 1"
sudo nmcli con mod "Wired connection 1" ipv4.method manual ipv4.addresses 2.0.0.10/8 \
  ipv4.gateway "" ipv4.never-default yes ipv6.method disabled
sudo nmcli con up "Wired connection 1"
```

控制頁面（`http://<Pi>:8765/`）的連法二選一：

- **A. 兩個網路**：Ethernet 接 Art-Net，控制用 Wi-Fi（`wlan0`）或 USB 網卡接館方網路。Pi 有多個介面時，在 `config.toml` 設 `[artnet] source_ip = "2.0.0.10"`，讓 Art-Net 從 Ethernet 送出。
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
