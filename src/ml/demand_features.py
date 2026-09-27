"""中籤預測的資料整理與特徵工程。

核心紀律：預測第 t 學期時，**只能使用選課開始前就知道的資訊**：
- 第 t 學期本身：課程清單（上限、時段、學分、課程性質、備註…），這些在選課前就公告
- 第 t 學期以前（idx < t）：歷年登記人數、上限、是否爆滿
第 t 學期的「登記人數／選上人數」是結果，絕不進特徵。tests/test_demand_features.py 以竄改測試把關。
"""

from typing import List

import numpy as np
import pandas as pd

KEY = ["學年度", "學期", "課程代碼", "序號"]

# 開課班別(代表) 基數很高（資工二、美一…），只保留固定的全校性類別，其他歸為系所／碩博課程
GROUP_NAMES = [
    "核心通識", "教育學程", "大二體育", "大三、四體育", "精進英外文", "精進中文",
    "全民國防教育課程", "語文課", "服務學習",
]

CATEGORICAL = ["group", "課程性質", "課程性質2", "學院", "部別", "學制", "可跨班", "day"]

NUMERIC = [
    "學期", "log_cap", "學分", "n_slots", "total_periods", "start", "is_evening", "全英語授課",
    "note_cannot", "note_campus", "note_remote", "note_limit",
    # 同學期競爭（課程清單公告即可得）
    "grp_n_sections", "grp_log_cap", "slot_competitors", "same_name_sections",
    # 歷史（僅 idx < t）
    "ct_last", "ct_mean", "ct_n", "ct_full_rate", "ct_gap",
    "c_last", "c_mean", "c_n", "c_full_rate",
    "t_mean", "t_n",
    "grp_prev_mean", "grp_prev_full_rate", "grp_pressure",
]

FEATURES = NUMERIC + CATEGORICAL


def semester_index(year, semester) -> pd.Series:
    """111-1 → 0、111-2 → 1、112-1 → 2 …，不同學年度可直接比先後"""
    return (pd.to_numeric(year) - 100) * 2 + (pd.to_numeric(semester) - 1)


def idx_to_label(idx: int) -> str:
    return f"{idx // 2 + 100}-{idx % 2 + 1}"


def label_to_idx(label: str) -> int:
    y, s = label.split("-")
    return (int(y) - 100) * 2 + (int(s) - 1)


def _group_of(cls_name: str) -> str:
    s = str(cls_name)
    for g in GROUP_NAMES:
        if g in s:
            return g
    if "博" in s or "碩" in s:
        return "碩博班"
    return "系所課程"


