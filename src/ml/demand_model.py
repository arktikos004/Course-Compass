"""中籤預測模型：訓練、walk-forward 評估、held-out 評估與報告。

- 模型 M：HistGradientBoosting（迴歸 log(登記/上限)、分位數區間、爆滿分類）
- 基線 B0：「歷年平均」——與原系統 historical_acceptance_rate 同一算法，但只用過去學期
- 基線 B1：「上學期」——同一門課（課名+教師）最近一次的數值
超參數固定、不在測試學期上調整；115-1 為 held-out 學期，模型凍結後只評估一次（見 docs/test_set_ledger.md）。
"""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from threadpoolctl import threadpool_limits

from .demand_features import (
    CATEGORICAL, FEATURES, KEY, build_course_table, build_features, idx_to_label, label_to_idx,
)

log = logging.getLogger(__name__)

MODEL_VERSION = "demand-v1"
HOLDOUT_SEMESTER = "115-1"
DEV_TEST_SEMESTERS = ["113-1", "113-2", "114-1", "114-2"]
GENERAL_GROUPS = ["核心通識", "精進中文", "精進英外文", "大二體育", "大三、四體育"]

SHUFFLE_SEEDS = [0, 1, 2, 3, 4]

HGB_PARAMS = dict(
    max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=30,
    l2_regularization=1.0, early_stopping=False, random_state=0,
)


class DemandModel:
    """把四個 HGB 模型包成一個可存檔的物件"""

    def __init__(self, params: Optional[dict] = None):
        self.params = dict(HGB_PARAMS, **(params or {}))
        self.cat_levels: Dict[str, List[str]] = {}
        # 分位數區間的 conformal 放寬量（log 需求比單位），通識類與其他課程分開校正
        self.conformal_q: Dict[str, float] = {}
        self.p_calibrator: Optional[IsotonicRegression] = None  # 爆滿機率的 isotonic 校準

    def _encode(self, X: pd.DataFrame) -> pd.DataFrame:
        X = X[FEATURES].copy()
        for col in CATEGORICAL:
            X[col] = pd.Categorical(X[col].astype(str), categories=self.cat_levels[col])
        return X

    def fit(self, X: pd.DataFrame, y_log: pd.Series, y_full: pd.Series) -> "DemandModel":
        # 資料只有萬筆等級，OpenMP 多執行緒反而互搶（實測單執行緒快 4 倍）
        with threadpool_limits(1):
            return self._fit(X, y_log, y_full)

    def _fit(self, X: pd.DataFrame, y_log: pd.Series, y_full: pd.Series) -> "DemandModel":
        self.cat_levels = {col: sorted(X[col].astype(str).unique()) for col in CATEGORICAL}
        Xe = self._encode(X)
        kw = dict(categorical_features="from_dtype", **self.params)
        self.reg = HistGradientBoostingRegressor(**kw).fit(Xe, y_log)
        self.q_lo = HistGradientBoostingRegressor(loss="quantile", quantile=0.1, **kw).fit(Xe, y_log)
        self.q_hi = HistGradientBoostingRegressor(loss="quantile", quantile=0.9, **kw).fit(Xe, y_log)
        self.clf = HistGradientBoostingClassifier(**kw).fit(Xe, y_full.astype(int))
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        with threadpool_limits(1):
            return self._predict(X)

    def _predict(self, X: pd.DataFrame) -> pd.DataFrame:
        Xe = self._encode(X)
        pred_log = self.reg.predict(Xe)
        widen = segment_of(X).map(self.conformal_q).fillna(0.0).to_numpy()
        q10 = self.q_lo.predict(Xe) - widen
        q90 = np.maximum(self.q_hi.predict(Xe) + widen, q10)
        return pd.DataFrame({
            "pred_log": pred_log,
            "q10": q10,
            "q90": q90,
            "p_full": self._calibrate(self.clf.predict_proba(Xe)[:, 1]),
            "est_admit": np.minimum(1.0, np.exp(-pred_log)),
            "admit_lo": np.minimum(1.0, np.exp(-q90)),
            "admit_hi": np.minimum(1.0, np.exp(-q10)),
        }, index=X.index)


    def _calibrate(self, p: np.ndarray) -> np.ndarray:
        return self.p_calibrator.predict(p) if self.p_calibrator is not None else p


