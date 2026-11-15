"""中籤預測的讀取層：靜態資料包（scripts/build_static.py）與本機 API 共用。

- 已有的學期：直接讀 train-demand 產出的 demand_predictions.csv（每一筆都是「只用過去學期」訓練的樣本外預測）
- 沒有預測的新學期（例如選課前剛公告的 115-2）：allow_compute=True 時用凍結模型現算
  （需要 requirements-ai.txt 的 scikit-learn）；建置靜態網站時關掉，只讀 CSV
- 序號是學校清單裡的流水號，中途增開或停開別的課，後面的序號會整批位移。
  有傳入目前的課程表時，同一課程代碼底下依序號的順序對回預測檔的班次（align_serials）；預測檔本身不重算
"""

import json
import logging
import math
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Hashable, List, Optional, Set, Tuple

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


def _serial_order(serial: str):
    return (0, int(serial), "") if serial.isdigit() else (1, 0, serial)


def align_serials(
    current: Dict[Hashable, Set[str]], predicted: Dict[Hashable, Set[str]],
) -> Dict[Tuple[Hashable, str], Optional[str]]:
    """目前課程表的序號 → 預測檔裡同一班次的序號。

    current、predicted：{課程: 該課底下的序號集合}。回傳 {(課程, 目前序號): 預測檔序號}，只列出序號集合不同的課：
    - 班次數相同：依序號由小到大一一對應（整批位移不會改變同一門課各班的先後）
    - 班次數不同：分不出誰對誰，對應到 None（不顯示預測，也不採用剛好同號的那一筆）
    預測檔沒有的課不在回傳內。
    """
    alias: Dict[Tuple[Hashable, str], Optional[str]] = {}
    for course, now in current.items():
        before = predicted.get(course)
        if not before or now == before:
            continue
        if len(now) != len(before):
            alias.update({(course, serial): None for serial in now})
            continue
        for serial, target in zip(sorted(now, key=_serial_order), sorted(before, key=_serial_order)):
            alias[(course, serial)] = target
    return alias


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
        self._alias: Dict[Key, Optional[Key]] = {}        # 目前課程表的鍵 → 預測檔的鍵（None＝不顯示）
        self._aligned_df: Optional[pd.DataFrame] = None   # 上次對齊用的課程表；資料更新後會是另一個物件

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

    def _align(self, full_df: Optional[pd.DataFrame]) -> None:
        """課程表的序號和預測檔對不上時（重新爬取後位移），建立「目前的鍵 → 預測檔的鍵」對照。"""
        if full_df is None or full_df is self._aligned_df:
            return
        self._aligned_df = full_df
        self._alias = {}
        if not self._preds or not {"學年度", "學期", "課程代碼", "序號"}.issubset(full_df.columns):
            return
        predicted: Dict[Tuple[str, str, str], Set[str]] = defaultdict(set)
        for year, semester, code, serial in self._preds:
            predicted[(year, semester, code)].add(serial)
        current: Dict[Tuple[str, str, str], Set[str]] = defaultdict(set)
        for row in zip(full_df["學年度"], full_df["學期"], full_df["課程代碼"], full_df["序號"]):
            year, semester, code, serial = _key(*row)
            current[(year, semester, code)].add(serial)
        for (course, serial), target in align_serials(current, predicted).items():
            self._alias[(*course, serial)] = None if target is None else (*course, target)

    # ------------------------------------------------------------ public
    def get(self, course: Dict[str, Any], full_df: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
        """full_df：目前的完整課程表。有給才會處理序號位移，並在允許時為沒有預測的學期現算。"""
        if not applicable(course):
            return None
        with self._lock:
            self._load()
            self._align(full_df)
            k = _key(course.get("學年度"), course.get("學期"), course.get("課程代碼"), course.get("序號"))
            if k in self._alias:
                target = self._alias[k]
                return self._preds.get(target) if target else None
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
            self._alias, self._aligned_df = {}, None


def format_pct(v: Optional[float]) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.0f}%"
