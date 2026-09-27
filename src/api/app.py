from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pathlib import Path
import pandas as pd
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from pydantic import BaseModel

from config import (
    PROCESSED_DATA_DIR, WEB_DIR, API_HOST, API_PORT, LOG_LEVEL, LOG_FORMAT, LOG_FILE, LOG_DIR,
    BASE_URL, REQUEST_TIMEOUT, MODELS_DIR,
)
from utils.common import safe_read_csv, setup_logging
from api import dashboard as dashboard_builder
from api import filters as course_filters
from api import search as course_search
from api import vacancy as vacancy_client
from api.recommender import rank_courses
from api.predictions import PredictionStore

def clean_course_data(courses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """清理課程數據，處理 NaN 並規範型別"""
    import math
    cleaned = []
    for course in courses:
        cleaned_course = {}
        for key, value in course.items():
            if isinstance(value, float) and math.isnan(value):
                cleaned_course[key] = None
            else:
                cleaned_course[key] = value
        for fld in ['起始節次', '結束節次']:
            if fld in cleaned_course and cleaned_course[fld] is not None:
                try:
                    num = float(cleaned_course[fld])
                    if num.is_integer():
                        cleaned_course[fld] = int(num)
                except Exception:
                    pass
        cleaned.append(cleaned_course)
    return cleaned

def clean_single_course(course: Dict[str, Any]) -> Dict[str, Any]:
    import math
    cleaned_course = {}
    for key, value in course.items():
        if isinstance(value, float) and math.isnan(value):
            cleaned_course[key] = None
        else:
            cleaned_course[key] = value
    return cleaned_course

# 判定學期是否已完成分發的門檻：選上人數總和 ÷ 上限人數總和。
# 以上限人數當分母，是因為它在整個選課期間都穩定；登記人數在預選中仍會持續累積。
# 實測歷年已結算學期落在 64%~70%，預選中的學期為 0%~4%，取 20% 可安全區隔。
SETTLED_ENROLLMENT_RATIO = 0.2


def get_settled_semesters(full_df: pd.DataFrame) -> Set[Tuple[int, int]]:
    """回傳「已完成分發」的學期集合。

    仍在預選登記中的學期，選上人數尚未公布（或只分發了一小部分），且登記人數還在累積，
    是不完整的快照。拿它算中籤率會偏樂觀、算飽和度會偏低，因此統計時必須排除。
    """
    if full_df is None or full_df.empty:
        return set()
    if not {'學年度', '學期', '選上人數', '上限人數'}.issubset(full_df.columns):
        return set()

    keys = [full_df['學年度'], full_df['學期']]
    enrolled = pd.to_numeric(full_df['選上人數'], errors='coerce').fillna(0).groupby(keys).sum()
    capacity = pd.to_numeric(full_df['上限人數'], errors='coerce').fillna(0).groupby(keys).sum()

    settled = set()
    for key, total_capacity in capacity.items():
        if total_capacity <= 0:
            continue
        if enrolled.get(key, 0) / total_capacity < SETTLED_ENROLLMENT_RATIO:
            continue
        try:
            settled.add((int(key[0]), int(key[1])))
        except (TypeError, ValueError):
            continue
    return settled


def calculate_historical_stats(full_df: pd.DataFrame) -> Dict[tuple, float]:
    """計算每門課（同名稱+同教師）的歷年平均選上率"""
    if full_df is None or full_df.empty:
        return {}

    if '登記人數' not in full_df.columns or '上限人數' not in full_df.columns:
        return {}

    df = full_df.copy()
    df['登記人數'] = pd.to_numeric(df['登記人數'], errors='coerce').fillna(0)
    df['上限人數'] = pd.to_numeric(df['上限人數'], errors='coerce').fillna(0)

    df['課程名稱'] = df['課程名稱'].fillna('').astype(str).str.strip()
    df['教師姓名'] = df['教師姓名'].fillna('').astype(str).str.strip()

    valid_mask = (df['登記人數'] > 0) & (df['上限人數'] > 0)

    # 排除仍在預選登記中的學期，避免未結算的人數污染歷年統計
    settled = get_settled_semesters(full_df)
    if settled and {'學年度', '學期'}.issubset(df.columns):
        semester_pairs = list(zip(
            pd.to_numeric(df['學年度'], errors='coerce'),
            pd.to_numeric(df['學期'], errors='coerce'),
        ))
        valid_mask &= pd.Series(
            [pair in settled for pair in semester_pairs], index=df.index
        )

    valid_df = df[valid_mask].copy()

    if valid_df.empty:
        return {}

    valid_df['acceptance_rate'] = valid_df['上限人數'] / valid_df['登記人數']
    valid_df['acceptance_rate'] = valid_df['acceptance_rate'].clip(upper=1.0)

    avg_rates = valid_df.groupby(['課程名稱', '教師姓名'])['acceptance_rate'].mean().to_dict()

    return avg_rates

app = FastAPI(title="Course Master API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.mount("/css", StaticFiles(directory=str(WEB_DIR / "assets" / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(WEB_DIR / "assets" / "js")), name="js")
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
# 本機用 python main.py api 開網站時，前端讀的是 scripts/build_static.py 產生的 web/data。
# 目錄可能還沒建置，check_dir=False 讓 API 照常啟動（測試也不需要先建置）。
app.mount("/data", StaticFiles(directory=str(WEB_DIR / "data"), check_dir=False), name="data")

# 中籤預測（data/models/demand_predictions.csv）；AI 相關端點見檔尾
prediction_store = PredictionStore(MODELS_DIR)

# 以資料檔的 (路徑, mtime) 作為快取版本；資料更新後不必重啟 API 就會自動重讀。
_cache_version: Optional[Tuple[str, float]] = None
_courses_cache: Dict[str, pd.DataFrame] = {}
_stats_cache: Dict[str, Any] = {}

def _latest_processed_file() -> Optional[Path]:
    processed_files = sorted(PROCESSED_DATA_DIR.glob("all_courses_*.csv"))
    return processed_files[-1] if processed_files else None

def _ensure_cache_fresh(latest_file: Path) -> None:
    """資料檔換掉或被覆寫時，清空所有衍生快取"""
    global _cache_version
    version = (str(latest_file), latest_file.stat().st_mtime)
    if _cache_version != version:
        _courses_cache.clear()
        _stats_cache.clear()
        _cache_version = version

def get_latest_courses_df() -> Optional[pd.DataFrame]:
    """載入處理後的課程資料集。

    「latest」指的是最新的**處理後檔案**，不是最新學期——這份資料含全部 9 個學期，
    需要單一學期請再套 course_filters.filter_by_semester()。
    """
    latest_file = _latest_processed_file()
    if latest_file is None:
        return None

    _ensure_cache_fresh(latest_file)

    cache_key = "latest"
    if cache_key in _courses_cache:
        return _courses_cache[cache_key]

    df = safe_read_csv(latest_file)

    if df is not None:
        if '課程代碼' in df.columns and '序號' in df.columns:
            subset = [c for c in ['學年度', '學期', '課程代碼', '序號'] if c in df.columns]
            df = df.drop_duplicates(subset=subset, keep='last')
        _courses_cache[cache_key] = df

    return df

def get_raw_courses_df() -> Optional[pd.DataFrame]:
    """未去重的處理後資料（一個上課時段一列）。中籤預測現算特徵時需要完整的時段資訊。"""
    latest_file = _latest_processed_file()
    if latest_file is None:
        return None
    _ensure_cache_fresh(latest_file)
    if "raw" not in _courses_cache:
        df = safe_read_csv(latest_file)
        if df is None:
            return None
        _courses_cache["raw"] = df
    return _courses_cache["raw"]

def get_historical_stats() -> Dict[tuple, float]:
    """歷年平均中籤率（隨資料檔快取）。

    原本每次 /search 與 /recommend 請求都對全表重跑一次 groupby，改為只算一次。
    """
    df = get_latest_courses_df()
    if df is None:
        return {}

    if 'historical_stats' not in _stats_cache:
        _stats_cache['historical_stats'] = calculate_historical_stats(df)
    return _stats_cache['historical_stats']

def get_settled_semester_set() -> Set[Tuple[int, int]]:
    """已完成分發的學期集合（隨資料檔快取）"""
    df = get_latest_courses_df()
    if df is None:
        return set()

    if 'settled_semesters' not in _stats_cache:
        _stats_cache['settled_semesters'] = get_settled_semesters(df)
    return _stats_cache['settled_semesters']

def get_courses_by_semester(year: int, semester: int) -> Optional[pd.DataFrame]:
    df = get_latest_courses_df()
    if df is None:
        return None

    cache_key = f"{year}_{semester}"
    if cache_key in _courses_cache:
        return _courses_cache[cache_key]

    filtered = df[
        (df['學年度'].astype(str) == str(year)) &
        (df['學期'].astype(str) == str(semester))
    ].copy()

    if not filtered.empty:
        _courses_cache[cache_key] = filtered
    return filtered

class CourseResponse(BaseModel):
    courses: List[Dict[str, Any]]
    total: int

class CourseQueryRequest(BaseModel):
    """課程查詢的完整條件集合。

    搜尋與推薦本來是兩支端點、兩組幾乎相同的參數（連 category 都是同一個欄位），
    現在合成同一個查詢：關鍵字、篩選、課表感知（空堂／衝堂／剩餘學分）都在這裡，
    差別只剩下 sort 選什麼。
    """
    # 關鍵字與範圍
    keyword: Optional[str] = None
    year: Optional[int] = None
    semester: Optional[int] = None

    # 篩選條件
    category: Optional[str] = None               # 全校性課程：核心通識、精進中文…
    level: Optional[str] = None                  # 學制：大學部／碩士班／博士班／研究所
    division: Optional[str] = None               # 日間部／夜間部
    college: Optional[str] = None
    department: Optional[str] = None
    grade: Optional[str] = None
    general_group: Optional[str] = None          # 通識大類：跨學院通識／素養通識／校訂必修通識
    general_subs: Optional[List[str]] = None     # 通識子類：文、理、生活藝能及應用…
    preferred_days: Optional[List[str]] = None
    periods: Optional[List[int]] = None          # 整段上課時間都要落在這些節次內
    min_credits: Optional[float] = None
    max_credits: Optional[float] = None
    english_only: Optional[bool] = None
    has_vacancy: Optional[bool] = None
    exclude_college: Optional[str] = None        # 跨學院通識用：排除本院開的課

    # 課表感知：有送才會啟用，純關鍵字搜尋不必帶
    empty_slots: Optional[List[Dict[str, int]]] = None
    current_courses: List[Dict[str, Any]] = []
    target_credits: Optional[int] = None
    exclude_conflicts: bool = False

    # 排序與分頁
    # score／relevance／vacancy／acceptance（最好選上）／competitive（最搶手）／
    # registered（登記人數最多）／credits／name
    sort: str = "score"
    limit: int = 50
    offset: int = 0


# 舊名保留，避免既有呼叫端與文件失效
RecommendRequest = CourseQueryRequest

@app.get("/")
async def read_root():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/favicon.svg")
async def read_favicon():
    return FileResponse(WEB_DIR / "favicon.svg", media_type="image/svg+xml")

@app.get("/api/courses/all")
async def get_all_courses(year: Optional[int] = None, semester: Optional[int] = None):
    try:
        if year and semester:
            df = get_courses_by_semester(year, semester)
        else:
            df = get_latest_courses_df()
        
        if df is None or df.empty:
            return CourseResponse(courses=[], total=0)
        
        courses = df.to_dict('records')
        courses = clean_course_data(courses)
        return CourseResponse(courses=courses, total=len(courses))
    except Exception as e:
        logging.error(f"獲取課程列表失敗: {e}")
        raise HTTPException(status_code=500, detail="獲取課程列表失敗")

@app.get("/api/courses/search")
async def search_courses(
    q: str = "",
    year: Optional[int] = None,
    semester: Optional[int] = None,
    level: Optional[str] = None,
    general_group: Optional[str] = None,
    general_subs: Optional[str] = None,
    category: Optional[str] = None,
    college: Optional[str] = None,
    department: Optional[str] = None,
    grade: Optional[str] = None,
    division: Optional[str] = None,
    days: Optional[str] = None,
    periods: Optional[str] = None,
    min_credits: Optional[float] = None,
    max_credits: Optional[float] = None,
    english_only: bool = False,
    has_vacancy: bool = False,
    sort: str = "relevance",
    limit: int = 50,
    offset: int = 0,
):
    """全站課程搜尋（GET 版，可直接貼網址分享）。

    與 /api/courses/query 走同一套實作，差別只在這裡不接課表感知參數
    （空堂、衝堂、剩餘學分那些帶結構的欄位不適合塞進 query string），
    因此預設排序是相關度而非推薦分數。

    逗號分隔的參數（days / general_subs）是為了讓多選條件也能用 GET 表達。
    """
    try:
        def csv_param(value: Optional[str]) -> List[str]:
            return [item.strip() for item in (value or "").split(",") if item.strip()]

        return _query_courses(CourseQueryRequest(
            keyword=q or None,
            year=year,
            semester=semester,
            level=level,
            general_group=general_group,
            general_subs=csv_param(general_subs),
            category=category,
            college=college,
            department=department,
            grade=grade,
            division=division,
            preferred_days=csv_param(days),
            periods=[int(x) for x in csv_param(periods) if x.isdigit()],
            min_credits=min_credits,
            max_credits=max_credits,
            english_only=english_only or None,
            has_vacancy=has_vacancy or None,
            sort=sort,
            limit=limit,
            offset=offset,
        ))
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"搜索課程失敗: {e}")
        raise HTTPException(status_code=500, detail="搜索失敗")


@app.get("/api/filters")
async def get_filter_options(year: Optional[int] = None, semester: Optional[int] = None):
    """前端建立篩選選單所需的所有可選值（學制、學院、學制別、通識分類、全校性類別）。

    全部由資料推導，新學期多了或少了什麼類別，前端不必跟著改。
    """
    try:
        df = get_latest_courses_df()
        if df is None or df.empty:
            return {"levels": [], "colleges": [], "divisions": [], "general": [], "categories": []}

        year, semester = _resolve_semester(df, year, semester)
        scoped = course_filters.filter_by_semester(df, year, semester)
        options = course_filters.build_filter_options(scoped)
        options["year"] = year
        options["semester"] = semester
        return options
    except Exception as e:
        logging.error(f"取得篩選選項失敗: {e}")
        raise HTTPException(status_code=500, detail="取得篩選選項失敗")

@app.get("/api/courses/by-class")
async def get_courses_by_class(department: str, class_name: str, year: int, semester: int):
    try:
        df = get_courses_by_semester(year, semester)
        if df is None or df.empty:
            return CourseResponse(courses=[], total=0)
        
        mask = (
            (df['開課班別(代表)'].astype(str).str.contains(department, na=False)) |
            (df['開課班別(代表)'].astype(str).str.contains(class_name, na=False))
        )
        required_mask = df['課程性質'].astype(str).str.contains('必修', na=False)
        required_courses = df[mask & required_mask]
        elective_courses = df[mask & ~required_mask]
        result_df = pd.concat([required_courses, elective_courses], ignore_index=True)
        courses = result_df.to_dict('records')
        courses = clean_course_data(courses)
        return CourseResponse(courses=courses, total=len(courses))
    except Exception as e:
        logging.error(f"獲取班級課程失敗: {e}")
        raise HTTPException(status_code=500, detail="獲取班級課程失敗")

def _resolve_semester(df: pd.DataFrame, year: Optional[int], semester: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
    """指定學期優先；沒指定就取資料中最新的學期"""
    if year is not None and semester is not None:
        return year, semester
    if df is None or df.empty or '學年度' not in df.columns:
        return None, None
    try:
        latest_year = int(pd.to_numeric(df['學年度'], errors='coerce').max())
        same_year = df[df['學年度'].astype(str) == str(latest_year)]
        latest_semester = int(pd.to_numeric(same_year['學期'], errors='coerce').max())
        return latest_year, latest_semester
    except (TypeError, ValueError):
        return None, None


def _filter_by_empty_slots(df: pd.DataFrame, empty_slots: Optional[List[Dict[str, int]]]) -> pd.DataFrame:
    """只留整段上課時間都落在空堂裡的課"""
    if not empty_slots or df is None or df.empty:
        return df
    if not {'星期', '起始節次', '結束節次'}.issubset(df.columns):
        return df

    available = {
        (int(slot['day']), int(slot['period']))
        for slot in empty_slots
        if isinstance(slot, dict) and 'day' in slot and 'period' in slot
    }
    if not available:
        return df

    days = df['星期'].map(course_filters.normalize_weekday)
    starts = pd.to_numeric(df['起始節次'], errors='coerce')
    ends = pd.to_numeric(df['結束節次'], errors='coerce')

    keep = []
    for day, start, end in zip(days, starts, ends):
        if not day or start != start or end != end or start <= 0 or end <= 0:
            keep.append(False)
            continue
        keep.append(all((day, p) in available for p in range(int(start), int(end) + 1)))

    return df[pd.Series(keep, index=df.index)]


def _query_courses(request: CourseQueryRequest) -> Dict[str, Any]:
    """課程查詢的唯一實作：條件篩選 → 評分 → 排序 → 分頁。

    /api/courses/query、/api/courses/recommend、/api/courses/search 全部走這裡，
    避免同一套篩選邏輯有三份實作而各自漂移。

    分頁一定發生在所有條件套用完、排序完之後。舊版是先 head(50) 才讓前端補做
    開課班別／星期／空堂過濾，符合條件的課會在截斷時就被丟掉，
    使用者常看到「只找到 3 門」但實際上有幾十門。
    """
    full_df = get_latest_courses_df()
    if full_df is None or full_df.empty:
        raise HTTPException(status_code=404, detail="沒有處理過的課程數據")

    year, semester = _resolve_semester(full_df, request.year, request.semester)
    empty_result = {
        "courses": [], "total": 0, "returned": 0, "limit": request.limit,
        "offset": request.offset, "has_more": False, "year": year, "semester": semester,
    }

    target_df = course_filters.filter_by_semester(full_df, year, semester)
    if target_df is None or target_df.empty:
        return empty_result

    filtered = course_filters.apply_filters(
        target_df,
        level=request.level,
        general_group=request.general_group,
        general_subs=request.general_subs,
        category=request.category,
        college=request.college,
        department=request.department,
        grade=request.grade,
        division=request.division,
        days=request.preferred_days,
        periods=request.periods,
        min_credits=request.min_credits,
        max_credits=request.max_credits,
        english_only=request.english_only,
        has_vacancy=request.has_vacancy,
        keyword=request.keyword,
        exclude_courses=request.current_courses,
    )

    # 跨學院通識不能選本院開的課。原本前端是比對「學院」欄位，但核心通識課的學院
    # 一律是「通識教育中心」，永遠不會等於學生的學院，這條規則等於沒有生效；
    # 真正帶領域資訊的是課程性質後綴（跨學院通識(文) = 文學院開的）。
    if request.exclude_college:
        filtered = course_filters.exclude_own_college_general(filtered, request.exclude_college)

    filtered = _filter_by_empty_slots(filtered, request.empty_slots)

    if filtered is None or filtered.empty:
        return empty_result

    stats_map = get_historical_stats()
    courses = clean_course_data(filtered.to_dict('records'))

    # 一律先評分：即使使用者選了別的排序，前端仍要顯示每門課的推薦分數與理由
    ranked = rank_courses(
        courses,
        full_df=full_df,
        stats_map=stats_map,
        semester_settled=(year, semester) in get_settled_semester_set(),
        target_credits=request.target_credits,
        current_courses=request.current_courses,
        exclude_conflicts=request.exclude_conflicts,
    )

    if request.sort and request.sort != "score":
        ranked = course_search.rank_results(
            ranked, request.keyword, sort=request.sort, stats_map=stats_map
        )

    page, meta = course_search.paginate(ranked, request.limit, request.offset)
    return {
        "courses": page,
        "returned": len(page),
        "year": year,
        "semester": semester,
        "sort": request.sort,
        **meta,
    }


@app.post("/api/courses/query")
async def query_courses(request: CourseQueryRequest):
    """課程查詢（搜尋與推薦合一）。關鍵字、篩選、課表感知、排序都在同一支。"""
    try:
        return _query_courses(request)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"課程查詢失敗: {e}")
        raise HTTPException(status_code=500, detail=f"系統錯誤: {e}")


@app.post("/api/courses/recommend")
async def recommend_courses(request: CourseQueryRequest):
    """/api/courses/query 的別名，保留給既有呼叫端。"""
    try:
        return _query_courses(request)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"推薦 API 錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"系統錯誤: {e}")

@app.get("/api/courses/history")
async def get_course_history(q: str, limit: int = 100):
    try:
        df = get_latest_courses_df()
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="沒有處理過的課程數據")
        
        query = q.lower()
        mask = (
            df['課程名稱'].astype(str).str.lower().str.contains(query, na=False) |
            df['教師姓名'].astype(str).str.lower().str.contains(query, na=False)
        )
        results = df[mask].sort_values(['學年度', '學期'], ascending=[False, False]).head(limit)
        courses = results.to_dict('records')
        courses = clean_course_data(courses)
        return CourseResponse(courses=courses, total=len(courses))
    except Exception as e:
        raise HTTPException(status_code=500, detail="獲取歷年資料失敗")

