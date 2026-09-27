"""統計儀表板的資料組裝。

/api/courses/stats 原本只回總課數、教師數與幾個 value_counts，前端從沒接過。
這裡改成一次備齊儀表板要畫的東西：學期趨勢、學院分佈、時段熱區、搶手／好選課排行。

所有牽涉「登記/選上」的統計都只用已結算學期（settled），理由與 app.py 的
get_settled_semesters() 相同：預選登記中的學期是不完整快照，混進去會讓數字失真。
"""

from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from api.filters import build_general_taxonomy, classify_general, normalize_weekday

TOP_N = 10

# 「教師姓名」欄位裡不是人名的值。學校對論文類課程不填授課教師，
# 直接把課程性質填進教師欄（全庫 438 筆）。教師排行必須排除，否則第一名會是「論文」。
NON_TEACHER_VALUES = {"論文"}


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series([0] * len(df), index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").fillna(0)


def _counts(df: pd.DataFrame, column: str, top: Optional[int] = None) -> List[Dict[str, Any]]:
    if column not in df.columns:
        return []
    series = df[column].dropna().value_counts()
    if top:
        series = series.head(top)
    return [
        {"name": str(name), "course_count": int(count)}
        for name, count in series.items()
        if str(name).strip()
    ]


def _course_label(row: pd.Series) -> Dict[str, Any]:
    return {
        "課程名稱": str(row.get("課程名稱") or ""),
        "教師姓名": str(row.get("教師姓名") or ""),
        "課程代碼": str(row.get("課程代碼") or ""),
        "開課班別(代表)": str(row.get("開課班別(代表)") or ""),
        "學分": float(row.get("學分") or 0),
        "上限人數": int(row.get("上限人數") or 0),
        "登記人數": int(row.get("登記人數") or 0),
        "選上人數": int(row.get("選上人數") or 0),
    }


def semester_trend(df: pd.DataFrame, settled: Set[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """各學期開課數與登記/選上/上限總量，供折線圖使用"""
    if df is None or df.empty or not {"學年度", "學期"}.issubset(df.columns):
        return []

    rows = []
    for (year, semester), group in df.groupby(["學年度", "學期"]):
        try:
            year, semester = int(year), int(semester)
        except (TypeError, ValueError):
            continue
        is_settled = (year, semester) in settled
        capacity = float(_num(group, "上限人數").sum())
        registered = float(_num(group, "登記人數").sum())
        enrolled = float(_num(group, "選上人數").sum())
        rows.append({
            "year": year,
            "semester": semester,
            "label": f"{year}-{semester}",
            "course_count": int(len(group)),
            "teacher_count": int(group["教師姓名"].nunique()) if "教師姓名" in group.columns else 0,
            "capacity": capacity,
            "registered": registered,
            "enrolled": enrolled,
            # 預選中的學期這兩個比率沒有意義，一律留 None 讓前端斷線而不是畫出假趨勢
            "saturation": round(registered / capacity, 4) if is_settled and capacity else None,
            "fill_rate": round(enrolled / capacity, 4) if is_settled and capacity else None,
            "has_results": is_settled,
        })

    rows.sort(key=lambda r: (r["year"], r["semester"]))
    return rows


def period_heatmap(df: pd.DataFrame) -> Dict[str, Any]:
    """星期 × 節次的開課密度，用來看哪些時段最擠"""
    if df is None or df.empty or not {"星期", "起始節次", "結束節次"}.issubset(df.columns):
        return {"days": [], "periods": [], "matrix": []}

    max_period = 12
    grid = {(d, p): 0 for d in range(1, 8) for p in range(1, max_period + 1)}

    days = df["星期"].map(normalize_weekday)
    starts = pd.to_numeric(df["起始節次"], errors="coerce")
    ends = pd.to_numeric(df["結束節次"], errors="coerce")

    for day, start, end in zip(days, starts, ends):
        if not day or start != start or end != end:
            continue
        for period in range(int(start), int(end) + 1):
            if (day, period) in grid:
                grid[(day, period)] += 1

    used_days = list(range(1, 7))  # 週日幾乎沒課，固定顯示一~六
    matrix = [[grid[(day, period)] for day in used_days] for period in range(1, max_period + 1)]
    return {
        "days": used_days,
        "periods": list(range(1, max_period + 1)),
        "matrix": matrix,
    }


def _settled_only(df: pd.DataFrame, settled: Set[Tuple[int, int]]) -> pd.DataFrame:
    """只留已完成分發的學期。預選中的學期人數不完整，任何比率都會失真。"""
    if df is None or df.empty or not settled or not {"學年度", "學期"}.issubset(df.columns):
        return df
    pairs = list(zip(
        pd.to_numeric(df["學年度"], errors="coerce"),
        pd.to_numeric(df["學期"], errors="coerce"),
    ))
    return df[pd.Series([p in settled for p in pairs], index=df.index)]


def _core_general_only(df: pd.DataFrame) -> pd.DataFrame:
    """只留核心通識（跨學院通識＋素養通識）。

    刻意排除校訂必修通識：那是必修，學生沒有選擇權，
    把它算進「最搶手／最好選上」只會稀釋掉真正需要搶的那批課。
    """
    if df is None or df.empty or "課程性質" not in df.columns:
        return df.iloc[0:0] if df is not None else df
    groups = df["課程性質"].map(lambda n: classify_general(n)[0])
    return df[groups.isin(["跨學院通識", "素養通識"])]


def _ratio_ranking(
    df: pd.DataFrame, settled: Set[Tuple[int, int]], ascending: bool
) -> List[Dict[str, Any]]:
    """核心通識的飽和度（登記/上限）排行；ascending=True 取最好選上的課。

    範圍限定核心通識——全校排行前幾名永遠被那幾門超熱門通識佔滿，
    而系上必修根本不存在「搶不搶手」的問題，混在一起沒有參考價值。
    """
    if df is None or df.empty or not {"上限人數", "登記人數"}.issubset(df.columns):
        return []

    working = _core_general_only(_settled_only(df, settled))

    if working is None or working.empty:
        return []

    capacity = _num(working, "上限人數")
    registered = _num(working, "登記人數")
    # 上限太小的課（如個別指導）比率會失真，設一個最低規模門檻
    valid = working[(capacity >= 10) & (registered > 0)].copy()

    # 論文、獨立研究這類沒有固定上課時間的課，登記數低不代表「好選」，
    # 它們本來就不是搶課的對象，排行榜列出來只會洗版
    if "星期" in valid.columns:
        valid = valid[valid["星期"].notna()]
    if valid.empty:
        return []

    valid["saturation"] = _num(valid, "登記人數") / _num(valid, "上限人數")
    valid = valid.sort_values("saturation", ascending=ascending)

    # 同一門課會在多個學期各出現一次，只保留最具代表性的那筆，
    # 否則排行榜前幾名常常是同一門課的不同學年度
    results = []
    seen = set()
    for _, row in valid.iterrows():
        key = (str(row.get("課程名稱") or "").strip(), str(row.get("教師姓名") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        entry = _course_label(row)
        entry["saturation"] = round(float(row["saturation"]), 4)
        entry["學年度"] = int(row.get("學年度") or 0)
        entry["學期"] = int(row.get("學期") or 0)
        results.append(entry)
        if len(results) >= TOP_N:
            break
    return results


def general_domain_competition(
    full_df: pd.DataFrame, settled: Set[Tuple[int, int]]
) -> List[Dict[str, Any]]:
    """各通識領域的競爭程度：跨學院通識(文/理/工…)、素養通識兩類誰最難選。

    用平均飽和度（登記÷上限）而非總人數，否則開課多的領域會單純因為量大而看起來熱門。
    """
    working = _core_general_only(_settled_only(full_df, settled))
    if working is None or working.empty:
        return []

    rows = []
    for nature, group in working.groupby("課程性質"):
        big, sub = classify_general(nature)
        capacity = _num(group, "上限人數")
        registered = _num(group, "登記人數")
        valid = group[(capacity > 0) & (registered > 0)]
        if valid.empty:
            continue
        ratios = _num(valid, "登記人數") / _num(valid, "上限人數")
        rows.append({
            "name": f"{big}（{sub}）" if sub else str(big),
            "group": big,
            "sub": sub,
            "course_count": int(len(valid)),
            "avg_saturation": round(float(ratios.mean()), 4),
            "max_saturation": round(float(ratios.max()), 4),
        })

    rows.sort(key=lambda r: -r["avg_saturation"])
    return rows


def period_acceptance(full_df: pd.DataFrame, settled: Set[Tuple[int, int]]) -> Dict[str, Any]:
    """星期 × 節次的平均飽和度（登記÷上限），數字越低代表該時段的課越好選。

    與 period_heatmap 的差別：那張算「開幾門課」，這張算「有多難搶」。

    這裡刻意用飽和度而非中籤率。中籤率是 min(1, 上限÷登記)，上限被壓在 1，
    而全校絕大多數課都不必搶，實測各時段落在 0.88~1.00、差距僅 0.12，
    整張圖幾乎同一個顏色。改用未設上限的飽和度後範圍是 0.08~1.41，才看得出時段差異。
    """
    empty = {"days": [], "periods": [], "matrix": [], "counts": []}
    working = _settled_only(full_df, settled)
    if working is None or working.empty:
        return empty
    if not {"星期", "起始節次", "結束節次", "上限人數", "登記人數"}.issubset(working.columns):
        return empty

    capacity = _num(working, "上限人數")
    registered = _num(working, "登記人數")
    valid = working[(capacity > 0) & (registered > 0)]
    if valid.empty:
        return empty

    rates = _num(valid, "登記人數") / _num(valid, "上限人數")
    days = valid["星期"].map(normalize_weekday)
    starts = pd.to_numeric(valid["起始節次"], errors="coerce")
    ends = pd.to_numeric(valid["結束節次"], errors="coerce")

    max_period = 12
    totals = {(d, p): 0.0 for d in range(1, 8) for p in range(1, max_period + 1)}
    counts = {(d, p): 0 for d in range(1, 8) for p in range(1, max_period + 1)}

    for day, start, end, rate in zip(days, starts, ends, rates):
        if not day or start != start or end != end:
            continue
        for period in range(int(start), int(end) + 1):
            if (day, period) in totals:
                totals[(day, period)] += float(rate)
                counts[(day, period)] += 1

    used_days = list(range(1, 7))
    matrix, count_matrix = [], []
    for period in range(1, max_period + 1):
        matrix.append([
            round(totals[(d, period)] / counts[(d, period)], 4) if counts[(d, period)] else None
            for d in used_days
        ])
        count_matrix.append([counts[(d, period)] for d in used_days])

    return {
        "days": used_days,
        "periods": list(range(1, max_period + 1)),
        "matrix": matrix,
        "counts": count_matrix,
    }


def teacher_stats(
    current_df: pd.DataFrame, full_df: pd.DataFrame, settled: Set[Tuple[int, int]]
) -> Dict[str, List[Dict[str, Any]]]:
    """教師開課數（本學期）與平均登記人數（歷年已結算學期）排行。"""
    result: Dict[str, List[Dict[str, Any]]] = {"most_courses": [], "most_popular": []}
    if current_df is None or current_df.empty or "教師姓名" not in current_df.columns:
        return result

    def real_teachers(df: pd.DataFrame) -> pd.DataFrame:
        """濾掉不是人名的教師欄位值。

        論文、論文指導這類課程，來源資料的教師欄位不填人名而填「論文」（全庫 438 筆）。
        不濾掉的話「本學期開課最多的教師」第一名會是「論文」122 門。

        用明確清單而非「教師姓名 == 課程名稱」之類的模糊比對：課名可能是「論文指導(一)」
        而教師欄仍是「論文」，比對不到；反過來用包含關係又可能誤傷真實姓名
        （例如老師姓名剛好是課名的一部分）。日後若發現其他非人名值，加進這個集合即可。
        """
        name = df["教師姓名"].fillna("").astype(str).str.strip()
        return df[(name != "") & (~name.isin(NON_TEACHER_VALUES))]

    current_real = real_teachers(current_df)
    # 「教師姓名」可能是多位老師合開的字串，這裡不拆開——拆開會把共同授課
    # 誤算成各自開課，反而失真
    counts = current_real["教師姓名"].dropna().value_counts().head(TOP_N)
    result["most_courses"] = [
        {"name": str(name), "course_count": int(count)}
        for name, count in counts.items() if str(name).strip()
    ]

    working = real_teachers(_settled_only(full_df, settled))
    if working is None or working.empty or "登記人數" not in working.columns:
        return result

    registered = _num(working, "登記人數")
    valid = working[registered > 0].copy()
    if valid.empty:
        return result

    valid["_reg"] = _num(valid, "登記人數")
    grouped = valid.groupby("教師姓名")["_reg"].agg(["mean", "count"])
    # 只開過一兩門課的老師平均值不穩定，設一個最低樣本數
    grouped = grouped[grouped["count"] >= 5].sort_values("mean", ascending=False).head(TOP_N)
    result["most_popular"] = [
        {
            "name": str(name),
            "avg_registered": round(float(row["mean"]), 1),
            "course_count": int(row["count"]),
        }
        for name, row in grouped.iterrows() if str(name).strip()
    ]
    return result


def college_saturation(full_df: pd.DataFrame, settled: Set[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """各學院的平均飽和度與全英語授課比例（只計真正的學院）。"""
    working = _settled_only(full_df, settled)
    if working is None or working.empty or "學院" not in working.columns:
        return []

    capacity = _num(working, "上限人數")
    registered = _num(working, "登記人數")
    valid = working[(capacity > 0) & (registered > 0)].copy()
    if valid.empty:
        return []

    valid["_sat"] = _num(valid, "登記人數") / _num(valid, "上限人數")

    rows = []
    for college, group in valid.groupby("學院"):
        name = str(college).strip()
        # 「學院」欄位混了通識教育中心、語文中心等非學院單位，這裡只留真學院
        if not name.endswith("學院"):
            continue
        english = 0
        if "全英語授課" in group.columns:
            english = int(group["全英語授課"].astype(str).str.lower().isin(["true", "1"]).sum())
        rows.append({
            "name": name,
            "course_count": int(len(group)),
            "avg_saturation": round(float(group["_sat"].mean()), 4),
            "english_ratio": round(english / len(group), 4) if len(group) else 0.0,
        })

    rows.sort(key=lambda r: -r["avg_saturation"])
    return rows


def build_dashboard(
    full_df: pd.DataFrame,
    current_df: pd.DataFrame,
    settled: Set[Tuple[int, int]],
    year: Optional[int] = None,
    semester: Optional[int] = None,
) -> Dict[str, Any]:
    """組裝儀表板全部區塊。current_df 是選定學期，full_df 是全部歷年。"""
    if current_df is None or current_df.empty:
        return {"overview": {}, "trend": [], "colleges": [], "levels": [],
                "general": [], "heatmap": {}, "hottest": [], "easiest": [], "credits": []}

    capacity = float(_num(current_df, "上限人數").sum())
    registered = float(_num(current_df, "登記人數").sum())
    enrolled = float(_num(current_df, "選上人數").sum())
    is_settled = (year, semester) in settled if year and semester else False

    vacancy_count = int((_num(current_df, "上限人數") > _num(current_df, "登記人數")).sum())

    overview = {
        "year": year,
        "semester": semester,
        "has_results": is_settled,
        "total_courses": int(len(current_df)),
        "total_teachers": int(current_df["教師姓名"].nunique()) if "教師姓名" in current_df.columns else 0,
        "total_credits": float(round(_num(current_df, "學分").sum(), 1)),
        "english_courses": int(current_df["全英語授課"].astype(str).str.lower().isin(["true", "1"]).sum())
        if "全英語授課" in current_df.columns else 0,
        "capacity": capacity,
        "registered": registered,
        "enrolled": enrolled,
        "saturation": round(registered / capacity, 4) if capacity else None,
        "vacancy_courses": vacancy_count,
    }

    credits = []
    if "學分" in current_df.columns:
        for value, count in sorted(current_df["學分"].dropna().value_counts().items()):
            credits.append({"credits": float(value), "course_count": int(count)})

    return {
        "overview": overview,
        "trend": semester_trend(full_df, settled),
        "colleges": _counts(current_df, "學院"),
        "levels": _counts(current_df, "部別"),
        "divisions": _counts(current_df, "學制"),
        "top_departments": _counts(current_df, "開課班別(代表)", top=TOP_N),
        "general": build_general_taxonomy(current_df),
        "heatmap": period_heatmap(current_df),
        # 排行榜只採計核心通識（跨學院＋素養），必修課不存在搶不搶手的問題
        "hottest": _ratio_ranking(full_df, settled, ascending=False),
        "easiest": _ratio_ranking(full_df, settled, ascending=True),
        "credits": credits,
        "general_competition": general_domain_competition(full_df, settled),
        "period_acceptance": period_acceptance(full_df, settled),
        "teachers": teacher_stats(current_df, full_df, settled),
        "college_saturation": college_saturation(full_df, settled),
    }
