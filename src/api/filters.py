"""課程篩選與分類法 —— /search、/recommend、/stats、/dashboard 共用。

抽成獨立模組的原因：原本篩選邏輯全寫在 recommend 端點裡，而且「開課班別」「星期」
「空堂」這幾項前端還會再跑一次。後端先 head(50) 截斷、前端才過濾，等於把符合條件
的課在截斷時就丟掉了，使用者看到的結果數遠少於實際符合的數量。統一收在這裡之後，
截斷一律發生在所有條件都套用完、且排序完成之後。

分類法（通識類別、學制、學院…）一律從資料現況推導，不寫死選項清單，
沿用本專案「新學期上線不必改前端」的做法。
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

WEEKDAY_TO_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}
NUM_TO_WEEKDAY = {v: k for k, v in WEEKDAY_TO_NUM.items()}

# 「部別」欄位在處理後的資料裡已是乾淨的三值（大學部/碩士班/博士班），
# 可直接比對；研究所是碩＋博的方便集合，供前端「只看研究所」用。
LEVEL_GRADUATE = "研究所"
LEVEL_ALIASES: Dict[str, List[str]] = {
    "大學部": ["大學部"],
    "碩士班": ["碩士班"],
    "博士班": ["博士班"],
    LEVEL_GRADUATE: ["碩士班", "博士班"],
}

# 開課班別(代表) 直接命中的全校性類別（推薦頁的分類按鈕沿用這組值）
GLOBAL_CLASS_CATEGORIES = [
    "核心通識", "精進中文", "精進英外文", "教育學程", "大二體育", "大三、四體育",
]

# 學院 → 跨學院通識的領域代號。
#
# 「跨學院通識不能選本院開的課」這條規則不能靠「學院」欄位判斷：所有核心通識課的
# 學院都是「通識教育中心」，拿學生的學院去比對永遠不會相等，等於這條規則沒有生效。
# 實際承載領域資訊的是「課程性質」的後綴，例如 跨學院通識(文) 就是文學院開的。
COLLEGE_TO_GENERAL_DOMAIN = {
    "文學院": "文",
    "理學院": "理",
    "工學院": "工",
    "管理學院": "管",
    "教育學院": "教",
    "科技學院": "科技",
    "社會科學暨體育學院": "社體",
}


def exclude_own_college_general(df: pd.DataFrame, college: Optional[str]) -> pd.DataFrame:
    """排除學生本院開設的跨學院通識課。"""
    if df is None or df.empty or not college or "課程性質" not in df.columns:
        return df

    domain = COLLEGE_TO_GENERAL_DOMAIN.get(str(college).strip())
    if not domain:
        return df

    classified = df["課程性質"].map(classify_general)
    own = classified.map(lambda gs: gs[0] == "跨學院通識" and gs[1] == domain)
    return df[~own]


def normalize_weekday(value: Any) -> Optional[int]:
    """把『星期』欄位（資料裡是「一」～「六」）或前端傳來的數字統一成 1~7"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    if text in WEEKDAY_TO_NUM:
        return WEEKDAY_TO_NUM[text]
    if text.isdigit():
        num = int(text)
        return num if 1 <= num <= 7 else None
    return None


def classify_general(nature: Any) -> tuple:
    """把『課程性質』對應到 (通識大類, 子類)；不是通識課回傳 (None, None)。

    彰師大的通識在「課程性質」欄位裡是完整分類法：
      跨學院通識(文) / (理) / (工) / (管) / (教) / (社體) / (科技)
      素養通識-文化美學與文明 / 素養通識-生活藝能及應用
      校必(通識)
    前兩類合起來剛好等於開課班別為「核心通識」的課，第三類則掛在各系班級下。
    """
    text = str(nature or "").strip()
    if text.startswith("跨學院通識"):
        match = re.search(r"[（(]([^）)]+)[）)]", text)
        return "跨學院通識", (match.group(1) if match else "")
    if text.startswith("素養通識"):
        sub = text.split("-", 1)[1].strip() if "-" in text else ""
        return "素養通識", sub
    if text == "校必(通識)":
        return "校訂必修通識", ""
    return None, None