@app.get("/api/courses/stats")
async def get_course_stats():
    """簡易統計（保留舊介面）；儀表板請改用 /api/dashboard"""
    try:
        df = get_latest_courses_df()
        if df is None or df.empty:
            raise HTTPException(status_code=404, detail="沒有處理過的課程數據")
        return {
            "total_courses": len(df),
            "total_teachers": df['教師姓名'].nunique() if '教師姓名' in df.columns else 0,
            "departments": df['開課班別(代表)'].value_counts().head(10).to_dict() if '開課班別(代表)' in df.columns else {},
            "course_types": df['課程性質'].value_counts().to_dict() if '課程性質' in df.columns else {},
            "english_only": int(df['全英語授課'].sum()) if '全英語授課' in df.columns else 0,
            "avg_enrollment": float(df['選上人數'].mean()) if '選上人數' in df.columns else 0,
            "max_enrollment": int(df['選上人數'].max()) if '選上人數' in df.columns else 0,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"取得統計失敗: {e}")
        raise HTTPException(status_code=500, detail="取得統計失敗")


@app.get("/api/dashboard")
async def get_dashboard(year: Optional[int] = None, semester: Optional[int] = None):
    """統計儀表板：總覽、學期趨勢、學院／學制分佈、時段熱區、搶手與好選課排行。

    所有牽涉登記/選上的統計都只採計已結算學期，理由同 get_settled_semesters()。
    """
    try:
        full_df = get_latest_courses_df()
        if full_df is None or full_df.empty:
            raise HTTPException(status_code=404, detail="沒有處理過的課程數據")

        year, semester = _resolve_semester(full_df, year, semester)
        current_df = course_filters.filter_by_semester(full_df, year, semester)

        return dashboard_builder.build_dashboard(
            full_df, current_df, get_settled_semester_set(), year, semester
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"取得儀表板資料失敗: {e}")
        raise HTTPException(status_code=500, detail="取得儀表板資料失敗")


