# Course Compass AI 功能說明

三項 AI 功能都附有與基線的比較、可以重跑的評估，並寫明限制。

| 功能 | 正式網站（靜態） | 本機版（`python main.py api`） | 技術 | 報告 |
|---|---|---|---|---|
| 中籤預測 | ✅ 建置期寫進資料包 | ✅ | HistGradientBoosting＋conformal 區間＋isotonic 校準 | `docs/demand_model_report.md`、`docs/test_set_ledger.md` |
| 選課助理 | 顯示本機版說明 | ✅ | 本機 Ollama（qwen2.5:7b）tool calling；沒有 Ollama 時改用離線規則模式 | `docs/agent_eval.md` |
| 教學大綱搜尋與問答 | 不顯示 | ✅ | PDF 文字層 → 段落切塊 → bge-m3 向量與字元 TF-IDF 混合搜尋 | `docs/rag_eval.md` |

**為什麼分成兩種部署**：網站以 Cloudflare Pages 全靜態方式部署（見 `wrangler.toml`），沒有伺服器可以跑語言模型。
- **中籤預測**：預測可以事先算好，由 `scripts/build_static.py` 讀取 `data/models/demand_predictions.csv`，寫進每門課的 `admission_pred`。CI 不需要安裝 scikit-learn。
- **選課助理與大綱問答**：需要即時推論，只在本機版提供。
- **前端判斷方式**：前端先呼叫 `/api/ai/status`，確認拿到 JSON 且 `available=true` 才啟用。Cloudflare 找不到路徑時會回傳首頁（200），所以不能只看狀態碼。

---

## 1. 中籤預測

**問題**：選課時最常見的困擾是「這門課會不會爆、我選不選得上」。現有的「歷年平均中籤率」是把該課所有已結算學期平均起來；對於正在選的這學期，它並不是預測。

**做法**：
- **預測目標**：一門課的需求比 log(登記/上限)，並推得兩個數字：
  - **估計中籤率** = min(1, 上限/登記)，附 80% 區間
  - **爆滿機率** P(登記 > 上限)
- **特徵**（預測第 t 學期時只用）：
  - 第 t 學期公告的課程清單：上限、時段、學分、性質、備註、同時段競爭班數
  - **t 以前、而且已結算的學期**的登記紀錄。「已結算」沿用 `get_settled_semesters()` 的判定，仍在預選中的學期不拿來當標籤。
- **防洩漏**：
  - 竄改測試：改動第 t 學期的登記人數，特徵必須不變（`tests/test_ai.py`）
  - 打亂標籤測試：5 個種子
  - 超參數固定、不在測試學期上調
- **區間與校準**：
  - 分位數迴歸加 conformal 校正，通識類和其他課分開校正
  - 爆滿機率用 isotonic 校準
  - 兩種校準都只用訓練學期的最後一期來做

**結果**（M = 模型、B0 = 歷年平均〔只用過去學期〕、B1 = 上學期）：

| | 開發折（113-1～114-2，逐學期只用過去資料） | 保留學期 115-1 |
|---|---|---|
| 全部課程 爆滿辨識 PR-AUC | M 0.653／B0 0.514／B1 0.384 | M **0.542**／B0 0.438／B1 0.347（base rate 0.083） |
| 全部課程 中籤率誤差 MAE | M 0.017／B0 0.022 | M **0.021**／B0 0.026 |
| 通識體育語文 PR-AUC | M 0.903／B0 0.887 | M **0.720**／B0 0.662 |
| 通識體育語文 中籤率誤差 | M 0.124／**B0 0.117（B0 較好）** | M **0.157**／B0 0.184 |
| 冷啟動（首次開的課）PR-AUC | M 0.364／B0 0.196 | M **0.530**／B0 0.324 |
| 80% 區間實際涵蓋率 | 全部 82.0%、通識 75.7% | 全部 **70.1%**、通識 **63.1%**（偏窄） |

**誠實邊界**：
- 115-1 各項都贏 B0，但**數字全面低於開發折**，新學期有分布漂移。
- **80% 區間在 115-1 只涵蓋 70%**，介面上有註明區間偏窄。
- 通識的中籤率誤差在開發折是**輸給**歷年平均的，兩者都寫進報告。
- **115-1 已經曝光過**。模型最早在 2026-09-26 以另一份爬取結果評估過一次；移植到 main 後，用 main 的資料以同一凍結設定重算，數字完全相同。兩次都記在 `docs/test_set_ledger.md`。之後若再改模型，下一個乾淨的測試學期是 115-2。
- 打亂標籤測試的 ROC-AUC 是 0.416±0.028，原因分析寫在報告裡：六成爆滿課集中在通識類，打亂後類別層級的偏移造成高變異。這不是洩漏，洩漏只會讓它高於 0.5。
- 爆滿機率經過 isotonic 校準，所以數值是分段的，很多課會顯示相同的機率（例如 41%）。
- 中籤率假設超額時隨機分發。實際有年級、學院等優先規則，所以只是近似值。
- **沒有動到推薦分數**。找課頁的「推薦分數」仍然用歷年平均中籤率，所以 JS 和 Python 的 parity 不受影響。要改用模型預測，需要同時修改 `recommender.py` 和 `query.js`，並重新產生 parity 樣本，這部分留待之後討論。

**新學期的流程**：
1. 課程清單公告後，每日排程會自動爬到新學期的資料。
2. 執行 `python main.py predict-demand`，用凍結模型補上新學期的預測。這一步不重訓，特徵仍然只用過去的已結算紀錄。
3. 把 `data/models/demand_predictions.csv` commit 上去，下一次部署就會出現在網站上。
4. 學期結算後，可以用 `python main.py train-demand` 重訓。

