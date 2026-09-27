# Course Master — 智慧選課輔助系統

智慧選課輔助系統，提供課程資料爬取、處理、查詢與推薦功能。

## 專案結構

```
Course-Compass/
├── main.py                 # 主入口點
├── requirements.txt        # 依賴套件
├── .github/workflows/      # 自動化排程
│   ├── update-data.yml    # 每日自動爬取並更新課程資料
│   └── monitor.yml        # 每 10 分鐘檢查目標課程缺額並 LINE 通知
├── ncue-course-monitor/    # 缺額監控（共用 src/crawler/ncue_client.py）
│   ├── main.py
│   └── monitor_config.py  # 監控目標與 LINE 設定（可用環境變數覆寫）
├── config/                 # 配置目錄
│   ├── __init__.py        # 配置匯出
│   ├── paths.py           # 路徑配置
│   ├── crawler.py         # 爬蟲配置
│   ├── api.py             # API 配置
│   └── logging_config.py  # 日誌配置
├── data/                   # 資料目錄
│   ├── raw/               # 原始爬取資料
│   ├── processed/         # 處理後的資料
│   └── dict/              # 字典檔案（教師、科系映射）
├── src/                    # 原始碼
│   ├── api/               # API 模組
│   │   ├── app.py         # FastAPI 應用與路由
│   │   ├── filters.py     # 共用篩選層與分類法（學制、通識類別）
│   │   ├── recommender.py # 推薦評分與排序
│   │   ├── search.py      # 搜尋相關度排序與分頁
│   │   ├── dashboard.py   # 統計儀表板資料組裝
│   │   └── vacancy.py     # 即時缺額查詢（唯一會對外請求的模組）
│   ├── crawler/           # 爬蟲模組
│   │   ├── crawler.py     # 課程爬蟲
│   │   └── ncue_client.py # OB010 站台存取層（純 requests+bs4，與監控共用）
│   ├── processor/         # 資料處理模組
│   │   ├── data_processor.py      # 資料處理器
│   │   ├── teacher_dict_builder.py # 教師字典構建器
│   │   └── department_mapper.py   # 科系映射器
│   ├── utils/             # 工具模組
│   │   ├── common.py      # 共用工具
│   │   └── io.py          # I/O 工具
│   └── config.py          # Config shim
├── tests/                 # 自動測試（標準庫 unittest，不需額外依賴）
│   └── test_api.py        # 篩選、評分、排序、統計與 /query 端點
├── scripts/               # 維護腳本
│   ├── print_config.py           # 列印配置
│   └── check_processed_fields.py # 檢查處理後欄位
├── web/                   # 前端檔案
│   ├── index.html         # 分頁：課表 / 找課 / 缺額監控 / 歷年 / 儀表板 / 分群分析
│   └── assets/
│       ├── css/
│       └── js/
└── logs/                  # 日誌目錄
```

## 快速開始

### 1. 安裝依賴

```bash
pip install -r requirements.txt
```

### 2. 執行完整流程

```bash
# 執行完整流程（爬取 → 構建字典 → 處理 → 啟動 API）
python main.py all
```

### 3. 分步執行

```bash
# 爬取課程資料（預設爬完整區間 config/crawler.py 的 START ~ END）
python main.py crawl

# 只重爬最後 N 個學期（過去學期資料已凍結，日常更新用這個即可）
python main.py crawl --latest 2

# 構建教師字典（產生候選清單，需人工併入 data/dict/teacher.csv）
python main.py build-dict

# 處理資料
python main.py process

# 啟動 API 服務
python main.py api
```

> 新學期上線時，記得把 `config/crawler.py` 的 `END_YEAR` / `END_SEMESTER` 往後調。
> API 會依資料檔的 mtime 自動重讀，處理完不需要重啟服務。

## 主要功能

### 資料爬取
- 自動爬取多學期課程資料，支援只重爬最新學期
- 處理 ASP.NET MVC 表單（防偽 token、AJAX 標頭、honeypot 欄位）
- 解析課程表格、中英文課名、教學大綱連結與教師個人頁
- GitHub Actions 每日自動更新（`.github/workflows/update-data.yml`）