def build_course_table(full_df: pd.DataFrame, settled=None) -> pd.DataFrame:
    """processed 資料是一個上課時段一列；彙整成一門課（學期+代碼+序號）一列，並附上目標值。

    settled：已完成分發的學期集合（api.app.get_settled_semesters）。仍在預選登記中的學期
    登記人數還在累積，不能當標籤；None 表示全部視為已結算（測試用的小資料）。"""
    df = full_df.copy()
    for col in ["上限人數", "登記人數", "選上人數", "起始節次", "結束節次", "學分"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["day_num"] = df["星期"].map({"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7})
    df["periods"] = (df["結束節次"] - df["起始節次"] + 1).clip(lower=0)

    first_cols = [
        "課程名稱", "教師姓名", "開課班別(代表)", "課程性質", "課程性質2", "學院", "部別", "學制",
        "可跨班", "全英語授課", "學分", "上限人數", "登記人數", "選上人數", "備註",
    ]
    first_cols = [c for c in first_cols if c in df.columns]
    agg = {c: "first" for c in first_cols}
    agg.update({"day_num": "first", "起始節次": "first", "periods": "sum", "星期": "count"})
    c = df.groupby(KEY, as_index=False, sort=False).agg(agg)
    c = c.rename(columns={"day_num": "first_day", "起始節次": "start", "periods": "total_periods", "星期": "n_slots"})

    c["idx"] = semester_index(c["學年度"], c["學期"])
    c["課程名稱"] = c["課程名稱"].fillna("").astype(str).str.strip()
    c["教師姓名"] = c["教師姓名"].fillna("").astype(str).str.strip()
    c["group"] = c["開課班別(代表)"].map(_group_of)

    cap, reg = c["上限人數"], c["登記人數"]
    c["labeled"] = (cap > 0) & (reg > 0)
    if settled is not None:
        pairs = list(zip(pd.to_numeric(c["學年度"]).astype(int), pd.to_numeric(c["學期"]).astype(int)))
        c["labeled"] &= pd.Series([p in settled for p in pairs], index=c.index)
    c["y_log"] = np.where(c["labeled"], np.log(reg.clip(lower=1) / cap.clip(lower=1)), np.nan)
    c["y_full"] = np.where(c["labeled"], (reg > cap).astype(float), np.nan)
    c["y_admit"] = np.where(c["labeled"], np.minimum(1.0, cap / reg.clip(lower=1)), np.nan)
    return c


def _static_features(cur: pd.DataFrame) -> pd.DataFrame:
    """第 t 學期課程清單本身就有的資訊"""
    f = pd.DataFrame(index=cur.index)
    f["學期"] = pd.to_numeric(cur["學期"])
    f["log_cap"] = np.log1p(cur["上限人數"].fillna(0))
    f["學分"] = cur["學分"]
    f["n_slots"] = cur["n_slots"]
    f["total_periods"] = cur["total_periods"]
    f["start"] = cur["start"]
    f["is_evening"] = (cur["start"] >= 10).astype(float)
    f["全英語授課"] = cur["全英語授課"].astype(str).str.lower().isin(["true", "1", "是"]).astype(float)
    note = cur["備註"].fillna("").astype(str)
    f["note_cannot"] = note.str.contains("不能修|不得修|不可修").astype(float)
    f["note_campus"] = note.str.contains("寶山").astype(float)
    f["note_remote"] = note.str.contains("遠距|線上").astype(float)
    f["note_limit"] = note.str.contains("限").astype(float)
    for col in ["group", "課程性質", "課程性質2", "學院", "部別", "學制", "可跨班"]:
        f[col] = cur[col].fillna("").astype(str)
    f["day"] = cur["first_day"].fillna(0).astype(int).astype(str)

    # 同學期競爭：同類別的班數與總容量、同時段同類別的班數、同名課開幾班
    grp = cur.groupby("group")
    f["grp_n_sections"] = grp["上限人數"].transform("size")
    f["grp_log_cap"] = np.log1p(grp["上限人數"].transform("sum"))
    slot_key = cur["group"] + "|" + cur["first_day"].astype(str) + "|" + cur["start"].astype(str)
    f["slot_competitors"] = slot_key.map(slot_key.value_counts()) - 1
    f["same_name_sections"] = cur["課程名稱"].map(cur["課程名稱"].value_counts())
    return f


def _history_features(cur: pd.DataFrame, prior: pd.DataFrame, t: int) -> pd.DataFrame:
    """只用 idx < t 的已標註課程"""
    f = pd.DataFrame(index=cur.index)
    prior = prior.sort_values("idx")

    def agg_by(keys: List[str], prefix: str, with_last=True, with_full=True, with_gap=False):
        g = prior.groupby(keys)
        stats = pd.DataFrame({f"{prefix}_mean": g["y_log"].mean(), f"{prefix}_n": g["y_log"].size()})
        if with_last:
            stats[f"{prefix}_last"] = g["y_log"].last()
        if with_full:
            stats[f"{prefix}_full_rate"] = g["y_full"].mean()
        if with_gap:
            stats[f"{prefix}_gap"] = t - g["idx"].max()
        merged = cur[keys].merge(stats, left_on=keys, right_index=True, how="left")
        return merged.drop(columns=keys)

    f = f.join(agg_by(["課程名稱", "教師姓名"], "ct", with_gap=True))
    f = f.join(agg_by(["課程名稱"], "c"))
    f = f.join(agg_by(["教師姓名"], "t", with_last=False, with_full=False))
    for col in ["ct_n", "c_n", "t_n"]:
        f[col] = f[col].fillna(0)

    # 類別層級：最近一個有資料的學期，該類別的平均需求比、爆滿率、以及「上期總登記 ÷ 本期總容量」
    if prior.empty:
        f["grp_prev_mean"] = np.nan
        f["grp_prev_full_rate"] = np.nan
        f["grp_pressure"] = np.nan
        return f
    last_idx = prior.groupby("group")["idx"].max()
    last_rows = prior[prior["idx"] == prior["group"].map(last_idx)]
    g = last_rows.groupby("group")
    prev = pd.DataFrame({
        "grp_prev_mean": g["y_log"].mean(),
        "grp_prev_full_rate": g["y_full"].mean(),
        "grp_prev_reg": g["登記人數"].sum(),
    })
    cur_cap = cur.groupby("group")["上限人數"].sum()
    f["grp_prev_mean"] = cur["group"].map(prev["grp_prev_mean"])
    f["grp_prev_full_rate"] = cur["group"].map(prev["grp_prev_full_rate"])
    f["grp_pressure"] = np.log((cur["group"].map(prev["grp_prev_reg"]) + 1) / (cur["group"].map(cur_cap) + 1))
    return f


def build_features(courses: pd.DataFrame, t: int) -> pd.DataFrame:
    """回傳第 t 學期所有課程的特徵（index 對齊 courses 中 idx == t 的列）"""
    cur = courses[courses["idx"] == t]
    prior = courses[(courses["idx"] < t) & courses["labeled"]]
    feats = _static_features(cur).join(_history_features(cur, prior, t))
    return feats[FEATURES]


def build_feature_frame(courses: pd.DataFrame, semesters: List[int]) -> pd.DataFrame:
    parts = [build_features(courses, t) for t in semesters]
    return pd.concat(parts) if parts else pd.DataFrame(columns=FEATURES)