@app.get("/api/vacancy")
async def get_vacancy(codes: str, year: Optional[int] = None, semester: Optional[int] = None):
    """即時查詢課程目前的登記/上限（直接問學校站台，不看已爬好的 CSV）。

    缺額是會秒變的資料，用快取好的 CSV 回答只會給出過期答案，因此這支是唯一
    會對外發請求的端點。單次最多查 vacancy_client.MAX_CODES_PER_REQUEST 門。
    """
    try:
        code_list = [c.strip() for c in (codes or "").split(",") if c.strip()]
        if not code_list:
            raise HTTPException(status_code=400, detail="請提供至少一個課程代碼")

        df = get_latest_courses_df()
        year, semester = _resolve_semester(df, year, semester)
        if year is None or semester is None:
            raise HTTPException(status_code=400, detail="無法判斷查詢學期，請指定 year 與 semester")

        results = vacancy_client.fetch_vacancy(
            BASE_URL, year, semester, code_list, timeout=min(REQUEST_TIMEOUT, 30)
        )
        return {
            "year": year,
            "semester": semester,
            "results": results,
            "truncated": len(code_list) > vacancy_client.MAX_CODES_PER_REQUEST,
            "max_codes": vacancy_client.MAX_CODES_PER_REQUEST,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"查詢缺額失敗: {e}")
        raise HTTPException(status_code=500, detail="查詢缺額失敗")

