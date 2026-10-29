# 在 Windows 主機上開放選課助理

正式網站 `course.sekinv.com` 是 Cloudflare Pages 上的靜態網站，沒辦法執行語言模型。
選課助理與大綱問答改由一台 24 小時開機的 Windows 主機提供：

```
使用者 → course.sekinv.com/api/ai/*（Pages Function：functions/api/ai/[[path]].js）
       → Cloudflare Tunnel（例如 ai-api.sekinv.com）
       → Windows 主機 127.0.0.1:8010（python main.py api）
       → 雲端語言模型 API（Gemini 等，金鑰在主機的 .env）
```

- 主機**不用開任何 port、不用固定 IP**：Tunnel 是主機主動連出去的。
- 前端沒有改網址，還是呼叫同網域的 `/api/ai/*`，Pages Function 在中間轉發。
- 主機只接受帶共用密鑰的請求，密鑰只有 Pages Function 知道；同時依使用者 IP 限流，避免 API 額度被刷光（規則見 `src/api/ai_gate.py`）。
- 主機沒開、或還沒設定好時，網站照常運作，選課助理分頁會顯示「暫時無法使用」。

以下步驟做一次就好。第 1～5 步在主機上做，第 6～7 步在 Cloudflare 網頁上做。

---

## 1. 安裝工具

- **Git for Windows**：https://git-scm.com/download/win
- **Python 3.11**：https://www.python.org/downloads/ ，安裝時勾選「Add python.exe to PATH」。
  用 3.11 是因為 CI 也是 3.11，`requirements.txt` 鎖定的 pandas、lxml 版本在 3.11 都有現成的 Windows 安裝檔；太新的 Python 可能要從原始碼編譯而失敗。
- **電源設定**：「設定 → 系統 → 電源」把睡眠改成「永不」，否則主機睡著時助理就斷線。

## 2. 下載專案（放在 OneDrive 以外的地方）

用「命令提示字元」執行：

```bat
git clone https://github.com/arktikos004/Course-Compass.git C:\srv\Course-Compass
cd /d C:\srv\Course-Compass
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ai.txt
```

不要放在 OneDrive 資料夾：`.venv`、下載的大綱 PDF 和 `.env`（含金鑰）都不該同步到雲端，同步中的檔案也可能被鎖住。
repo 是公開的，之後每天自動 `git pull` 不需要登入 GitHub。

## 3. 設定 `.env`

```bat
copy .env.example .env
notepad .env
```

要填的有：

| 設定 | 值 |
|---|---|
| `LLM_API_KEY` | Gemini 金鑰（https://aistudio.google.com 申請） |
| `AI_SHARED_SECRET` | 共用密鑰，用下面的指令產生一組，第 7 步還會用到 |
| `API_PORT` | `8010`（避開常被其他服務占用的 8000；第 6 步的 Tunnel 也要指到這個 port） |

產生密鑰：

```bat
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

`AI_SHARED_SECRET`、`API_PORT` 在範本裡是註解，要把行首的 `#` 拿掉。**註解要寫在獨立一行**，不要寫在值的後面（`update.bat` 會直接讀 `API_PORT=` 後面整段文字）。

## 4. 建立大綱索引

```bat
.venv\Scripts\python.exe main.py fetch-syllabi
.venv\Scripts\python.exe main.py build-index
```

- `fetch-syllabi` 以每秒最多 1 個請求下載約 1,700 份大綱 PDF，約 1～2 小時。
- `build-index` 會用 `.env` 裡的 `LLM_API_EMBED_MODEL` 把約 1.7 萬段文字做 embedding。碰到免費額度的速率上限時會自動重試，可能會跑比較久。
- 沒有索引時選課助理仍可使用，只是不會搜尋大綱內容，也不會出現「問這門課的大綱」。

## 5. 註冊開機自動執行

用「以系統管理員身分執行」打開 PowerShell：

```powershell
cd C:\srv\Course-Compass
powershell -ExecutionPolicy Bypass -File deploy\windows\install_tasks.ps1
```

它會詢問 Windows 登入密碼（Microsoft 帳號就是該帳號的密碼，不是 PIN），讓排程在沒有人登入時也能執行。註冊兩個排程：