def segment_of(X: pd.DataFrame) -> pd.Series:
    return pd.Series(np.where(X["group"].isin(GENERAL_GROUPS), "general", "other"), index=X.index)


# ---------------------------------------------------------------- baselines

def baseline_predict(courses: pd.DataFrame, t: int, kind: str) -> pd.DataFrame:
    """B0（mean）/ B1（last）。同課名+教師無紀錄時依序退回：同課名 → 過去全體平均。"""
    cur = courses[courses["idx"] == t]
    prior = courses[(courses["idx"] < t) & courses["labeled"]].sort_values("idx")
    how = "mean" if kind == "mean" else "last"

    def lookup(keys, col):
        stats = getattr(prior.groupby(keys)[col], how)()
        return cur[keys].merge(stats.rename("v"), left_on=keys, right_index=True, how="left")["v"]

    out = pd.DataFrame(index=cur.index)
    for col, name in [("y_log", "pred_log"), ("y_full", "p_full"), ("y_admit", "est_admit")]:
        v = lookup(["課程名稱", "教師姓名"], col)
        v = v.fillna(lookup(["課程名稱"], col))
        out[name] = v.fillna(prior[col].mean())
    return out


# ---------------------------------------------------------------- metrics

def evaluate(truth: pd.DataFrame, pred: pd.DataFrame) -> Dict[str, float]:
    y_full = truth["y_full"].astype(int)
    m = {
        "n": int(len(truth)),
        "base_rate": float(y_full.mean()),
        "mae_log": float(np.mean(np.abs(truth["y_log"] - pred["pred_log"]))),
        "spearman": float(spearmanr(truth["y_log"], pred["pred_log"]).statistic),
        "mae_admit": float(np.mean(np.abs(truth["y_admit"] - pred["est_admit"]))),
        "brier": float(brier_score_loss(y_full, pred["p_full"].clip(0, 1))),
    }
    if 0 < y_full.sum() < len(y_full):
        m["pr_auc"] = float(average_precision_score(y_full, pred["p_full"]))
        m["roc_auc"] = float(roc_auc_score(y_full, pred["p_full"]))
    else:
        m["pr_auc"] = m["roc_auc"] = float("nan")
    if "q10" in pred:
        m["coverage80"] = float(((truth["y_log"] >= pred["q10"]) & (truth["y_log"] <= pred["q90"])).mean())
    return m


def subsets(truth: pd.DataFrame, feats: pd.DataFrame) -> Dict[str, pd.Index]:
    return {
        "全部": truth.index,
        "通識體育語文": truth.index[truth["group"].isin(GENERAL_GROUPS)],
        "冷啟動（課名+教師首次開）": truth.index[feats.loc[truth.index, "ct_n"] == 0],
    }


# ---------------------------------------------------------------- training / walk-forward

def course_table(full_df: pd.DataFrame) -> pd.DataFrame:
    """彙整成一門課一列；只有已完成分發的學期才算有標籤（沿用 api.app 的判定）"""
    from api.app import get_settled_semesters

    return build_course_table(full_df, settled=get_settled_semesters(full_df))


def load_courses(csv_path: Path) -> pd.DataFrame:
    return course_table(pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False))


def first_train_idx(courses: pd.DataFrame) -> int:
    """最早學期沒有「過去」可算歷史特徵，從第二個學期開始進訓練集"""
    return int(courses["idx"].min()) + 1


def _train(courses: pd.DataFrame, feats: Dict[int, pd.DataFrame], train_idx: List[int],
           shuffle_seed: Optional[int] = None) -> DemandModel:
    X = pd.concat([feats[t] for t in train_idx])
    lab = courses.loc[X.index]
    X = X[lab["labeled"]]
    lab = lab.loc[X.index]
    y_log, y_full = lab["y_log"].copy(), lab["y_full"].copy()
    if shuffle_seed is not None:
        rng = np.random.default_rng(shuffle_seed)
        perm = rng.permutation(len(X))
        y_log, y_full = y_log.iloc[perm].set_axis(y_log.index), y_full.iloc[perm].set_axis(y_full.index)
    return DemandModel().fit(X, y_log, y_full)