@app.get("/api/semesters")
async def get_semesters():
    """資料中可用的學期清單（新到舊）。

    has_results=False 代表該學期仍在預選登記中（選上人數尚未公布），
    前端據此標示「預選登記中」並將該學期排除於中籤率/飽和度統計之外。
    """
    try:
        df = get_latest_courses_df()
        if df is None or df.empty or not {'學年度', '學期'}.issubset(df.columns):
            return {"semesters": []}

        settled = get_settled_semester_set()
        counts = df.groupby(['學年度', '學期']).size()

        semesters = []
        for (year, semester), count in counts.items():
            try:
                year, semester = int(year), int(semester)
            except (TypeError, ValueError):
                continue
            semesters.append({
                "year": year,
                "semester": semester,
                "has_results": (year, semester) in settled,
                "course_count": int(count),
            })

        semesters.sort(key=lambda s: (s["year"], s["semester"]), reverse=True)
        return {"semesters": semesters}
    except Exception as e:
        logging.error(f"獲取學期列表失敗: {e}")
        raise HTTPException(status_code=500, detail="獲取學期列表失敗")

@app.get("/api/courses/{course_id}")
async def get_course_detail(course_id: str):
    try:
        df = get_latest_courses_df()
        if df is None or df.empty: raise HTTPException(404)
        course = df[df['課程代碼'].astype(str) == str(course_id)]
        if course.empty: raise HTTPException(404)
        return clean_single_course(course.iloc[0].to_dict())
    except HTTPException: raise
    except Exception: raise HTTPException(500)

