"""選課助理可呼叫的工具。每個工具都只是現有邏輯的薄包裝：
篩選用 api.filters.apply_filters（與查詢頁同一套）、中籤預測用 api.predictions.PredictionStore，
大綱搜尋用 ai.index.SyllabusIndex。回傳精簡 JSON，控制在 LLM context 內。
"""

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd

from api.filters import GLOBAL_CLASS_CATEGORIES, LEVEL_ALIASES, apply_filters
from api.filters import normalize_weekday as day_to_num

from .parse import TIME_OF_DAY

PERIOD_TIMES = {
    1: ("08:10", "09:00"), 2: ("09:05", "09:55"), 3: ("10:15", "11:05"), 4: ("11:10", "12:00"),
    5: ("13:10", "14:00"), 6: ("14:05", "14:55"), 7: ("15:15", "16:05"), 8: ("16:10", "17:00"),
    9: ("17:10", "18:00"), 10: ("18:20", "19:10"), 11: ("19:15", "20:05"), 12: ("20:10", "21:00"),
    13: ("21:05", "21:55"), 14: ("12:05", "12:55"),
}
DAY_ZH = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}


def _int(v) -> Optional[int]:
    try:
        f = float(v)
        return None if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return None


def course_key(c: Dict[str, Any]) -> Tuple[str, str]:
    code = str(c.get("課程代碼", c.get("code", ""))).strip()
    serial = _int(c.get("序號", c.get("serial")))
    return code, str(serial) if serial is not None else str(c.get("序號", c.get("serial", ""))).strip()


def slots_of(c: Dict[str, Any]) -> Set[Tuple[int, int]]:
    d = day_to_num(c.get("星期"))
    s, e = _int(c.get("起始節次")), _int(c.get("結束節次"))
    if not d or not s or not e:
        return set()
    return {(d, p) for p in range(s, e + 1)}


def time_text(c: Dict[str, Any]) -> str:
    d = day_to_num(c.get("星期"))
    s, e = _int(c.get("起始節次")), _int(c.get("結束節次"))
    if not d or not s or not e:
        return "時間未定"
    t = f"{PERIOD_TIMES.get(s, ('', ''))[0]}–{PERIOD_TIMES.get(e, ('', ''))[1]}"
    return f"週{DAY_ZH[d]} 第{s}-{e}節（{t}）"


