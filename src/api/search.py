"""全站課程搜尋的相關度排序與分頁。

原本 /api/courses/search 只做 substring 比對後 head(limit)，命中順序等於 CSV 順序：
搜「英文」時，課名就叫「英文」的課可能排在「大學英文閱讀」後面。這裡給一個
可預期的相關度：完全相符 > 開頭相符 > 包含，且課名優先於教師、教師優先於英文課名。
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# 欄位權重：命中課名最相關，其次教師，再來課程代碼與英文課名
FIELD_WEIGHTS = (
    ("課程名稱", 100),
    ("教師姓名", 60),
    ("課程代碼", 55),
    ("英文課程名稱", 40),
    ("開課班別(代表)", 20),
)

# 命中位置加成：完全相符 > 從頭開始 > 出現在中間
EXACT_BONUS = 2.0
PREFIX_BONUS = 1.5
CONTAINS_BONUS = 1.0


def relevance(course: Dict[str, Any], query: str) -> float:
    """單一課程對查詢字串的相關度，取各欄位命中的最高分再加總小額欄位分。"""
    q = query.strip().lower()
    if not q:
        return 0.0

    total = 0.0
    for field, weight in FIELD_WEIGHTS:
        value = str(course.get(field) or "").strip().lower()
        if not value or q not in value:
            continue
        if value == q:
            factor = EXACT_BONUS
        elif value.startswith(q):
            factor = PREFIX_BONUS
        else:
            factor = CONTAINS_BONUS
        total += weight * factor

    # 同分時讓有開課時間、有名額的課稍微優先，避免時間未定的課佔滿前排
    if course.get("星期"):
        total += 1.0
    return total


def paginate(items: List[Any], limit: int, offset: int) -> Tuple[List[Any], Dict[str, Any]]:
    """回傳 (該頁資料, 分頁資訊)"""
    total = len(items)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    page = items[offset:offset + limit]
    return page, {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
    }


def rank_results(
    courses: List[Dict[str, Any]],
    query: Optional[str],
    sort: str = "relevance",
    stats_map: Optional[Dict[tuple, float]] = None,
) -> List[Dict[str, Any]]:
    """依指定方式排序搜尋結果，並附上歷年中籤率。"""
    stats_map = stats_map or {}

    for course in courses:
        # 已經算過就不要再覆寫：/query 會先讓 rank_courses 評分（那裡已填好中籤率），
        # 再視排序需求交給這裡。無條件覆寫會在 stats_map 查不到時把既有值抹成 None，
        # 導致「最搶手」「最好選上」兩種排序都退化成無資料的順序。
        if "historical_acceptance_rate" not in course:
            name = str(course.get("課程名稱") or "").strip()
            teacher = str(course.get("教師姓名") or "").strip()
            course["historical_acceptance_rate"] = stats_map.get((name, teacher))
        if query:
            course["relevance"] = round(relevance(course, query), 2)

    def credits_of(course: Dict[str, Any]) -> float:
        try:
            value = float(course.get("學分") or 0)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if value != value else value

    def vacancy_of(course: Dict[str, Any]) -> float:
        try:
            limit = float(course.get("上限人數") or 0)
            registered = float(course.get("登記人數") or 0)
        except (TypeError, ValueError):
            return 0.0
        return limit - registered

    def registered_of(course: Dict[str, Any]) -> float:
        try:
            value = float(course.get("登記人數") or 0)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if value != value else value

    if sort == "credits":
        courses.sort(key=lambda c: (-credits_of(c), str(c.get("課程名稱") or "")))
    elif sort == "vacancy":
        courses.sort(key=lambda c: (-vacancy_of(c), str(c.get("課程名稱") or "")))
    elif sort == "acceptance":
        # 中籤率高 → 低（最好選上的排前面）；沒有歷年資料的排最後
        courses.sort(key=lambda c: (
            -(c.get("historical_acceptance_rate") if c.get("historical_acceptance_rate") is not None else -1),
            str(c.get("課程名稱") or ""),
        ))
    elif sort == "competitive":
        # 中籤率低 → 高（最搶手的排前面）。
        # 沒有歷年資料的課不能排在最前面——那會讓「最熱門」變成「沒資料的課」，
        # 因此無資料一律視為 2（大於任何實際比率）沉到最後。
        courses.sort(key=lambda c: (
            c.get("historical_acceptance_rate") if c.get("historical_acceptance_rate") is not None else 2,
            str(c.get("課程名稱") or ""),
        ))
    elif sort == "registered":
        courses.sort(key=lambda c: (-registered_of(c), str(c.get("課程名稱") or "")))
    elif sort == "name":
        courses.sort(key=lambda c: str(c.get("課程名稱") or ""))
    elif query:
        courses.sort(key=lambda c: (-c.get("relevance", 0.0), str(c.get("課程名稱") or "")))
    else:
        courses.sort(key=lambda c: (str(c.get("課程名稱") or ""), str(c.get("課程代碼") or "")))

    return courses