@app.get("/api/departments")
async def get_departments(year: Optional[int] = None, semester: Optional[int] = None):
    try:
        if year and semester: df = get_courses_by_semester(year, semester)
        else: df = get_latest_courses_df()
        if df is None or df.empty or '開課班別(代表)' not in df.columns: return {"departments": []}
        departments = df['開課班別(代表)'].dropna().unique().tolist()
        departments = [d for d in departments if d and str(d).strip()]
        departments.sort()
        return {"departments": departments}
    except: raise HTTPException(500)

# ============================================================================
# AI 功能（本機版）：中籤預測說明、選課助理、教學大綱搜尋與問答
#
# 正式網站是 Cloudflare Pages 全靜態部署，沒有這些端點——中籤預測在建置期寫進
# 靜態資料包，選課助理與大綱問答需要本機的 Ollama，只在 python main.py api 時可用。
# 前端先打 /api/ai/status，拿不到就把這兩個功能標成「本機版限定」。
# ============================================================================

_syllabus_indexes: Dict[Tuple[int, int], Any] = {}


def get_syllabus_index(year: int, semester: int):
    """教學大綱向量索引（python main.py build-index 產生）；沒有就回傳 None"""
    key = (int(year), int(semester))
    if key not in _syllabus_indexes:
        try:
            from ai.index import SyllabusIndex
            _syllabus_indexes[key] = SyllabusIndex.load(*key)
        except Exception as e:
            logging.info(f"{key[0]}-{key[1]} 沒有可用的大綱索引：{e}")
            _syllabus_indexes[key] = None
    return _syllabus_indexes[key]