@dataclass
class CourseContext:
    semester_df: pd.DataFrame                      # 目標學期（API 同款，每門課一列）
    history_df: pd.DataFrame                       # 全部學期（每門課一列）
    year: int
    semester: int
    current_courses: List[Dict[str, Any]] = field(default_factory=list)
    predict: Optional[Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = None
    syllabus_index: Any = None

    def __post_init__(self):
        self._by_key = {course_key(r): r for r in self.semester_df.to_dict("records")}

    def lookup(self, code, serial) -> Optional[Dict[str, Any]]:
        return self._by_key.get(course_key({"code": code, "serial": serial}))

    @property
    def occupied(self) -> Set[Tuple[int, int]]:
        occ = set()
        for c in self.current_courses:
            occ |= slots_of(c)
        return occ

    def pred(self, c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.predict(c) if self.predict else None

    def compact(self, c: Dict[str, Any]) -> Dict[str, Any]:
        p = self.pred(c)
        code, serial = course_key(c)
        return {
            "code": code, "serial": serial,
            "name": c.get("課程名稱"), "teacher": c.get("教師姓名"),
            "credits": c.get("學分"), "class": c.get("開課班別(代表)"), "type": c.get("課程性質"),
            "time": time_text(c), "location": c.get("上課地點") or "",
            "capacity": _int(c.get("上限人數")),
            "p_full": p["p_full"] if p else None,
            "est_admit": p["est_admit"] if p else None,
            "admit_range": [p["admit_lo"], p["admit_hi"]] if p else None,
        }


# ------------------------------------------------------------------ tool specs (Ollama / OpenAI 格式)

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "find_courses",
            "description": "依條件在目標學期找課。會自動排除已在課表上的課；預設也排除與現有課表衝堂的課。"
                           "回傳每門課的時間、學分與中籤預測（p_full=爆滿機率，est_admit=預估中籤率）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "課名、教師或備註關鍵字，例如「資料分析」"},
                    "category": {"type": "string", "enum": GLOBAL_CLASS_CATEGORIES,
                                 "description": "全校性課程類別；找系上課程時不要填，改用 department"},
                    "college": {"type": "string", "description": "學院全名，例如「管理學院」"},
                    "department": {"type": "string", "description": "科系全名，例如「資訊工程學系」"},
                    "grade": {"type": "string", "description": "年級數字，例如 \"2\""},
                    "level": {"type": "string", "enum": list(LEVEL_ALIASES)},
                    "preferred_days": {"type": "array", "items": {"type": "string"}, "description": "只要這些星期，例如 [\"二\",\"四\"]"},
                    "avoid_days": {"type": "array", "items": {"type": "string"}, "description": "不要這些星期，例如 [\"五\"]"},
                    "credits": {"type": "number", "description": "學分數"},
                    "time_of_day": {"type": "string", "enum": ["morning", "afternoon", "evening"]},
                    "avoid_first_period": {"type": "boolean", "description": "不要第 1 節（早八）"},
                    "max_p_full": {"type": "number", "description": "爆滿機率上限（0～1），使用者說好選、不要太難搶時用 0.3"},
                    "only_free_slots": {"type": "boolean", "description": "只找不跟現有課表衝堂的課，預設 true"},
                    "limit": {"type": "integer", "description": "最多回傳幾門，預設 10、上限 30"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search_syllabus",
            "description": "用自然語言搜尋教學大綱內容（例如「用 Python 做資料分析」），找出內容相關的課與命中的大綱段落。"
                           "只查目標學期；適合課名看不出主題時使用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "k": {"type": "integer", "description": "回傳幾門，預設 8"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_course_detail",
            "description": "查一門課的詳細資料、中籤預測與同課名同教師的歷年登記紀錄。",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "serial": {"type": "string"}},
                "required": ["code", "serial"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_schedule_conflict",
            "description": "檢查一組課（加上使用者現有課表）彼此是否衝堂。",
            "parameters": {
                "type": "object",
                "properties": {
                    "courses": {"type": "array", "items": {"type": "object", "properties": {
                        "code": {"type": "string"}, "serial": {"type": "string"}}}},
                },
                "required": ["courses"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_course_history",
            "description": "查某課名或教師在過去學期的開課與登記紀錄（上限、登記人數、實際中籤率）。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "課名或教師姓名"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


# ------------------------------------------------------------------ implementations

def find_courses(ctx: CourseContext, keyword: str = None, category: str = None, college: str = None,
                 department: str = None, grade: str = None, level: str = None, preferred_days=None,
                 avoid_days=None, credits=None, time_of_day: str = None, avoid_first_period: bool = False,
                 max_p_full=None, only_free_slots: bool = True, limit: int = 10, **_ignored) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 10), 30))
    current = [{"code": k[0], "serial": k[1]} for k in map(course_key, ctx.current_courses)]
    periods = None
    if time_of_day in TIME_OF_DAY:
        lo, hi = TIME_OF_DAY[time_of_day]
        periods = list(range(lo, hi + 1))
    df = apply_filters(
        ctx.semester_df, level=level, category=category if category in GLOBAL_CLASS_CATEGORIES else None,
        college=college, department=department, grade=grade, days=preferred_days, periods=periods,
        min_credits=credits, max_credits=credits, keyword=keyword, exclude_courses=current,
    )
    rows = df.to_dict("records")
    avoid = {day_to_num(d) for d in (avoid_days or [])} - {None}
    if avoid:
        rows = [r for r in rows if day_to_num(r.get("星期")) not in avoid]
    if avoid_first_period:
        rows = [r for r in rows if _int(r.get("起始節次")) != 1]
    if only_free_slots is not False:
        occ = ctx.occupied
        rows = [r for r in rows if not (slots_of(r) & occ)]
    compact = [ctx.compact(r) for r in rows]
    if max_p_full is not None:
        compact = [c for c in compact if c["p_full"] is None or c["p_full"] <= float(max_p_full)]
        compact.sort(key=lambda c: c["p_full"] if c["p_full"] is not None else 0.0)
    return {"count": len(compact), "courses": compact[:limit]}


def semantic_search_syllabus(ctx: CourseContext, query: str, k: int = 8, **_ignored) -> Dict[str, Any]:
    if ctx.syllabus_index is None:
        return {"available": False, "hits": [], "note": "教學大綱索引尚未建立（python main.py build-index）"}
    k = max(1, min(int(k or 8), 20))
    hits = ctx.syllabus_index.search(query, k=k, year=ctx.year, semester=ctx.semester)
    out = []
    for h in hits:
        c = ctx.lookup(h["code"], h["serial"])
        if c is None:
            continue
        out.append({**ctx.compact(c), "score": round(h["score"], 3), "section": h.get("section"),
                    "snippet": h["text"][:120]})
    return {"available": True, "hits": out}


def get_course_detail(ctx: CourseContext, code: str, serial: str, **_ignored) -> Dict[str, Any]:
    c = ctx.lookup(code, serial)
    if c is None:
        return {"found": False}
    return {"found": True, **ctx.compact(c), "note": str(c.get("備註") or "")[:200],
            "syllabus_url": c.get("教學大綱連結") or "",
            "history": _history(ctx, name=str(c.get("課程名稱") or ""), teacher=str(c.get("教師姓名") or ""))}


def _history(ctx: CourseContext, name: str = None, teacher: str = None, query: str = None, limit: int = 8):
    h = ctx.history_df
    cur = (h["學年度"].astype(int) * 10 + h["學期"].astype(int)) < ctx.year * 10 + ctx.semester
    h = h[cur]
    if query:
        q = str(query)
        h = h[h["課程名稱"].astype(str).str.contains(q, regex=False, na=False) |
              h["教師姓名"].astype(str).str.contains(q, regex=False, na=False)]
    else:
        h = h[(h["課程名稱"].astype(str) == name) & (h["教師姓名"].astype(str) == teacher)]
    h = h.sort_values(["學年度", "學期"], ascending=False).head(limit)
    out = []
    for r in h.to_dict("records"):
        cap, reg = _int(r.get("上限人數")) or 0, _int(r.get("登記人數")) or 0
        out.append({"semester": f"{_int(r['學年度'])}-{_int(r['學期'])}", "name": r.get("課程名稱"),
                    "teacher": r.get("教師姓名"), "capacity": cap, "registered": reg,
                    "admit_rate": round(min(1.0, cap / reg), 3) if cap > 0 and reg > 0 else None})
    return out


def get_course_history(ctx: CourseContext, query: str, **_ignored) -> Dict[str, Any]:
    return {"records": _history(ctx, query=query, limit=12)}


def check_schedule_conflict(ctx: CourseContext, courses: List[Dict[str, Any]], **_ignored) -> Dict[str, Any]:
    items = [(f"{c.get('課程名稱')}（現有課表）", slots_of(c)) for c in ctx.current_courses]
    unknown = []
    for ref in courses or []:
        c = ctx.lookup(ref.get("code"), ref.get("serial"))
        if c is None:
            unknown.append(ref)
            continue
        items.append((f"{c.get('課程名稱')}（{course_key(c)[0]}）", slots_of(c)))
    conflicts = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            overlap = items[i][1] & items[j][1]
            if overlap:
                d, p = sorted(overlap)[0]
                conflicts.append({"a": items[i][0], "b": items[j][0], "at": f"週{DAY_ZH[d]} 第{p}節"})
    return {"conflicts": conflicts, "unknown": unknown}


IMPLS = {
    "find_courses": find_courses,
    "semantic_search_syllabus": semantic_search_syllabus,
    "get_course_detail": get_course_detail,
    "check_schedule_conflict": check_schedule_conflict,
    "get_course_history": get_course_history,
}


def run_tool(ctx: CourseContext, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name not in IMPLS:
        return {"error": f"沒有這個工具：{name}"}
    try:
        return IMPLS[name](ctx, **(args or {}))
    except Exception as e:  # 工具出錯要回給模型，讓它換個方式，而不是整個對話失敗
        return {"error": f"{type(e).__name__}: {e}"}