### 缺額監控
- 針對指定課程代碼查詢名額，出現缺額時以 LINE 推播通知
- 以 `state.json` 記錄狀態，只在「額滿 → 有缺額」時通知一次，不重複轟炸
- 需在 repo secrets 設定 `LINE_CHANNEL_ACCESS_TOKEN` 與 `LINE_USER_ID`
- 監控目標可用環境變數 `TARGET_YEAR` / `TARGET_SEMESTER` / `TARGET_COURSES` 覆寫

### 資料處理
- 課程名稱分割（中英文）
- 教師姓名智能解析
- 上課時間與地點解析
- 科系映射（學院、科系、年級、班級）

### 推薦排序

推薦結果會依「選不選得上」評分後排序（0~100），不再是原始資料順序：

| 權重 | 項目 | 說明 |
| ---: | --- | --- |
| 60 | 選上機率 | 優先用歷年中籤率；沒有歷年資料才退而用本學期登記數推估（且僅限已結算學期） |
| 25 | 目前空額比例 | `(上限 - 登記) / 上限` |
| 15 | 學分符合度 | 該課學分是否還塞得進「目標學分 − 已選學分」的剩餘額度 |

衝堂的課不會被剔除（使用者可能想換掉舊課），但分數乘以 0.4 排到後面。
每門課都會回傳 `score_detail`，前端據此說明推薦理由，不給無從檢驗的黑箱分數。

> **篩選一律在後端完成後才分頁。** 早期版本是後端先 `head(50)`、前端再補做分類／星期／
> 空堂過濾，符合條件的課會在截斷時就被丟掉，常常出現「只找到 3 門」但實際有數十門的情況。

> **搜尋與推薦已合併。** 兩者本來是各自的端點與分頁，但送的是同一組條件（連 `category`
> 都是同一個欄位），後端也走同一個 `apply_filters`。合併後只剩 `_query_courses()` 一個實作，
> 差別只在 `sort` 選什麼、以及有沒有帶課表感知參數（`empty_slots` / `current_courses` /
> `target_credits` / `exclude_conflicts`）。

### 篩選維度

- **學制分流**：大學部／碩士班／博士班，以及「研究所」（碩＋博）的方便集合，依 `部別` 欄位
- **通識分類**：依 `課程性質` 推導出跨學院通識（文/理/工/管/教/社體/科技）、
  素養通識（文化美學與文明／生活藝能及應用）、校訂必修通識
- 學院、科系、年級、日夜間部、星期、節次、學分區間、全英語授課、是否仍有名額

選項全部由 `/api/filters` 依資料現況推導，新學期多出新類別時前端不必改。

> **跨學院通識「不能選本院」的判定**：不能比對 `學院` 欄位 —— 所有核心通識課的學院
> 都是「通識教育中心」，拿學生的學院去比永遠不會相等，這條規則等於沒生效。
> 真正帶領域資訊的是課程性質後綴，`跨學院通識(文)` 即文學院開設，
> 對應表見 `src/api/filters.py` 的 `COLLEGE_TO_GENERAL_DOMAIN`。

### API 服務

| 端點 | 說明 |
| --- | --- |
| `POST /api/courses/query` | **課程查詢（搜尋與推薦合一）**：關鍵字、篩選、課表感知、排序、分頁 |
| `POST /api/courses/recommend` | `/query` 的別名，保留給既有呼叫端 |
| `GET /api/courses/search` | GET 版查詢，可直接貼網址分享；不接課表感知參數 |
| `GET /api/filters` | 篩選選單的可選值（學制、學院、通識分類…），由資料推導 |
| `GET /api/dashboard` | 統計儀表板：總覽、學期趨勢、時段熱區、搶手／好選課排行 |
| `GET /api/vacancy` | 即時缺額查詢，直接向學校站台請求 |
| `GET /api/semesters` | 可用學期清單，含是否已完成分發 |
| `GET /api/courses/history` | 歷年開課紀錄 |
| `GET /api/courses/all`、`/api/departments`、`/api/courses/stats` | 既有端點 |

### Web 介面

- **我的課表**：完整課表系統（12 節次）、一鍵導入班級課程
- **找課**：搜尋與推薦合併的單一查詢頁 —— 關鍵字、分類、多條件篩選、六種排序、
  卡片／列表兩種呈現、分頁載入；勾選「只看我的空堂」「排除衝堂」即可納入課表判斷
