"""main.py 的 train-demand / eval-holdout 入口"""

import sys

from config import DOCS_DIR, MODELS_DIR, PROCESSED_DATA_DIR
from utils.common import setup_logging


def _latest_csv():
    files = sorted(PROCESSED_DATA_DIR.glob("all_courses_*.csv"))
    if not files:
        sys.exit("找不到 processed 資料，請先執行 python main.py process")
    return files[-1]


def train_main():
    setup_logging()
    from ml.demand_model import _pooled, train_and_report

    res = train_and_report(_latest_csv(), MODELS_DIR, DOCS_DIR)
    pooled = _pooled(res["metrics"])
    cols = ["subset", "model", "n", "mae_admit", "spearman", "brier", "pr_auc", "coverage80"]
    print("\n=== walk-forward 開發折（平均） ===")
    print(pooled[cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    sh = _pooled(res["shuffled"])
    print("\n=== 打亂標籤測試（全部） ===")
    print(sh[sh["subset"] == "全部"][cols].to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print(f"\n報告：{DOCS_DIR / 'demand_model_report.md'}")
    print(f"模型：{MODELS_DIR / 'demand_model.joblib'}（held-out {res['meta']['holdout_semester']} 未評估）")


def holdout_main():
    setup_logging()
    from ml.demand_model import HOLDOUT_SEMESTER, eval_holdout

    print(f"⚠️  將在 held-out 學期 {HOLDOUT_SEMESTER} 上計算指標，並寫入 docs/test_set_ledger.md。")
    res = eval_holdout(_latest_csv(), MODELS_DIR, DOCS_DIR)
    cols = ["subset", "model", "n", "base_rate", "mae_admit", "spearman", "brier", "pr_auc", "coverage80"]
    print(res[[c for c in cols if c in res.columns]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))


def predict_main():
    setup_logging()
    from ml.demand_model import predict_missing

    added = predict_missing(_latest_csv(), MODELS_DIR)
    print(f"已補上 {', '.join(added)} 的中籤預測" if added else "所有學期都已有預測，不需補")