@app.on_event("startup")
def warm_up_syllabus_index():
    """啟動時在背景載入最新學期的大綱索引（需數秒），避免第一個查詢卡住"""
    import threading

    def _load():
        df = get_latest_courses_df()
        year, semester = _resolve_semester(df, None, None)
        if year is not None:
            get_syllabus_index(year, semester)
    threading.Thread(target=_load, daemon=True).start()


@app.get("/api/ai/status")
def ai_status():
    """前端用來判斷 AI 功能是否可用（靜態網站上這支不存在）"""
    from ai.llm import get_client

    df = get_latest_courses_df()
    year, semester = _resolve_semester(df, None, None)
    client = get_client()
    index = get_syllabus_index(year, semester) if year is not None else None
    return {
        "available": True,
        "provider": getattr(client, "name", "unknown"),
        "llm_ready": not getattr(client, "name", "").startswith("mock"),
        "syllabus_index": index.info if index is not None else None,
        "semester": f"{year}-{semester}" if year is not None else None,
    }


@app.get("/api/predict/model-info")
async def get_prediction_model_info():
    """中籤預測模型的版本、訓練資料與驗證結果（開發折＋held-out）"""
    meta = prediction_store.meta()
    if not meta:
        raise HTTPException(status_code=404, detail="尚未訓練中籤預測模型")
    keys = ["model_version", "trained_at", "data_file", "frozen_train_semesters", "holdout_semester",
            "dev_test_semesters", "dev_summary", "holdout"]
    return {k: meta.get(k) for k in keys}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    current_courses: List[Dict[str, Any]] = []
    year: Optional[int] = None
    semester: Optional[int] = None
    provider: Optional[str] = None  # "mock" 可強制使用離線規則模式


