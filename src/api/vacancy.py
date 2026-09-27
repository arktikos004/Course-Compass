"""即時缺額查詢 —— 直接向學校站台問，不看已爬好的 CSV。

缺額監控原本只存在於 GitHub Actions + LINE 推播（ncue-course-monitor/），
網頁上看不到任何東西。這裡把同一份查詢邏輯（src/crawler/ncue_client.py）接上 API，
讓網頁也能查目前名額。

刻意不落地成檔案：缺額是會秒變的資料，存下來只會給出過期答案。改用短時記憶體
快取擋住重複點擊，同時避免對學校站台造成不必要的流量。
"""

import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from crawler.ncue_client import (
    TimeoutSpec, build_session, fetch_course_table, find_column_index, parse_course_table,
)

logger = logging.getLogger(__name__)

# 單次請求最多查幾門課：每門課都是一次對外請求，過多會拖垮回應時間也不禮貌
MAX_CODES_PER_REQUEST = 12

# 快取秒數：選課尖峰時名額變動快，但重複點擊同一顆按鈕不該每次都打站台
CACHE_TTL_SECONDS = 45

_cache: Dict[str, Dict[str, Any]] = {}


def _to_int(value: Any) -> int:
    text = str(value).strip()
    return int(text) if text.isdigit() else 0


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _cache.get(key)
    if not entry:
        return None
    if time.time() - entry["at"] > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return entry["data"]


def _cache_put(key: str, data: Dict[str, Any]) -> None:
    _cache[key] = {"at": time.time(), "data": data}


def fetch_vacancy(
    base_url: str,
    year: Any,
    semester: Any,
    codes: Sequence[str],
    *,
    timeout: TimeoutSpec = 30,
) -> List[Dict[str, Any]]:
    """查詢指定課程代碼目前的登記/上限，回傳每個開課序號一筆。"""
    unique_codes = []
    for code in codes:
        code = str(code).strip()
        if code and code not in unique_codes:
            unique_codes.append(code)
    unique_codes = unique_codes[:MAX_CODES_PER_REQUEST]

    if not unique_codes:
        return []

    results: List[Dict[str, Any]] = []
    session = None

    for code in unique_codes:
        cache_key = f"{year}-{semester}-{code}"
        cached = _cache_get(cache_key)
        if cached is not None:
            results.extend(cached["rows"])
            continue

        if session is None:
            # 一次最多 12 門課、每門 2 次請求，重試次數要比監控保守，
            # 否則站台掛掉時這支 API 會讓瀏覽器卡上好幾分鐘。
            session = build_session(retries=1)

        rows_for_code: List[Dict[str, Any]] = []
        try:
            table = fetch_course_table(
                session, base_url, year, semester,
                scr_selcode=code, html_parser="html.parser", timeout=timeout,
            )
        except Exception as exc:  # 對外請求失敗不該讓整批查詢一起垮掉
            logger.warning("查詢課程 %s 缺額失敗: %s", code, exc)
            results.append({
                "課程代碼": code, "error": "查詢失敗，請稍後再試", "rows": None,
            })
            continue

        if table is None:
            results.append({
                "課程代碼": code, "error": "查無此課程（代碼有誤或該學期未開放）",
            })
            continue

        headers, rows = parse_course_table(table)
        idx_limit = find_column_index(headers, "上限人數")
        idx_registered = find_column_index(headers, "登記人數")
        if idx_limit is None or idx_registered is None:
            logger.error("站台表格缺少人數欄位，可能又改版了: %s", headers)
            results.append({"課程代碼": code, "error": "站台格式改變，暫時無法解析"})
            continue

        for row in rows:
            if str(row.get("課程代碼", "")).strip() != code:
                continue
            limit = _to_int(row.get("上限人數"))
            registered = _to_int(row.get("登記人數"))
            rows_for_code.append({
                "課程代碼": code,
                "序號": str(row.get("序號", "")).strip(),
                "課程名稱": str(row.get("課程名稱", "")).strip(),
                "教師姓名": str(row.get("教師姓名", "")).strip(),
                "上限人數": limit,
                "登記人數": registered,
                "剩餘名額": max(0, limit - registered),
                "has_vacancy": limit > registered,
            })

        if rows_for_code:
            _cache_put(cache_key, {"rows": rows_for_code})
            results.extend(rows_for_code)
        else:
            results.append({"課程代碼": code, "error": "查無此課程的開課資料"})

    return results