- **CourseCompass Backend**：開機就啟動 `deploy\windows\run_backend.bat`；後端當掉會在 10 秒內自動重開。
- **CourseCompass Update**：每天 04:30 執行 `deploy\windows\update.bat`（GitHub 在 04:00 更新課程資料）。
  - 只有資料變動：不用重啟，後端會自動讀新資料。
  - 程式、套件或中籤預測檔變動：自動重啟後端。

確認後端有起來（把 `你的密鑰` 換成 `.env` 裡的值）：

```bat
curl -H "X-Backend-Secret: 你的密鑰" http://localhost:8010/api/ai/status
```

看到 `"available":true` 和 `"provider":"api · gemini-…"` 就對了。如果 provider 是 `mock（雲端 API 無法使用…）`，代表金鑰或模型名稱有問題，錯誤原因寫在 `logs\course_master.log`。

## 6. 建立 Cloudflare Tunnel

在 Cloudflare 後台：

1. **Networking → Tunnels → Create a tunnel**，名稱例如 `course-compass-ai`。
2. 作業系統選 **Windows**，複製它給的安裝指令，在主機上用**系統管理員**命令提示字元執行。這會把 `cloudflared` 裝成 Windows 服務，開機自動連線。回到網頁等它顯示連線成功，按 **Continue**。
3. 進入這個 tunnel 的 **Routes → Add route → Published application**：
   - Subdomain：`ai-api`，Domain：`sekinv.com`
   - Service：`http://localhost:8010`

之後直接打開 `https://ai-api.sekinv.com/api/ai/status` 應該會看到 `forbidden`：代表 Tunnel 通了，而且沒有密鑰的人進不來。

## 7. 設定 Pages 並重新部署

Cloudflare 後台 **Workers & Pages → course-compass → Settings → Variables and Secrets → Add**，新增兩筆，**兩筆都按 Encrypt**：

| 名稱 | 值 |
|---|---|
| `AI_BACKEND_URL` | `https://ai-api.sekinv.com` |
| `AI_BACKEND_SECRET` | 和主機 `.env` 的 `AI_SHARED_SECRET` 一模一樣 |

都要設成加密的 Secret：專案有 `wrangler.toml`，一般變數在後台只能看、不能改，但 Secret 可以。

Secret 要在部署前設好才會生效，所以設完後重新部署一次：到 GitHub 的 **Actions → Build and Deploy to Cloudflare Pages → Run workflow**。

完成後打開 https://course.sekinv.com 的選課助理分頁，右上角會顯示「模型：api · gemini-…」。

---

## 日常維護

| 情況 | 要做的事 |
|---|---|
| 平常 | 不用管。資料每天自動更新，程式有更新時也會自動重啟。 |
| 重啟後端 | 執行 `deploy\windows\restart_backend.bat`：停掉後端，10 秒內自動重新啟動。 |
| 新學期開始 | 重跑第 4 步（`fetch-syllabi`、`build-index`），再重啟後端。 |
| 換金鑰、改限流 | 改 `.env` 後重啟後端。 |
| 換共用密鑰 | 主機 `.env` 和 Pages 的 `AI_BACKEND_SECRET` 都要改，然後重啟後端並重新部署。 |

## 疑難排解

| 症狀 | 檢查 |
|---|---|
| 網站顯示「選課助理暫時無法使用」 | 主機有沒有開機；工作排程器裡 CourseCompass Backend 是否在執行；Cloudflare 的 tunnel 是否 Healthy；兩邊密鑰是否相同。 |
| 網站仍顯示「本機版功能」的安裝說明 | Pages 的兩個 Secret 沒設好，或設完之後還沒重新部署。 |
| 回覆「問得太快了」「每人每天最多…」 | 限流生效了，數值可在 `.env` 調整（`AI_RATE_PER_MINUTE`、`AI_RATE_PER_DAY`、`AI_GLOBAL_PER_DAY`）。 |
| 模型顯示為 mock | 雲端 API 連不上：檢查 `LLM_API_KEY`、模型名稱，或免費額度是否用完。 |
| 紀錄檔 | `logs\course_master.log`（後端，含 API 連線錯誤）、`logs\update.log`（每日更新）、`logs\backend_restarts.log`（後端重啟）。 |
