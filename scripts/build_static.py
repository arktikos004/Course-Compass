#!/usr/bin/env python3
"""產生靜態網站要用的資料包（供 Cloudflare Pages 部署）。

原本 FastAPI 每次請求都用 pandas 現算，但這些計算全都是「對一份每日更新的資料集
做純函式運算」——沒有使用者狀態、沒有寫入。既然如此就不需要伺服器：把不隨查詢
條件變動的部分（統計、篩選選項、中籤率）在建置期算完，隨查詢變動的部分
（篩選、評分、排序）改由瀏覽器執行。

輸出結構（預設寫到 web/data/）：

    meta.json              學期清單、已結算學期、歷年中籤率對照表、中籤預測模型的驗證摘要
    courses/115-1.json     單一學期的課程與該學期的篩選選項（找課頁用）；
                           每門課附 admission_pred（中籤預測，來自 data/models/demand_predictions.csv）
    dashboard/115-1.json   預先算好的統計儀表板
    history.json           全部學期的精簡紀錄（歷年課程頁用，延遲載入）

實測大小：單一學期 gzip 後約 0.1 MB，全部學期約 1.1 MB，都在可直接送給瀏覽器的範圍。

用法：
    python scripts/build_static.py            # 輸出到 web/data
    python scripts/build_static.py --out dist # 指定輸出目錄
"""

import argparse
import gzip
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from api import dashboard as dashboard_builder  # noqa: E402
from api import filters as course_filters  # noqa: E402
from api.app import (  # noqa: E402
    calculate_historical_stats, clean_course_data, get_settled_semesters,
)
from api.predictions import PredictionStore  # noqa: E402
from config import MODELS_DIR, PROCESSED_DATA_DIR  # noqa: E402

# 放進 meta.json 的模型說明欄位（系統說明視窗顯示驗證成績用）
PREDICTION_META_KEYS = [
    "model_version", "trained_at", "frozen_train_semesters", "holdout_semester",
    "dev_test_semesters", "dev_summary", "holdout",
]

# 前端實際會用到的欄位。不是整份 CSV 都要送給瀏覽器——教學大綱狀態、上課大樓
# 這類目前沒有介面用到的欄位省下來，單一學期可以再小一截。
COURSE_COLUMNS = [
    "學年度", "學期", "課程代碼", "序號", "課程名稱", "英文課程名稱", "教師姓名",
    "開課班別(代表)", "學院", "科系", "年級", "班級", "學制", "部別", "課程性質",
    "學分", "星期", "起始節次", "結束節次", "上課地點", "上限人數", "登記人數",
    "選上人數", "全英語授課", "可跨班", "教學大綱連結", "教學大綱狀態", "備註",
]

# 歷年課程頁只需要畫人數趨勢與列出開課紀錄，欄位可以再精簡
HISTORY_COLUMNS = [
    "學年度", "學期", "課程代碼", "序號", "課程名稱", "教師姓名",
    "開課班別(代表)", "學分", "星期", "起始節次", "結束節次",
    "上限人數", "登記人數", "選上人數",
]


def latest_processed_file() -> Path:
    files = sorted(Path(PROCESSED_DATA_DIR).glob("all_courses_*.csv"))
    if not files:
        raise SystemExit("找不到處理後的資料檔，請先執行 python main.py process")
    return files[-1]


