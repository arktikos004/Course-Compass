"""中籤預測的讀取層：靜態資料包（scripts/build_static.py）與本機 API 共用。

- 已有的學期：直接讀 train-demand 產出的 demand_predictions.csv（每一筆都是「只用過去學期」訓練的樣本外預測）
- 沒有預測的新學期（例如選課前剛公告的 115-2）：allow_compute=True 時用凍結模型現算
  （需要 requirements-ai.txt 的 scikit-learn）；建置靜態網站時關掉，只讀 CSV
"""

import json
import logging
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger(__name__)

# 這兩類課幾乎都不經登記抽籤（歷年登記人數為 0 的比例 97%、100%），預測沒有意義
NOT_APPLICABLE_TYPES = {"自由選修", "校必(通識)"}

Key = Tuple[str, str, str, str]


def _key(year, semester, code, serial) -> Key:
    def norm(v):
        try:
            f = float(v)
            return str(int(f)) if f.is_integer() else str(v)
        except (TypeError, ValueError):
            return str(v).strip()
    return norm(year), norm(semester), str(code).strip(), norm(serial)


def applicable(course: Dict[str, Any]) -> bool:
    try:
        cap = float(course.get("上限人數") or 0)
    except (TypeError, ValueError):
        cap = 0
    return cap > 0 and str(course.get("課程性質") or "") not in NOT_APPLICABLE_TYPES


class PredictionStore:
    def __init__(self, models_dir: Path, allow_compute: bool = True):
        self.models_dir = models_dir
        self.allow_compute = allow_compute
        self._lock = threading.Lock()
        self._preds: Optional[Dict[Key, Dict[str, float]]] = None
        self._computed_semesters: set = set()
        self._model = None
        self._meta: Optional[dict] = None

    # ------------------------------------------------------------ loading
    def _load(self):
        if self._preds is not None:
            return
        self._preds = {}
        path = self.models_dir / "demand_predictions.csv"
        if not path.exists():
            log.warning("找不到 %s，中籤預測停用（請執行 python main.py train-demand）", path)
            return
        df = pd.read_csv(path, encoding="utf-8-sig", dtype={"課程代碼": str})
        for r in df.itertuples(index=False):
            self._preds[_key(r.學年度, r.學期, r.課程代碼, r.序號)] = self._pack(r._asdict())

    @staticmethod
    def _pack(r: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "est_admit": round(float(r["est_admit"]), 4),
            "admit_lo": round(float(r["admit_lo"]), 4),
            "admit_hi": round(float(r["admit_hi"]), 4),
            "p_full": round(float(r["p_full"]), 4),
            "model": r.get("model_version", "demand-v1"),
        }

    def meta(self) -> Dict[str, Any]:
        if self._meta is None:
            meta_path = self.models_dir / "demand_model.meta.json"
            self._meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            holdout_path = self.models_dir / "demand_holdout.json"
            if holdout_path.exists():
                self._meta["holdout"] = json.loads(holdout_path.read_text(encoding="utf-8"))
        return self._meta

    def _ensure_semester(self, year: str, semester: str, full_df: Optional[pd.DataFrame]) -> None:
        """資料裡有這個學期、但預測檔沒有時，用凍結模型現算一次"""
        sem = (year, semester)
        if not self.allow_compute or sem in self._computed_semesters or full_df is None:
            return
        self._computed_semesters.add(sem)
        if any(k[:2] == sem for k in self._preds):
            return
        if not (self.models_dir / "demand_model.meta.json").exists():
            return
        try:
            from ml.demand_features import KEY, build_features, label_to_idx
            from ml.demand_model import course_table, load_frozen_model

            courses = course_table(full_df)
            if self._model is None:
                self._model = load_frozen_model(courses, self.models_dir)
            t = label_to_idx(f"{year}-{semester}")
            feats = build_features(courses, t)
            if feats.empty:
                return
            pred = self._model.predict(feats)
            rows = courses.loc[feats.index, KEY].join(pred)
            version = self.meta().get("model_version", "demand-v1")
            for r in rows.to_dict("records"):
                r["model_version"] = version
                self._preds[_key(r["學年度"], r["學期"], r["課程代碼"], r["序號"])] = self._pack(r)
            log.info("已為 %s-%s 現算 %d 筆中籤預測", year, semester, len(rows))
        except Exception as e:  # 預測失敗不影響主要功能
            log.error("現算中籤預測失敗：%s", e)

    # ------------------------------------------------------------ public
    def get(self, course: Dict[str, Any], full_df: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
        if not applicable(course):
            return None
        with self._lock:
            self._load()
            k = _key(course.get("學年度"), course.get("學期"), course.get("課程代碼"), course.get("序號"))
            if k not in self._preds:
                self._ensure_semester(k[0], k[1], full_df)
            return self._preds.get(k)

    def attach(self, courses: List[Dict[str, Any]], full_df: Optional[pd.DataFrame] = None) -> None:
        for c in courses:
            c["admission_pred"] = self.get(c, full_df)

    def reset(self) -> None:
        with self._lock:
            self._preds, self._model, self._meta = None, None, None
            self._computed_semesters = set()


def format_pct(v: Optional[float]) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.0f}%"