@app.post("/api/ai/chat")
def ai_chat(request: ChatRequest):
    """選課助理：自然語言需求 → LLM 呼叫工具查課 → 只回傳目標學期確實存在的課"""
    from ai.agent import run_agent
    from ai.llm import get_client
    from ai.tools import CourseContext, course_key

    full_df = get_latest_courses_df()
    if full_df is None or full_df.empty:
        raise HTTPException(status_code=404, detail="沒有處理過的課程數據")
    if not request.messages or request.messages[-1].role != "user":
        raise HTTPException(status_code=400, detail="最後一則訊息必須是使用者的問題")

    year, semester = _resolve_semester(full_df, request.year, request.semester)
    sem_df = course_filters.filter_by_semester(full_df, year, semester)
    by_key = {course_key(r): r for r in sem_df.to_dict('records')}
    # 前端傳來的現有課表以資料集中的版本為準；別學期的課（例如 localStorage 舊資料）照原樣使用
    current = [by_key.get(course_key(c), c) for c in request.current_courses]

    raw_df = get_raw_courses_df()
    ctx = CourseContext(
        semester_df=sem_df, history_df=full_df, year=int(year), semester=int(semester),
        current_courses=current,
        predict=lambda c: prediction_store.get(c, raw_df),
        syllabus_index=get_syllabus_index(year, semester),
    )
    try:
        result = run_agent([m.model_dump() for m in request.messages], ctx, get_client(request.provider))
    except Exception as e:
        logging.error(f"選課助理錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"選課助理發生錯誤: {e}")

    courses = clean_course_data(result["courses"])
    prediction_store.attach(courses, raw_df)
    result["courses"] = courses
    result["semester"] = f"{year}-{semester}"
    return result


@app.get("/api/ai/syllabus-search")
def syllabus_search(q: str, k: int = 8, year: Optional[int] = None, semester: Optional[int] = None):
    """用自然語言搜尋教學大綱內容，回傳相關課程與命中的段落"""
    from ai.tools import course_key

    full_df = get_latest_courses_df()
    if full_df is None or full_df.empty:
        raise HTTPException(status_code=404, detail="沒有處理過的課程數據")
    year, semester = _resolve_semester(full_df, year, semester)
    index = get_syllabus_index(year, semester)
    if index is None:
        return {"available": False, "hits": [],
                "detail": "尚未建立這學期的大綱索引（python main.py fetch-syllabi && python main.py build-index）"}
    sem_df = course_filters.filter_by_semester(full_df, year, semester)
    by_key = {course_key(r): r for r in sem_df.to_dict('records')}
    hits = []
    for h in index.search(q, k=max(1, min(k, 30))):
        c = by_key.get(course_key({"code": h["code"], "serial": h["serial"]}))
        if c is not None:
            hits.append({**c, "match_section": h["section"], "match_text": h["text"],
                         "match_score": round(float(h["score"]), 4)})
    hits = clean_course_data(hits)
    prediction_store.attach(hits, get_raw_courses_df())
    return {"available": True, "hits": hits, "index": index.info}


class SyllabusQARequest(BaseModel):
    code: str
    serial: str
    question: str
    year: Optional[int] = None
    semester: Optional[int] = None


@app.post("/api/ai/syllabus-qa")
def syllabus_qa(request: SyllabusQARequest):
    """針對單一課程的大綱問答，回答附上引用的原文段落"""
    from ai.index import answer_from_syllabus
    from ai.llm import get_client

    full_df = get_latest_courses_df()
    if full_df is None or full_df.empty:
        raise HTTPException(status_code=404, detail="沒有處理過的課程數據")
    year, semester = _resolve_semester(full_df, request.year, request.semester)
    index = get_syllabus_index(year, semester)
    if index is None:
        raise HTTPException(status_code=404, detail="尚未建立這學期的大綱索引")
    return answer_from_syllabus(index, request.code, request.serial, request.question, get_client())


def main():
    setup_logging()
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)

if __name__ == "__main__":
    main()