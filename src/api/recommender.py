"""推薦排序 —— 把「選不選得上」變成可解釋的分數。

改寫前的 /recommend 只是把過濾結果 head(50)，取到的是 CSV 原始順序，
等於完全沒有推薦邏輯；算好的歷年中籤率也只拿來顯示、沒有參與排序。

這裡用三個學生實際在意的因素評分，並把每項的原始值一起回傳，讓前端能說明
「為什麼推這門」，而不是給一個無從檢驗的黑箱分數。
"""

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from api.filters import normalize_weekday

# 權重：能不能選上是主因，其次是當下還有沒有空額，學分符合度只做微調。
WEIGHT_CHANCE = 60.0
WEIGHT_VACANCY = 25.0
WEIGHT_CREDIT = 15.0

# 沒有任何歷年或本學期資料可推估時的中性值，避免新開課程被壓到最後
UNKNOWN_CHANCE = 0.5


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return default if num != num else num  # NaN


def occupied_slots(df: pd.DataFrame, courses: Optional[Sequence[Dict[str, Any]]]) -> Set[Tuple[int, int]]:
    """由已選課程（只給課程代碼＋序號）回推佔用的 (星期, 節次)。

    前端只送 code/serial，時間要回資料表查，才能在後端就判斷衝堂。
    """
    slots: Set[Tuple[int, int]] = set()
    if df is None or df.empty or not courses:
        return slots
    if not {"課程代碼", "序號", "星期", "起始節次", "結束節次"}.issubset(df.columns):
        return slots

    codes = df["課程代碼"].astype(str).str.strip()
    serials = df["序號"].astype(str).str.strip()

    for item in courses:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        serial = str(item.get("serial", "")).strip()
        if not code:
            continue
        match = df[(codes == code) & (serials == serial)] if serial else df[codes == code]
        for _, row in match.iterrows():
            day = normalize_weekday(row.get("星期"))
            start, end = _to_float(row.get("起始節次")), _to_float(row.get("結束節次"))
            if not day or start <= 0 or end <= 0:
                continue
            for period in range(int(start), int(end) + 1):
                slots.add((day, period))
    return slots


def selected_credits(df: pd.DataFrame, courses: Optional[Sequence[Dict[str, Any]]]) -> float:
    """已選課程的總學分（同樣回資料表查，前端不必送）"""
    if df is None or df.empty or not courses or "學分" not in df.columns:
        return 0.0
    if not {"課程代碼", "序號"}.issubset(df.columns):
        return 0.0

    codes = df["課程代碼"].astype(str).str.strip()
    serials = df["序號"].astype(str).str.strip()

    total = 0.0
    for item in courses:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "")).strip()
        serial = str(item.get("serial", "")).strip()
        if not code:
            continue
        match = df[(codes == code) & (serials == serial)] if serial else df[codes == code]
        if not match.empty:
            total += _to_float(match.iloc[0].get("學分"))
    return total


def _course_slots(course: Dict[str, Any]) -> List[Tuple[int, int]]:
    day = normalize_weekday(course.get("星期"))
    start, end = _to_float(course.get("起始節次")), _to_float(course.get("結束節次"))
    if not day or start <= 0 or end <= 0:
        return []
    return [(day, p) for p in range(int(start), int(end) + 1)]


def estimate_chance(
    course: Dict[str, Any],
    historical_rate: Optional[float],
    semester_settled: bool,
) -> Tuple[float, str]:
    """推估選上機率，並回報這個數字的來源，讓前端能標示可信度。"""
    if historical_rate is not None:
        return max(0.0, min(1.0, float(historical_rate))), "歷年中籤率"

    limit = _to_float(course.get("上限人數"))
    registered = _to_float(course.get("登記人數"))
    # 預選登記中的學期登記數還在累積，拿來推估會過度樂觀，只在已結算學期使用
    if semester_settled and limit > 0 and registered > 0:
        return max(0.0, min(1.0, limit / registered)), "本學期登記數"

    return UNKNOWN_CHANCE, "無足夠資料"