`data/models/demand_model.joblib` 是本機快取，不進版控。pickle 檔不能跨 numpy 大版本載入：本機 numpy 2 存的檔，在 CI 的 numpy 1.26 讀不了。所以需要模型卻讀不到時，會依 `demand_model.meta.json` 記錄的凍結訓練學期，用同一組設定重訓，大約十幾秒。實測在 Python 3.13／numpy 2 和 Python 3.11／numpy 1.26 兩種環境下，重訓出的預測和版控裡的預測檔逐筆相同，差異在 1e-15 以內。

## 2. 選課助理（本機版）

**流程**：
1. 使用者輸入一句話，例如「週五不要有課、2 學分通識、跟資料分析有關、不要太難搶」。
2. LLM 自行決定呼叫哪些工具，最多 5 輪。可用的工具有：
   - `find_courses`：底層是查詢頁同一套 `api.filters.apply_filters`，外層再加上避開星期、避開早八、爆滿機率上限、排除衝堂
   - `semantic_search_syllabus`：大綱語意搜尋
   - `get_course_detail`、`get_course_history`
   - `check_schedule_conflict`：檢查衝堂
3. 最後以 JSON schema 強制輸出 `{reply, suggestions}`。

**設計重點**：
- **防幻覺**：LLM 建議的每一門課都用「課程代碼＋序號」回頭比對目標學期的資料，不存在就剔除，並在介面上標示。
- **透明**：介面可以展開看到助理實際呼叫了哪些工具、帶了什麼參數。
- **會參考「我的課表」**：自動排除已選的課和會衝堂的課。

**評估**：30 則查詢，每則的條件由人工寫定，不是由解析器產生，見 `data/eval/agent_queries.json`。

| provider | 條件滿足率 | 全部符合的查詢比例 | 幻覺率 | 有解卻空答 |
|---|---|---|---|---|
| 離線規則模式 | 86.2% | 80.8% | 0% | 0% |
| qwen2.5:7b（Ollama） | **尚未評估**：開發環境沒有安裝 Ollama | | | |

規則模式的失誤集中在它不認得的說法，例如「只有週三下午有空」「星期二」「不會爆滿」。裝好 Ollama 後執行 `python main.py eval-agent`，就會兩種一起比較。

## 3. 教學大綱搜尋與問答（本機版）

- **資料**：115-1 共 1,895 門課，其中 1,732 門有大綱 PDF。
  - 以每秒最多 1 個請求的速度下載，並快取在 `data/syllabus/`（不進版控）。
  - 直接讀 PDF 文字層，1,732 份全部可讀，共切成 16,816 塊。
- **處理方式**：
  - 清掉每份都一樣的樣板文字，並接回被拆成一字一行的直書表格。
  - 依「教學目標／教學大綱／評量方式／教材／教學進度…」切段落，每塊約 500 字、重疊 80 字。
- **搜尋**：向量相似度與字元 bigram TF-IDF 各占一半，權重固定為 0.5，沒有針對評估題目調整。
- **問答**：只取該課程最相關的 3 段給 LLM，要求標註引用編號，段落裡沒有的資訊就回答「大綱沒有寫」。介面會附上引用的原文。
- **評估**（`docs/rag_eval.md`）：15 則換句話說的查詢，「相關」的定義是大綱含有事先寫定的相關詞，屬於代理標註。
  - **開發環境沒有 Ollama，索引用的是離線 mock embedding（字元雜湊），沒有語意能力**。在這個條件下的結果：

    | 方法 | P@5 | P@10 | Hit@5 | MRR |
    |---|---|---|---|---|
    | 混合（系統採用） | 0.720 | 0.593 | **1.000** | 0.836 |
    | 純向量（mock） | 0.467 | 0.427 | 0.867 | 0.648 |
    | 基線：整篇字面 TF-IDF | **0.773** | **0.707** | 0.933 | **0.842** |

  - **離線模式下，混合搜尋並沒有勝過字面基線**：只有 Hit@5 較好，P@5、P@10 基線較好。要驗證語意能力，需要安裝 Ollama、`ollama pull bge-m3`、重建索引，再跑 `python main.py eval-rag`。

---

## 如何執行

```bash
pip install -r requirements.txt -r requirements-ai.txt
python main.py train-demand          # walk-forward 評估＋訓練凍結模型＋報告
python main.py predict-demand        # 新學期：用凍結模型補上預測（不重訓）
# python main.py eval-holdout        # 在保留學期上計算指標，每次都會記入 test_set_ledger.md

# 選課助理與大綱搜尋需要本機 Ollama（https://ollama.com/download）
ollama pull qwen2.5:7b && ollama pull bge-m3
python main.py fetch-syllabi         # 下載當學期大綱（約 1,700 份，需時 1～2 小時）
python main.py build-index           # 建立大綱向量索引
python main.py eval-agent && python main.py eval-rag

python scripts/build_static.py && python main.py api    # http://localhost:8000
AI_PROVIDER=mock python main.py api                       # 沒有 Ollama 時用離線規則模式
python -m unittest discover -s tests                      # 70 項測試（7 項需要 scikit-learn）
```

## 生成式 AI 使用說明（競賽申報用）

- **系統內**：選課助理與大綱問答使用本機開源模型 qwen2.5:7b（Ollama），向量搜尋使用 bge-m3。課程資料不會送到雲端。
- **開發過程**：程式撰寫與文件整理有使用 AI 程式助理（Claude Code）協助。模型設計、驗證結果與限制，都以本文件所列、可重跑的腳本與測試為準。