- **缺額監控**：即時查詢名額，可從課表一鍵加入監控清單，支援停留本頁時自動更新
- **統計儀表板**：學期趨勢、學院／學制／學分分佈、上課時段熱區、搶手與好選課排行
- **歷年課程**：登記／選上／上限圖表
- **中籤預測**（AI）：查詢結果與課程詳情顯示爆滿機率、預估中籤率與 80% 區間；
  預測在建置期寫進靜態資料包，正式網站就看得到
- **選課助理**、**問大綱**（AI，本機版）：用一句話描述需求、針對課程大綱提問；
  需要本機的 Ollama，正式網站會顯示本機版說明
- 學年度與學期選單由 `/api/semesters` 動態產生，新學期上線不必改前端
- 仍在預選登記中的學期會標示「預選登記中」，並自動排除於中籤率／飽和度統計外

> **為何要排除預選中的學期**：預選登記進行中時「登記人數」仍在累積、「選上人數」尚未公布，
> 是不完整的快照。若納入計算，中籤率（上限/登記）會偏樂觀、飽和度（登記/上限）會偏低。
> 判定方式見 `src/api/app.py` 的 `get_settled_semesters()`。

## AI 功能

詳見 [docs/AI_FEATURES.md](docs/AI_FEATURES.md)（方法、驗證數字與限制）。

```bash
pip install -r requirements.txt -r requirements-ai.txt   # AI 套件與核心分開，CI 不需要
python main.py train-demand      # 重訓中籤預測 → data/models/、docs/demand_model_report.md
python main.py predict-demand    # 新學期公告後，用凍結模型補上預測（不重訓）
python main.py fetch-syllabi     # 下載當學期教學大綱 PDF（data/syllabus/，不進版控）
python main.py build-index       # 建立大綱索引（需 ollama pull bge-m3；沒有 Ollama 時用離線 mock）
python scripts/build_static.py && python main.py api   # 本機完整版：http://localhost:8000
```

## 資料流程

```
原始網站 → 爬蟲 → 原始 CSV → 資料處理 → 清理資料 → API → Web 介面
```

## 技術棧

- **後端**：Python 3.8+
- **Web 框架**：FastAPI
- **資料處理**：pandas
- **爬蟲**：requests + BeautifulSoup4
- **前端**：HTML5 + Bootstrap 5 + JavaScript

## 配置說明

主要配置位於 `config/` 目錄：

- `paths.py`：檔案路徑配置
- `crawler.py`：爬蟲參數（學期範圍、URL 等）
- `api.py`：API 伺服器配置
- `logging_config.py`：日誌配置

## 維護腳本

- `scripts/print_config.py`：檢查載入的配置
- `scripts/check_processed_fields.py`：驗證處理後資料的欄位

## 測試

```bash
python -m unittest discover -s tests -v
```

70 項測試，涵蓋篩選層、推薦評分、搜尋排序、儀表板統計與 `/api/courses/query` 的整合行為，
以及 AI 功能（`tests/test_ai.py`：中籤預測防洩漏、選課助理防幻覺、大綱切塊）。
其中 7 項需要 scikit-learn，沒裝 `requirements-ai.txt` 時自動略過（CI 即是如此）。
多數用手工造的小資料集，不需要先跑爬蟲；少數 smoke 測試會在
`data/processed/` 有實際資料時才執行。

每個開發過程中實際踩到的 bug 都留了對應的迴歸測試，例如：

- 分頁前必須先套完所有條件（舊版先 `head(50)` 再讓前端過濾，結果數會少報）
- 剩餘學分為負時 `credit_fit` 不可比剩餘為正時更高
- 跨學院通識的「本院」要看課程性質後綴，不是 `學院` 欄位
- 學院選單必須排除通識教育中心等非學院單位
- 教師排行要濾掉原始資料把「論文」填進教師欄的情況
- `rank_results` 不可覆寫已算好的中籤率，否則兩種熱門排序會失效

## 注意事項

- 爬蟲會發出網路請求，執行前請確認網路可用且符合目標網站使用規範
- 建議先使用 `scripts/check_processed_fields.py` 檢查處理後資料再啟動 API
- 教師字典需要人工審核高風險項目

## 授權

本專案採用 MIT 授權。
