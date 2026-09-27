"""把「週五不要有課、2 學分通識、跟資料分析有關、不要太難搶」這類中文需求解析成篩選條件。

給 MockClient 當離線大腦，也給評估腳本當「標準答案」的來源：評估時用它檢查 LLM 的建議
有沒有違反使用者講出來的條件。只處理常見說法，解析不到的就留空，不猜。
"""

import re
from typing import Any, Dict, List

DAY_CHARS = "一二三四五六日"
NEG_WORDS = ("不要", "不想", "沒課", "避開", "空下來", "不排", "不上", "休息", "空出")
POS_WORDS = ("只要", "只想", "想要", "希望", "集中", "排在", "都在")

CATEGORY_RULES = [
    (r"教育學程", "教育學程"),
    (r"核心通識|通識", "核心通識"),
    (r"英文|英外文|外文", "精進英外文"),
    (r"精進中文|國文", "精進中文"),
    (r"大三.{0,2}體育|大四.{0,2}體育", "大三、四體育"),
    (r"體育", "大二體育"),
]

EASY_WORDS = ("好選", "不要太難搶", "不要太搶", "容易選", "容易上", "穩上", "不想抽", "不要抽", "好中籤", "不要爆滿")

TIME_OF_DAY = {"morning": (1, 4), "afternoon": (5, 9), "evening": (10, 13)}

CN_NUM = {"一": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _days_in(clause: str) -> List[str]:
    days = []
    # 週三、週四五、星期一和三、禮拜二
    for m in re.finditer(r"(?:週|周|星期|禮拜)([一二三四五六日](?:[、,和跟及與]?[一二三四五六日])*)", clause):
        days += [c for c in m.group(1) if c in DAY_CHARS]
    return list(dict.fromkeys(days))


def parse_request(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    avoid, prefer = [], []
    for clause in re.split(r"[，,。；;！!？?\n]", text):
        days = _days_in(clause)
        if not days:
            continue
        if any(w in clause for w in NEG_WORDS):
            avoid += days
        elif any(w in clause for w in POS_WORDS):
            prefer += days
    if avoid:
        out["avoid_days"] = list(dict.fromkeys(avoid))
    if prefer:
        out["preferred_days"] = list(dict.fromkeys(prefer))

    for pattern, cat in CATEGORY_RULES:
        if re.search(pattern, text):
            out["category"] = cat
            break

    m = re.search(r"(\d+|[一兩二三四五六])\s*學分", text)
    if m:
        v = m.group(1)
        out["credits"] = int(v) if v.isdigit() else CN_NUM[v]

    if any(w in text for w in EASY_WORDS):
        out["max_p_full"] = 0.3

    if re.search(r"(不要|不想|避開).{0,2}早八", text):
        out["avoid_first_period"] = True
    for word, key in (("早上", "morning"), ("上午", "morning"), ("下午", "afternoon"), ("晚上", "evening"), ("夜間", "evening")):
        if word in text and not re.search(rf"(不要|不想|避開).{{0,2}}{word}", text):
            out["time_of_day"] = key
            break

    m = re.search(r"(\d+|[一兩二三四五六])\s*門", text)
    if m:
        v = m.group(1)
        out["limit"] = int(v) if v.isdigit() else CN_NUM[v]

    kw = None
    for pat in (r"(?:跟|和|與)(.{1,12}?)(?:有關|相關)", r"關於(.{1,12}?)(?:的課|課程|$|，|。)",
                r"想學(.{1,12}?)(?:的課|課程|$|，|。)", r"([^\s，,。的]{2,10})相關的"):
        m = re.search(pat, text)
        if m:
            kw = m.group(1).strip(" 的")
            break
    if kw:
        out["keyword"] = kw
    return out


def course_satisfies(course: Dict[str, Any], cond: Dict[str, Any], pred: Dict[str, Any] = None) -> Dict[str, bool]:
    """逐條檢查一門課是否符合條件（評估用）。回傳 {條件名: 是否符合}，只檢查 cond 有提到的條件。"""
    res = {}
    day = str(course.get("星期") or "")
    if "avoid_days" in cond:
        res["avoid_days"] = day not in cond["avoid_days"]
    if "preferred_days" in cond:
        res["preferred_days"] = day in cond["preferred_days"]
    if "category" in cond:
        res["category"] = cond["category"] in str(course.get("開課班別(代表)") or "")
    if "credits" in cond:
        try:
            res["credits"] = float(course.get("學分")) == float(cond["credits"])
        except (TypeError, ValueError):
            res["credits"] = False
    if "max_p_full" in cond:
        p = (pred or {}).get("p_full") if pred else None
        res["max_p_full"] = p is None or p <= cond["max_p_full"]
    if cond.get("avoid_first_period"):
        res["avoid_first_period"] = str(course.get("起始節次")) not in ("1", "1.0")
    if "time_of_day" in cond:
        lo, hi = TIME_OF_DAY[cond["time_of_day"]]
        try:
            s = int(float(course.get("起始節次")))
            res["time_of_day"] = lo <= s <= hi
        except (TypeError, ValueError):
            res["time_of_day"] = False
    return res