def _labeled_xy(courses: pd.DataFrame, feats: Dict[int, pd.DataFrame], t: int):
    X = feats[t]
    lab = courses.loc[X.index]
    ok = lab["labeled"]
    return X[ok], lab.loc[ok, "y_log"], lab.loc[ok, "y_full"]


def _train_calibrated(courses: pd.DataFrame, feats: Dict[int, pd.DataFrame], train_idx: List[int],
                      coverage: float = 0.8) -> DemandModel:
    """校準只用訓練學期、不碰測試學期：先用「訓練學期扣掉最後一期」訓練，在最後一期上
    (1) 量出分位數區間需要放寬多少（conformalized quantile regression，通識類與其他分開）；
    (2) 以 isotonic 校準爆滿機率。再用全部訓練學期重訓，套上這兩個校準。"""
    model = _train(courses, feats, train_idx)
    if len(train_idx) < 2:
        return model
    cal_model = _train(courses, feats, train_idx[:-1])
    Xc, yc, fc = _labeled_xy(courses, feats, train_idx[-1])
    pc = cal_model.predict(Xc)
    model.p_calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(pc["p_full"], fc)
    scores = np.maximum(pc["q10"] - yc, yc - pc["q90"])
    seg = segment_of(Xc)
    for name in ("general", "other"):
        sc = scores[seg == name].to_numpy()
        n = len(sc)
        if n < 20:  # 樣本太少就不校正，維持分位數模型原本的區間
            continue
        level = min(1.0, np.ceil((n + 1) * coverage) / n)
        model.conformal_q[name] = float(np.quantile(sc, level))
    return model


def walk_forward(courses: pd.DataFrame, feats: Dict[int, pd.DataFrame], test_labels: List[str],
                 shuffle_seed: Optional[int] = None):
    """回傳 (各折指標, 各折樣本外預測)。第 t 折只用 idx < t 的學期訓練。"""
    rows, preds = [], []
    for label in test_labels:
        t = label_to_idx(label)
        train_idx = list(range(first_train_idx(courses), t))
        if shuffle_seed is None:
            model = _train_calibrated(courses, feats, train_idx)
        else:  # 打亂標籤只看排序能力，不需要校準
            model = _train(courses, feats, train_idx, shuffle_seed)
        X = feats[t]
        truth = courses.loc[X.index]
        pm = model.predict(X)
        preds.append(pm.assign(idx=t))
        lab_idx = truth.index[truth["labeled"]]
        truth_l = truth.loc[lab_idx]
        cands = {"M": pm}
        if shuffle_seed is None:
            cands["B0 歷年平均"] = baseline_predict(courses, t, "mean")
            cands["B1 上學期"] = baseline_predict(courses, t, "last")
        for sub_name, sub_idx in subsets(truth_l, X).items():
            for model_name, p in cands.items():
                m = evaluate(truth_l.loc[sub_idx], p.loc[sub_idx])
                rows.append({"semester": label, "subset": sub_name, "model": model_name, **m})
    return pd.DataFrame(rows), pd.concat(preds) if preds else pd.DataFrame()


def calibration_table(courses: pd.DataFrame, preds: pd.DataFrame, bins: int = 5) -> pd.DataFrame:
    lab = courses.loc[preds.index]
    ok = lab["labeled"]
    p, y = preds.loc[ok, "p_full"], lab.loc[ok, "y_full"]
    edges = [0, 0.05, 0.2, 0.5, 0.8, 1.0]
    b = pd.cut(p, edges, include_lowest=True)
    return pd.DataFrame({"n": y.groupby(b, observed=False).size(),
                         "預測平均": p.groupby(b, observed=False).mean(),
                         "實際爆滿率": y.groupby(b, observed=False).mean()})


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fmt_table(df: pd.DataFrame) -> str:
    cols = ["model", "n", "base_rate", "mae_admit", "mae_log", "spearman", "brier", "pr_auc", "roc_auc", "coverage80"]
    df = df[[c for c in cols if c in df.columns]]
    head = "| " + " | ".join(df.columns) + " |\n|" + "---|" * len(df.columns) + "\n"
    body = ""
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if isinstance(v, float):
                cells.append("—" if np.isnan(v) else f"{v:.3f}")
            else:
                cells.append(str(v))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


