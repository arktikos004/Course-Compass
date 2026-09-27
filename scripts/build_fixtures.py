#!/usr/bin/env python3
"""產生查詢對照樣本，作為 Python → JS 移植的驗收基準。

把篩選、評分、排序改寫成 JS 時，最大的風險是行為悄悄飄移：邊界條件、
排序穩定性、NaN 處理稍有不同，使用者看到的結果就不一樣，而且很難察覺。

這支腳本用現有的 Python 實作（線上驗證過的那一份）跑一組涵蓋各種條件組合的查詢，
把「輸入參數 → 回傳的課程順序與分數」存下來。JS 引擎必須逐筆重現同樣的結果。

用法：
    python scripts/build_fixtures.py            # 輸出到 tests/fixtures/query_cases.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from api.app import CourseQueryRequest, _query_courses  # noqa: E402

YEAR, SEMESTER = 115, 1

# 已選課程：用來觸發衝堂、剩餘學分、排除已選這幾條路徑
SELECTED = [{"code": "00254", "serial": "1747"}]     # 工程與生活，週一 3-4 節

# 全空堂扣掉週一 3-4 節，用來驗證空堂過濾要求「整段落在空堂內」
EMPTY_SLOTS = [{"day": d, "period": p}
               for d in range(1, 8) for p in range(1, 13)
               if not (d == 1 and p in (3, 4))]

CASES = [
    ("預設查全部", {}),
    ("關鍵字-課名", {"keyword": "英文"}),
    ("關鍵字-教師", {"keyword": "邱湘雲"}),
    ("關鍵字-課號", {"keyword": "00254"}),
    ("關鍵字-英文課名", {"keyword": "Python"}),
    ("關鍵字無結果", {"keyword": "zzzz不存在"}),

    ("分類-核心通識", {"category": "核心通識"}),
    ("分類-教育學程", {"category": "教育學程"}),

    ("學制-大學部", {"level": "大學部"}),
    ("學制-碩士班", {"level": "碩士班"}),
    ("學制-研究所", {"level": "研究所"}),

    ("通識-跨學院", {"general_group": "跨學院通識"}),
    ("通識-跨學院子類", {"general_group": "跨學院通識", "general_subs": ["文", "理"]}),
    ("通識-素養", {"general_group": "素養通識"}),
    ("通識-校訂必修", {"general_group": "校訂必修通識"}),

    ("學院", {"college": "工學院"}),
    ("學院+系所", {"college": "工學院", "department": "資訊工程學系"}),
    ("年級", {"grade": "2"}),
    ("日夜間部", {"division": "夜間部"}),

    ("星期單日", {"preferred_days": ["2"]}),
    ("星期多日", {"preferred_days": ["1", "3", "5"]}),
    ("星期中文", {"preferred_days": ["二"]}),

    ("學分下限", {"min_credits": 3}),
    ("學分上限", {"max_credits": 2}),
    ("學分區間", {"min_credits": 2, "max_credits": 3}),

    ("只看有名額", {"has_vacancy": True}),
    ("只看全英語", {"english_only": True}),

    ("排除本院通識", {"category": "核心通識", "exclude_college": "文學院"}),
    ("排除本院通識-理", {"general_group": "跨學院通識", "exclude_college": "理學院"}),

    ("課表-排除已選", {"current_courses": SELECTED}),
    ("課表-衝堂標記", {"category": "核心通識", "current_courses": SELECTED}),
    ("課表-排除衝堂", {"category": "核心通識", "current_courses": SELECTED,
                  "exclude_conflicts": True}),
    ("課表-空堂過濾", {"category": "核心通識", "empty_slots": EMPTY_SLOTS}),
    ("課表-目標學分足", {"category": "核心通識", "current_courses": SELECTED,
                   "target_credits": 20}),
    ("課表-目標學分不足", {"category": "核心通識", "current_courses": SELECTED,
                    "target_credits": 3}),
    ("課表-目標學分已超過", {"category": "核心通識", "current_courses": SELECTED,
                     "target_credits": 1}),

    ("排序-分數", {"category": "核心通識", "sort": "score"}),
    ("排序-相關度", {"keyword": "英文", "sort": "relevance"}),
    ("排序-剩餘名額", {"category": "核心通識", "sort": "vacancy"}),
    ("排序-最好選上", {"category": "核心通識", "sort": "acceptance"}),
    ("排序-最搶手", {"category": "核心通識", "sort": "competitive"}),
    ("排序-登記人數", {"category": "核心通識", "sort": "registered"}),
    ("排序-學分", {"category": "核心通識", "sort": "credits"}),
    ("排序-課名", {"category": "核心通識", "sort": "name"}),

    ("分頁-第2頁", {"category": "核心通識", "limit": 10, "offset": 10}),
    ("分頁-尾頁", {"category": "核心通識", "limit": 10, "offset": 50}),

    ("組合-關鍵字+學制", {"keyword": "文化", "level": "大學部"}),
    ("組合-通識+星期+有名額", {"general_group": "素養通識", "preferred_days": ["2", "4"],
                       "has_vacancy": True}),
    ("組合-全條件", {"keyword": "生活", "level": "大學部", "division": "日間部",
                 "min_credits": 2, "preferred_days": ["1", "2", "3", "4", "5"],
                 "current_courses": SELECTED, "target_credits": 20,
                 "sort": "score", "limit": 20}),
]


def main() -> None:
    out = ROOT / "tests" / "fixtures" / "query_cases.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    cases = []
    for name, params in CASES:
        request = CourseQueryRequest(year=YEAR, semester=SEMESTER,
                                     **{**{"limit": 50}, **params})
        result = _query_courses(request)
        cases.append({
            "name": name,
            "params": {"year": YEAR, "semester": SEMESTER, "limit": 50, **params},
            "expected": {
                "total": result["total"],
                "has_more": result["has_more"],
                # 只比對能唯一識別課程的欄位與計算結果，避免把整份課程資料存進樣本
                "courses": [
                    {
                        "code": c["課程代碼"],
                        "serial": str(c["序號"]),
                        "score": c["recommend_score"],
                        "chance": c["score_detail"]["chance"],
                        "chance_source": c["score_detail"]["chance_source"],
                        "vacancy_seats": c["score_detail"]["vacancy_seats"],
                        "credit_fit": c["score_detail"]["credit_fit"],
                        "conflict": c["has_conflict"],
                        "rate": c["historical_acceptance_rate"],
                    }
                    for c in result["courses"]
                ],
            },
        })
        print(f"  {name:<22} total={result['total']:>5}  回傳 {len(result['courses'])} 筆")

    out.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n共 {len(cases)} 組樣本 → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