def score_course(
    course: Dict[str, Any],
    *,
    historical_rate: Optional[float],
    semester_settled: bool,
    remaining_credits: Optional[float],
    busy_slots: Set[Tuple[int, int]],
) -> Dict[str, Any]:
    """回傳單一課程的分數與各項細節（分數 0~100）"""
    chance, chance_source = estimate_chance(course, historical_rate, semester_settled)

    limit = _to_float(course.get("上限人數"))
    registered = _to_float(course.get("登記人數"))
    vacancy_ratio = max(0.0, min(1.0, (limit - registered) / limit)) if limit > 0 else 0.0

    credits = _to_float(course.get("學分"))
    if remaining_credits is None:
        # 沒有設定目標學分，這一項就不構成限制
        credit_fit = 1.0
    elif remaining_credits <= 0:
        # 已選學分達到或超過目標，任何課都塞不進剩餘額度。
        # 這裡不能當成「沒有限制」給滿分——那會讓剩餘 -1 反而比剩餘 1 分數更高。
        credit_fit = 0.0
    elif credits <= 0:
        # 零學分課（如實習、講座）不佔額度，給中間值不獎不罰
        credit_fit = 0.5
    else:
        credit_fit = 1.0 if credits <= remaining_credits else 0.0

    slots = _course_slots(course)
    has_conflict = bool(slots) and any(slot in busy_slots for slot in slots)

    score = (
        WEIGHT_CHANCE * chance
        + WEIGHT_VACANCY * vacancy_ratio
        + WEIGHT_CREDIT * credit_fit
    )
    # 衝堂的課不直接剔除（使用者可能想換掉舊課），但一律排到後面
    if has_conflict:
        score *= 0.4

    return {
        "score": round(score, 1),
        "chance": round(chance, 4),
        "chance_source": chance_source,
        "vacancy_ratio": round(vacancy_ratio, 4),
        "vacancy_seats": int(max(0, limit - registered)),
        "credit_fit": credit_fit,
        "has_conflict": has_conflict,
    }


def rank_courses(
    courses: List[Dict[str, Any]],
    *,
    full_df: pd.DataFrame,
    stats_map: Dict[tuple, float],
    semester_settled: bool,
    target_credits: Optional[int],
    current_courses: Optional[Sequence[Dict[str, Any]]],
    exclude_conflicts: bool = False,
) -> List[Dict[str, Any]]:
    """為候選課程評分並排序（高分在前）"""
    busy = occupied_slots(full_df, current_courses)

    remaining: Optional[float] = None
    if target_credits is not None and target_credits > 0:
        remaining = float(target_credits) - selected_credits(full_df, current_courses)

    scored = []
    for course in courses:
        name = str(course.get("課程名稱") or "").strip()
        teacher = str(course.get("教師姓名") or "").strip()
        historical_rate = stats_map.get((name, teacher))

        detail = score_course(
            course,
            historical_rate=historical_rate,
            semester_settled=semester_settled,
            remaining_credits=remaining,
            busy_slots=busy,
        )
        if exclude_conflicts and detail["has_conflict"]:
            continue

        course = dict(course)
        course["historical_acceptance_rate"] = historical_rate
        course["recommend_score"] = detail["score"]
        course["score_detail"] = detail
        course["has_conflict"] = detail["has_conflict"]
        scored.append(course)

    # 分數打平時（例如一堆同樣冷門、必定選得上的課）用登記人數當次要排序：
    # 同樣選得上的前提下，其他同學也想修的課比乏人問津的課值得先看。
    scored.sort(
        key=lambda c: (
            -c["recommend_score"],
            -_to_float(c.get("登記人數")),
            str(c.get("課程代碼") or ""),
            str(c.get("序號") or ""),
        )
    )
    return scored