def build_general_taxonomy(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """從資料現況推導通識分類選單（大類 → 子類 → 門數）"""
    if df is None or df.empty or "課程性質" not in df.columns:
        return []

    groups: Dict[str, Dict[str, int]] = {}
    for nature, count in df["課程性質"].value_counts().items():
        group, sub = classify_general(nature)
        if group is None:
            continue
        groups.setdefault(group, {})
        groups[group][sub] = groups[group].get(sub, 0) + int(count)

    # 先跨學院、再素養、最後校訂必修，與學生選課時的思考順序一致
    order = ["跨學院通識", "素養通識", "校訂必修通識"]
    result = []
    for group in sorted(groups, key=lambda g: order.index(g) if g in order else 99):
        subs = [
            {"name": sub, "course_count": count}
            for sub, count in sorted(groups[group].items(), key=lambda kv: -kv[1])
            if sub
        ]
        result.append({
            "group": group,
            "subs": subs,
            "course_count": sum(groups[group].values()),
        })
    return result


def _series_str(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column].fillna("").astype(str).str.strip()


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def filter_by_semester(df: pd.DataFrame, year: Optional[int], semester: Optional[int]) -> pd.DataFrame:
    if df is None or df.empty or year is None or semester is None:
        return df
    return df[
        (df["學年度"].astype(str) == str(year))
        & (df["學期"].astype(str) == str(semester))
    ]


def apply_filters(
    df: pd.DataFrame,
    *,
    level: Optional[str] = None,
    general_group: Optional[str] = None,
    general_subs: Optional[Sequence[str]] = None,
    category: Optional[str] = None,
    college: Optional[str] = None,
    department: Optional[str] = None,
    grade: Optional[str] = None,
    division: Optional[str] = None,
    days: Optional[Sequence[Any]] = None,
    periods: Optional[Sequence[int]] = None,
    min_credits: Optional[float] = None,
    max_credits: Optional[float] = None,
    english_only: Optional[bool] = None,
    has_vacancy: Optional[bool] = None,
    keyword: Optional[str] = None,
    exclude_courses: Optional[Sequence[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """套用所有課程篩選條件。每個條件都先確認欄位存在，缺欄位就跳過該條件。"""
    if df is None or df.empty:
        return df

    out = df

    # --- 學制：大學部 / 碩士班 / 博士班 / 研究所（碩＋博）---
    if level and "部別" in out.columns:
        wanted = LEVEL_ALIASES.get(level, [level])
        out = out[_series_str(out, "部別").isin(wanted)]

    # --- 通識類別（依課程性質）---
    if general_group and "課程性質" in out.columns:
        subs = set(_as_list(general_subs))
        classified = out["課程性質"].map(classify_general)
        group_match = classified.map(lambda gs: gs[0] == general_group)
        if subs:
            group_match &= classified.map(lambda gs: gs[1] in subs)
        out = out[group_match]

    # --- 全校性分類（開課班別(代表)）---
    if category and "開課班別(代表)" in out.columns:
        if category in GLOBAL_CLASS_CATEGORIES:
            out = out[_series_str(out, "開課班別(代表)").str.contains(category, na=False)]

    if college and "學院" in out.columns:
        out = out[_series_str(out, "學院") == str(college).strip()]

    if department:
        dept = str(department).strip()
        if "科系" in out.columns and "開課班別(代表)" in out.columns:
            out = out[
                (_series_str(out, "科系") == dept)
                | _series_str(out, "開課班別(代表)").str.contains(re.escape(dept), na=False)
            ]
        elif "開課班別(代表)" in out.columns:
            out = out[_series_str(out, "開課班別(代表)").str.contains(re.escape(dept), na=False)]

    if grade and "年級" in out.columns:
        out = out[_series_str(out, "年級") == str(grade).strip()]

    if division and "學制" in out.columns:
        out = out[_series_str(out, "學制") == str(division).strip()]

    # --- 星期：資料存中文，前端可能送數字，兩邊都正規化成 1~7 再比 ---
    day_list = [normalize_weekday(d) for d in _as_list(days)]
    day_set = {d for d in day_list if d}
    if day_set and "星期" in out.columns:
        out = out[out["星期"].map(normalize_weekday).isin(day_set)]

    # --- 節次：課程的整段上課時間都要落在指定節次內 ---
    period_set = {int(p) for p in (periods or []) if str(p).strip().isdigit()}
    if period_set and {"起始節次", "結束節次"}.issubset(out.columns):
        start = pd.to_numeric(out["起始節次"], errors="coerce")
        end = pd.to_numeric(out["結束節次"], errors="coerce")
        covered = [
            bool(s == s and e == e and set(range(int(s), int(e) + 1)) <= period_set)
            for s, e in zip(start, end)
        ]
        out = out[pd.Series(covered, index=out.index)]

    if "學分" in out.columns and (min_credits is not None or max_credits is not None):
        credits = pd.to_numeric(out["學分"], errors="coerce")
        if min_credits is not None:
            out = out[credits.fillna(-1) >= float(min_credits)]
        if max_credits is not None:
            credits = pd.to_numeric(out["學分"], errors="coerce")
            out = out[credits.fillna(10**6) <= float(max_credits)]

    if english_only and "全英語授課" in out.columns:
        flags = out["全英語授課"]
        out = out[flags.astype(str).str.lower().isin(["true", "1", "yes", "y"]) | (flags == True)]  # noqa: E712

    if has_vacancy and {"上限人數", "登記人數"}.issubset(out.columns):
        limit = pd.to_numeric(out["上限人數"], errors="coerce").fillna(0)
        registered = pd.to_numeric(out["登記人數"], errors="coerce").fillna(0)
        out = out[limit > registered]

    if keyword:
        query = str(keyword).strip().lower()
        searchable = ["課程名稱", "英文課程名稱", "教師姓名", "課程代碼", "開課班別(代表)"]
        mask = None
        for column in searchable:
            if column not in out.columns:
                continue
            hit = _series_str(out, column).str.lower().str.contains(re.escape(query), na=False)
            mask = hit if mask is None else (mask | hit)
        if mask is not None:
            out = out[mask]

    if exclude_courses and {"課程代碼", "序號"}.issubset(out.columns):
        pairs = {
            (str(c.get("code", "")).strip(), str(c.get("serial", "")).strip())
            for c in exclude_courses
            if isinstance(c, dict)
        }
        pairs.discard(("", ""))
        if pairs:
            keys = list(zip(_series_str(out, "課程代碼"), _series_str(out, "序號")))
            out = out[pd.Series([k not in pairs for k in keys], index=out.index)]

    return out


def build_filter_options(df: pd.DataFrame) -> Dict[str, Any]:
    """一次回傳前端建選單所需的所有可選值（皆由資料推導）"""
    if df is None or df.empty:
        return {"levels": [], "colleges": [], "divisions": [], "general": [], "categories": []}

    def counted(column: str) -> List[Dict[str, Any]]:
        if column not in df.columns:
            return []
        return [
            {"name": str(name), "course_count": int(count)}
            for name, count in df[column].dropna().value_counts().items()
            if str(name).strip()
        ]

    # 「學院」欄位混了非學院單位（通識教育中心、語文中心、進修推廣部、軍訓室…）。
    # 把它們列進學院選單會造成一個難查的陷阱：核心通識課的學院一律是「通識教育中心」，
    # 使用者只要選了任何一個真學院再挑通識類別，交集必定是 0 門。
    # 那些單位的課本來就該從「全校性課程」或「通識類別」進入，因此這裡只留真學院。
    colleges = [c for c in counted("學院") if c["name"].endswith("學院")]

    levels = counted("部別")
    graduate_total = sum(item["course_count"] for item in levels if item["name"] in ("碩士班", "博士班"))
    if graduate_total:
        levels.append({"name": LEVEL_GRADUATE, "course_count": graduate_total})

    categories = []
    if "開課班別(代表)" in df.columns:
        班別 = _series_str(df, "開課班別(代表)")
        for name in GLOBAL_CLASS_CATEGORIES:
            count = int(班別.str.contains(name, na=False).sum())
            if count:
                categories.append({"name": name, "course_count": count})

    return {
        "levels": levels,
        "colleges": colleges,
        "divisions": counted("學制"),
        "general": build_general_taxonomy(df),
        "categories": categories,
    }