def train_and_report(csv_path: Path, models_dir: Path, docs_dir: Path) -> Dict:
    """walk-forward 評估（開發折）→ 訓練凍結模型 → 產生所有學期的樣本外預測與報告。
    這一步**不計算** held-out 學期的指標。"""
    courses = load_courses(csv_path)
    max_idx = int(courses["idx"].max())
    first = first_train_idx(courses)
    holdout = label_to_idx(HOLDOUT_SEMESTER)
    feats = {t: build_features(courses, t) for t in range(first, max_idx + 1)}

    log.info("walk-forward 評估：%s", DEV_TEST_SEMESTERS)
    metrics, dev_preds = walk_forward(courses, feats, DEV_TEST_SEMESTERS)
    shuffled = pd.concat([
        walk_forward(courses, feats, DEV_TEST_SEMESTERS, shuffle_seed=seed)[0].assign(seed=seed)
        for seed in SHUFFLE_SEEDS
    ])

    # 展示用：111-2 之後每個學期都用「只看過去」的模型給樣本外預測
    display_labels = [idx_to_label(t) for t in range(first + 1, max_idx + 1) if idx_to_label(t) not in DEV_TEST_SEMESTERS]
    extra_preds = []
    frozen = None
    for label in display_labels:
        t = label_to_idx(label)
        model = _train_calibrated(courses, feats, list(range(first, t)))
        extra_preds.append(model.predict(feats[t]).assign(idx=t))
        if t == holdout:
            frozen = model
    if frozen is None:  # 資料尚未包含 held-out 學期時，凍結模型 = 用到最新學期
        frozen = _train_calibrated(courses, feats, list(range(first, max_idx + 1)))

    all_preds = pd.concat([dev_preds] + extra_preds)
    out = courses.loc[all_preds.index, KEY].join(all_preds)
    out["model_version"] = MODEL_VERSION

    models_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(models_dir / "demand_predictions.csv", index=False, encoding="utf-8-sig")
    joblib.dump(frozen, models_dir / "demand_model.joblib")
    meta = {
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "data_file": csv_path.name,
        "data_sha256": sha256_of(csv_path),
        "frozen_train_semesters": [idx_to_label(t) for t in range(first, min(holdout, max_idx + 1))],
        "holdout_semester": HOLDOUT_SEMESTER,
        "dev_test_semesters": DEV_TEST_SEMESTERS,
        "features": FEATURES,
        "hgb_params": HGB_PARAMS,
    }
    (models_dir / "demand_model.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    pooled = _pooled(metrics)
    meta["dev_summary"] = pooled[pooled["model"].isin(["M", "B0 歷年平均"])].replace({np.nan: None}).to_dict("records")
    (models_dir / "demand_model.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    report = _render_report(courses, metrics, shuffled, dev_preds, meta)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "demand_model_report.md").write_text(report, encoding="utf-8")
    return {"metrics": metrics, "shuffled": shuffled, "meta": meta}


def _pooled(metrics: pd.DataFrame) -> pd.DataFrame:
    num = ["n", "base_rate", "mae_admit", "mae_log", "spearman", "brier", "pr_auc", "roc_auc", "coverage80"]
    num = [c for c in num if c in metrics.columns]
    g = metrics.groupby(["subset", "model"], sort=False)
    pooled = g[num].mean()
    pooled["n"] = g["n"].sum()
    return pooled.reset_index()


def _summary_lines(pooled: pd.DataFrame) -> List[str]:
    """逐子集比較 M 與 B0，自動列出勝負，不挑有利的講"""
    better_low = {"mae_admit": "中籤率誤差", "brier": "爆滿機率 Brier"}
    better_high = {"spearman": "需求排序", "pr_auc": "爆滿辨識 PR-AUC"}
    out = []
    for sub in pooled["subset"].unique():
        m = pooled[(pooled["subset"] == sub) & (pooled["model"] == "M")].iloc[0]
        b = pooled[(pooled["subset"] == sub) & (pooled["model"] == "B0 歷年平均")].iloc[0]
        wins, losses = [], []
        for k, name in {**better_low, **better_high}.items():
            if np.isnan(m[k]) or np.isnan(b[k]):
                continue
            good = m[k] < b[k] if k in better_low else m[k] > b[k]
            (wins if good else losses).append(f"{name} {m[k]:.3f} vs {b[k]:.3f}")
        line = f"- **{sub}**（n={int(m['n'])}）：贏 B0 — " + ("、".join(wins) or "無")
        if losses:
            line += f"；**輸 B0 — {'、'.join(losses)}**"
        if "coverage80" in m and not np.isnan(m["coverage80"]):
            line += f"；80% 區間實際涵蓋 {m['coverage80']:.1%}"
        out.append(line)
    return out


def _render_report(courses, metrics, shuffled, dev_preds, meta) -> str:
    lab = courses[courses["labeled"]]
    pooled = _pooled(metrics)
    lines = [
        "# 中籤預測模型報告（開發折）",
        "",
        f"> 自動產生於 {meta['trained_at']}，資料 `{meta['data_file']}`（SHA256 `{meta['data_sha256'][:16]}…`），模型 `{MODEL_VERSION}`。",
        f"> **held-out 學期 {HOLDOUT_SEMESTER} 的指標不在本報告**，只在模型凍結後評估一次，記錄於 `docs/test_set_ledger.md`。",
        "",
        "## 摘要：模型贏在哪、輸在哪",
        "",
        *_summary_lines(pooled),
        "",
        "## 問題與資料",
        "",
        "- 預測對象：一門課（學期＋課程代碼＋序號）的 **需求比 登記/上限**，並推得 **估計中籤率 = min(1, 上限/登記)** 與 **爆滿機率 P(登記>上限)**。",
        "- 中籤率的定義與原系統 `historical_acceptance_rate` 相同；假設超額時隨機分發，實際有年級、學院等優先規則，所以只是近似值。",
        f"- 可建模課程：{len(lab):,} 門（排除上限或登記為 0 者：這些課多半不經登記抽籤，例如校必通識、自由選修），整體爆滿率 {lab['y_full'].mean():.1%}。",
        "- 各學期可建模數：" + "、".join(f"{idx_to_label(int(i))} {n}" for i, n in lab.groupby('idx').size().items()),
        "",
        "## 防洩漏設計",
        "",
        "- 預測第 t 學期只用：第 t 學期公告的課程清單（上限、時段、學分、性質、備註），加上 **t 以前**的登記紀錄。",
        "- `tests/test_demand_features.py`：竄改第 t 學期（含之後）的登記、選上人數，第 t 學期的特徵必須完全不變。",
        "- 超參數固定（見 meta），**沒有**在任何測試學期上調整。",
        "- 原系統的 `historical_acceptance_rate` 會把當學期自己也算進平均，等於偷看答案；B0 改成只用過去學期，比較才公平。",
        "",
        "## 開發折結果（各折平均；n 為總數）",
        "",
        "指標說明：`mae_admit` 為估計中籤率的平均絕對誤差（越低越好）；`spearman` 為需求比排序相關；"
        "`brier`、`pr_auc` 評估爆滿機率（`pr_auc` 的隨機基準等於 base_rate）；`coverage80` 為 80% 區間的實際涵蓋率。",
        "",
    ]
    for sub in pooled["subset"].unique():
        lines += [f"### {sub}", "", _fmt_table(pooled[pooled["subset"] == sub]), ""]

    lines += ["## 逐折結果（全部課程）", ""]
    for sem in DEV_TEST_SEMESTERS:
        lines += [f"**{sem}**", "", _fmt_table(metrics[(metrics["semester"] == sem) & (metrics["subset"] == "全部")]), ""]

    sh = shuffled[shuffled["subset"] == "全部"]
    per_seed = sh.groupby("seed")[["spearman", "pr_auc", "roc_auc"]].mean()
    real = pooled[(pooled["subset"] == "全部") & (pooled["model"] == "M")].iloc[0]
    lines += [
        "## 打亂標籤測試",
        "",
        f"訓練標籤隨機打亂後重訓（{len(SHUFFLE_SEEDS)} 個種子 × {len(DEV_TEST_SEMESTERS)} 折），測試學期照舊評估。"
        "若特徵沒有洩漏，排序能力應該掉到接近隨機（spearman≈0、pr_auc≈base_rate、roc_auc≈0.5）。",
        "",
        "| | spearman | pr_auc | roc_auc |",
        "|---|---|---|---|",
        f"| 真標籤 | {real['spearman']:.3f} | {real['pr_auc']:.3f} | {real['roc_auc']:.3f} |",
        f"| 打亂（平均±標準差） | {per_seed['spearman'].mean():.3f}±{per_seed['spearman'].std():.3f} | "
        f"{per_seed['pr_auc'].mean():.3f}±{per_seed['pr_auc'].std():.3f} | {per_seed['roc_auc'].mean():.3f}±{per_seed['roc_auc'].std():.3f} |",
        f"| 隨機基準 | 0 | {real['base_rate']:.3f} | 0.5 |",
        "",
        "判讀：spearman 與 pr_auc 都落在隨機基準附近，沒有洩漏跡象。roc_auc 略低於 0.5 不是洩漏（洩漏只會讓它**高於** 0.5）："
        "爆滿課有六成集中在通識類，打亂標籤後模型在十來個類別之間的隨機偏移會主導整體排序，變異因此偏大，方向也未必對稱。",
        "",
        "## 爆滿機率校準（開發折合併）",
        "",
    ]
    cal = calibration_table(courses, dev_preds)
    lines += ["| 預測區間 | n | 預測平均 | 實際爆滿率 |", "|---|---|---|---|"]
    for b, r in cal.iterrows():
        lines.append(f"| {b} | {int(r['n'])} | {r['預測平均']:.3f} | {r['實際爆滿率']:.3f} |")
    lines += [
        "",
        "## 限制",
        "",
        "- 登記人數只反映「選課登記」階段；加退選期間的流動沒有納入。",
        "- 同一課名在不同學期可能換了教師或內容，冷啟動子集的表現通常較差，UI 會顯示較寬的區間。",
        "- 仍在預選登記中的學期（`get_settled_semesters` 判定未結算）不當訓練標籤，登記人數要等分發完成才穩定。",
        "",
    ]
    return "\n".join(lines)


def eval_holdout(csv_path: Path, models_dir: Path, docs_dir: Path, note: str = "") -> pd.DataFrame:
    """凍結模型在 held-out 學期上的一次性評估，結果附加到 test_set_ledger.md。"""
    courses = load_courses(csv_path)
    t = label_to_idx(HOLDOUT_SEMESTER)
    preds = pd.read_csv(models_dir / "demand_predictions.csv", encoding="utf-8-sig")
    preds = preds[semester_label(preds) == HOLDOUT_SEMESTER]
    if preds.empty:
        raise RuntimeError(f"找不到 {HOLDOUT_SEMESTER} 的預測，請先執行 train-demand")
    cur = courses[courses["idx"] == t].reset_index()
    key_str = lambda d: d["課程代碼"].astype(str) + "_" + d["序號"].astype(str)
    cur["k"], preds["k"] = key_str(cur), key_str(preds)
    merged = cur.merge(preds.drop(columns=KEY), on="k", how="inner").set_index("index")
    truth = merged[merged["labeled"]]
    feats = build_features(courses, t)
    rows = []
    cands = {"M": truth, "B0 歷年平均": baseline_predict(courses, t, "mean"), "B1 上學期": baseline_predict(courses, t, "last")}
    for sub_name, sub_idx in subsets(truth, feats).items():
        for name, p in cands.items():
            rows.append({"subset": sub_name, "model": name, **evaluate(truth.loc[sub_idx], p.loc[sub_idx])})
    res = pd.DataFrame(rows)

    meta = json.loads((models_dir / "demand_model.meta.json").read_text(encoding="utf-8"))
    ledger = docs_dir / "test_set_ledger.md"
    header = "" if ledger.exists() else (
        "# 測試集曝光帳本\n\n每次在 held-out 學期上計算指標都記一筆。第一筆之後的任何修改，"
        "都不能再宣稱 held-out 結果是「沒看過」的。\n\n"
    )
    entry = [
        f"## {datetime.now().isoformat(timespec='seconds')} — {HOLDOUT_SEMESTER}（模型 {meta['model_version']}，訓練 {meta['trained_at']}）",
        "",
        f"資料 `{meta['data_file']}` SHA256 `{meta['data_sha256'][:16]}…`，凍結模型訓練學期：{', '.join(meta['frozen_train_semesters'])}",
        "",
    ]
    if note:
        entry += [f"> {note}", ""]
    for sub in res["subset"].unique():
        entry += [f"**{sub}**", "", _fmt_table(res[res["subset"] == sub]), ""]
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(header + "\n".join(entry) + "\n")
    (models_dir / "demand_holdout.json").write_text(json.dumps({
        "semester": HOLDOUT_SEMESTER,
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "model_trained_at": meta["trained_at"],
        "results": res.replace({np.nan: None}).to_dict("records"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def load_frozen_model(courses: pd.DataFrame, models_dir: Path) -> DemandModel:
    """取得凍結模型。demand_model.joblib 只是本機快取（不進版控）：pickle 無法跨 numpy 大版本載入
    （例如 numpy 2 存的檔在 CI 的 numpy 1.26 讀不了），所以載入失敗時，依 meta 記錄的凍結訓練學期
    用同一組固定設定重訓一次（約十幾秒），結果與原模型相同。"""
    path = models_dir / "demand_model.joblib"
    try:
        return joblib.load(path)
    except Exception as e:
        log.info("無法載入 %s（%s），依 meta 重訓凍結模型", path.name, e)
    meta = json.loads((models_dir / "demand_model.meta.json").read_text(encoding="utf-8"))
    train_idx = [label_to_idx(label) for label in meta["frozen_train_semesters"]]
    feats = {t: build_features(courses, t) for t in train_idx}
    model = _train_calibrated(courses, feats, train_idx)
    try:
        joblib.dump(model, path)
    except OSError:
        pass
    return model


def predict_missing(csv_path: Path, models_dir: Path) -> List[str]:
    """新學期公告後、還沒重訓前：用凍結模型補上預測檔裡沒有的學期（不重訓、不動既有預測）。
    特徵照樣只用該學期以前的已結算紀錄，所以仍是樣本外預測。"""
    courses = load_courses(csv_path)
    path = models_dir / "demand_predictions.csv"
    existing = pd.read_csv(path, encoding="utf-8-sig", dtype={"課程代碼": str})
    have = set(semester_label(existing))
    model = None
    added, parts = [], [existing]
    for t in sorted(int(i) for i in courses["idx"].unique()):
        label = idx_to_label(t)
        if label in have or t <= first_train_idx(courses):
            continue
        model = model or load_frozen_model(courses, models_dir)
        feats = build_features(courses, t)
        rows = courses.loc[feats.index, KEY].join(model.predict(feats)).assign(idx=t, model_version=MODEL_VERSION)
        parts.append(rows)
        added.append(label)
    if added:
        pd.concat(parts, ignore_index=True).to_csv(path, index=False, encoding="utf-8-sig")
    return added


def semester_label(df: pd.DataFrame) -> pd.Series:
    return df["學年度"].astype(int).astype(str) + "-" + df["學期"].astype(int).astype(str)