def write_json(path: Path, payload) -> int:
    """寫出 JSON 並回傳 gzip 後的大小，方便確認有沒有超出合理範圍"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    return len(gzip.compress(text.encode("utf-8"), 6))


def records(df: pd.DataFrame, columns) -> list:
    """轉成 records 並沿用 API 的 NaN 清理，確保與線上版行為一致"""
    available = [c for c in columns if c in df.columns]
    return clean_course_data(df[available].to_dict("records"))


def attach_predictions(courses: list, store: PredictionStore) -> int:
    """把樣本外中籤預測掛到每門課上（admission_pred）；回傳有預測的門數。
    模型版本放在 meta.json 一次就好，逐門只留數字，控制資料包大小。"""
    count = 0
    for course in courses:
        pred = store.get(course)
        if pred:
            course["admission_pred"] = {k: round(pred[k], 3) for k in ("p_full", "est_admit", "admit_lo", "admit_hi")}
            count += 1
    return count


def build(out_dir: Path) -> None:
    source = latest_processed_file()
    print(f"資料來源：{source.name}")

    df = pd.read_csv(source, low_memory=False)
    if {"課程代碼", "序號"}.issubset(df.columns):
        subset = [c for c in ["學年度", "學期", "課程代碼", "序號"] if c in df.columns]
        df = df.drop_duplicates(subset=subset, keep="last")

    settled = get_settled_semesters(df)
    acceptance = calculate_historical_stats(df)
    # 只讀預測檔、不現算：建置環境（CI）不裝 scikit-learn。沒有預測的學期就不附
    predictions = PredictionStore(Path(MODELS_DIR), allow_compute=False)
    prediction_meta = predictions.meta()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # --- meta：學期清單、已結算集合、歷年中籤率 ---
    semesters = []
    for (year, semester), group in df.groupby(["學年度", "學期"]):
        year, semester = int(year), int(semester)
        semesters.append({
            "year": year,
            "semester": semester,
            "has_results": (year, semester) in settled,
            "course_count": int(len(group)),
        })
    semesters.sort(key=lambda s: (s["year"], s["semester"]), reverse=True)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source.name,
        "semesters": semesters,
        "settled": sorted([list(pair) for pair in settled]),
        # 鍵用「課程名稱 <TAB> 教師姓名」：課名與教師名都可能含空白與標點，
        # 用 tab 當分隔符才不會有真實字串意外撞到
        "acceptance": {f"{name}	{teacher}": round(rate, 6)
                       for (name, teacher), rate in acceptance.items()},
        "prediction_model": {k: prediction_meta.get(k) for k in PREDICTION_META_KEYS} if prediction_meta else None,
    }
    size = write_json(out_dir / "meta.json", meta)
    print(f"  meta.json                 gzip {size/1024:>7.1f} KB  "
          f"（{len(semesters)} 個學期、{len(acceptance)} 筆中籤率）")

    # --- 各學期的課程與篩選選項 ---
    total = 0
    for entry in semesters:
        year, semester = entry["year"], entry["semester"]
        scoped = course_filters.filter_by_semester(df, year, semester)
        courses = records(scoped, COURSE_COLUMNS)
        with_pred = attach_predictions(courses, predictions)
        payload = {
            "year": year,
            "semester": semester,
            "has_results": entry["has_results"],
            "filters": course_filters.build_filter_options(scoped),
            "courses": courses,
        }
        size = write_json(out_dir / "courses" / f"{year}-{semester}.json", payload)
        total += size
        print(f"  courses/{year}-{semester}.json         gzip {size/1024:>7.1f} KB  "
              f"（{len(payload['courses'])} 門，{with_pred} 門附中籤預測）")

        dash = dashboard_builder.build_dashboard(df, scoped, settled, year, semester)
        dsize = write_json(out_dir / "dashboard" / f"{year}-{semester}.json", dash)
        total += dsize

    print(f"  dashboard/*.json          gzip {total/1024:>7.1f} KB（含上列課程檔）")

    # --- 歷年課程頁用的精簡全集 ---
    size = write_json(out_dir / "history.json", {"courses": records(df, HISTORY_COLUMNS)})
    print(f"  history.json              gzip {size/1024:>7.1f} KB  （{len(df)} 列）")

    print(f"\n輸出完成：{out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description="產生靜態網站資料包")
    parser.add_argument("--out", default=str(ROOT / "web" / "data"),
                        help="輸出目錄（預設 web/data）")
    args = parser.parse_args()
    build(Path(args.out))


if __name__ == "__main__":
    main()
