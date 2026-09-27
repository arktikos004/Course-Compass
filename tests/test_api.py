"""後端邏輯的自動測試。

用標準庫 unittest，不新增依賴：
    python -m unittest discover -s tests -v

多數測試用手工造的小資料集，不依賴 data/processed 下的實際檔案，
這樣在還沒跑過爬蟲的環境也能執行。少數標記為 smoke 的測試會在
真實資料存在時才跑，用來確認整條路徑接得起來。

測試涵蓋這次開發過程中實際踩到的每一個 bug，避免再犯。
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from api import dashboard, filters, search  # noqa: E402
from api.recommender import (  # noqa: E402
    occupied_slots, rank_courses, score_course, selected_credits,
)


def make_courses(rows):
    """把精簡的 tuple 展開成完整欄位的 DataFrame"""
    columns = [
        "學年度", "學期", "課程代碼", "序號", "課程名稱", "英文課程名稱", "教師姓名",
        "開課班別(代表)", "學院", "科系", "年級", "學制", "部別", "課程性質",
        "學分", "星期", "起始節次", "結束節次", "上限人數", "登記人數", "選上人數",
        "全英語授課",
    ]
    return pd.DataFrame(rows, columns=columns)


# 一份涵蓋各種情境的樣本：跨學院通識、素養通識、校訂必修通識、系所課、研究所課
SAMPLE = make_courses([
    (115, 1, "A001", 1, "客家文化", "Hakka Culture", "邱湘雲", "核心通識", "通識教育中心",
     "通識教育中心", None, "日間部", "大學部", "跨學院通識(文)", 2.0, "一", 3, 4, 70, 86, 70, False),
    (115, 1, "A002", 2, "工程與生活", "Engineering", "伍朝欽", "核心通識", "通識教育中心",
     "通識教育中心", None, "日間部", "大學部", "跨學院通識(工)", 2.0, "一", 3, 4, 50, 70, 50, False),
    (115, 1, "A003", 3, "Python程式設計", "Python", "阮家慶", "核心通識", "通識教育中心",
     "通識教育中心", None, "日間部", "大學部", "素養通識-生活藝能及應用", 2.0, "二", 5, 6, 50, 30, 30, False),
    (115, 1, "B001", 4, "大一國文", "Chinese", "王老師", "電子一", "工學院",
     "電子工程系", "1", "日間部", "大學部", "校必(通識)", 3.0, "三", 1, 2, 60, 40, 40, False),
    (115, 1, "C001", 5, "資料結構", "Data Structure", "李老師", "資工二", "工學院",
     "資訊工程系", "2", "日間部", "大學部", "系必修", 3.0, "四", 3, 4, 60, 55, 55, True),
    (115, 1, "D001", 6, "高等演算法", "Adv Algorithm", "陳老師", "資工碩一", "工學院",
     "資訊工程系", "碩士", "日間部", "碩士班", "系選修", 3.0, "五", 6, 7, 20, 5, 5, False),
    (115, 1, "D002", 7, "論文", "Thesis", "論文", "資工博一", "工學院",
     "資訊工程系", "博士", "日間部", "博士班", "系必修", 0.0, None, None, None, 30, 1, 1, False),
])


class TestWeekdayAndGeneral(unittest.TestCase):
    def test_normalize_weekday_accepts_both_forms(self):
        self.assertEqual(filters.normalize_weekday("三"), 3)
        self.assertEqual(filters.normalize_weekday("3"), 3)
        self.assertEqual(filters.normalize_weekday(3), 3)

    def test_normalize_weekday_rejects_junk(self):
        for bad in (None, "", "nan", "八", float("nan")):
            self.assertIsNone(filters.normalize_weekday(bad))

    def test_classify_general_covers_three_groups(self):
        self.assertEqual(filters.classify_general("跨學院通識(文)"), ("跨學院通識", "文"))
        self.assertEqual(filters.classify_general("素養通識-生活藝能及應用"),
                         ("素養通識", "生活藝能及應用"))
        self.assertEqual(filters.classify_general("校必(通識)"), ("校訂必修通識", ""))

    def test_classify_general_ignores_non_general(self):
        self.assertEqual(filters.classify_general("系必修"), (None, None))


class TestApplyFilters(unittest.TestCase):
    def test_level_graduate_is_master_plus_phd(self):
        master = filters.apply_filters(SAMPLE, level="碩士班")
        phd = filters.apply_filters(SAMPLE, level="博士班")
        graduate = filters.apply_filters(SAMPLE, level=filters.LEVEL_GRADUATE)
        self.assertEqual(len(graduate), len(master) + len(phd))

    def test_general_group_includes_required_general(self):
        """校訂必修通識曾經查不到——它掛在各系班級下，開課班別不是核心通識"""
        result = filters.apply_filters(SAMPLE, general_group="校訂必修通識")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["課程名稱"], "大一國文")

    def test_general_subs_narrows_within_group(self):
        both = filters.apply_filters(SAMPLE, general_group="跨學院通識")
        only_arts = filters.apply_filters(SAMPLE, general_group="跨學院通識", general_subs=["文"])
        self.assertEqual(len(both), 2)
        self.assertEqual(len(only_arts), 1)

    def test_days_filter_normalizes_numeric_input(self):
        """資料存中文星期，前端送數字，兩邊都要能比對"""
        self.assertEqual(len(filters.apply_filters(SAMPLE, days=["1"])), 2)
        self.assertEqual(len(filters.apply_filters(SAMPLE, days=["一"])), 2)

    def test_credit_range(self):
        self.assertEqual(len(filters.apply_filters(SAMPLE, min_credits=3)), 3)
        self.assertEqual(len(filters.apply_filters(SAMPLE, max_credits=2)), 4)

    def test_has_vacancy_uses_registration_not_enrollment(self):
        result = filters.apply_filters(SAMPLE, has_vacancy=True)
        for _, row in result.iterrows():
            self.assertGreater(row["上限人數"], row["登記人數"])

    def test_keyword_matches_across_fields(self):
        self.assertEqual(len(filters.apply_filters(SAMPLE, keyword="客家")), 1)      # 課名
        self.assertEqual(len(filters.apply_filters(SAMPLE, keyword="阮家慶")), 1)    # 教師
        self.assertEqual(len(filters.apply_filters(SAMPLE, keyword="Python")), 1)   # 英文課名

    def test_exclude_courses_removes_selected(self):
        result = filters.apply_filters(SAMPLE, exclude_courses=[{"code": "A001", "serial": "1"}])
        self.assertNotIn("A001", set(result["課程代碼"]))
        self.assertEqual(len(result), len(SAMPLE) - 1)


class TestExcludeOwnCollegeGeneral(unittest.TestCase):
    """跨學院通識「不能選本院」要看課程性質後綴，不能比對學院欄位。

    所有核心通識課的學院都是「通識教育中心」，用學院比對永遠不會相等，
    這條規則會默默失效——這是實際發生過的 bug。
    """

    def test_excludes_matching_domain(self):
        result = filters.exclude_own_college_general(SAMPLE, "工學院")
        natures = set(result["課程性質"])
        self.assertNotIn("跨學院通識(工)", natures)
        self.assertIn("跨學院通識(文)", natures)

    def test_college_column_is_not_the_signal(self):
        """被排除的課，其學院欄位其實是通識教育中心而非工學院"""
        engineering_general = SAMPLE[SAMPLE["課程性質"] == "跨學院通識(工)"]
        self.assertEqual(engineering_general.iloc[0]["學院"], "通識教育中心")

    def test_unknown_college_is_noop(self):
        self.assertEqual(len(filters.exclude_own_college_general(SAMPLE, "不存在學院")), len(SAMPLE))


class TestFilterOptions(unittest.TestCase):
    def test_colleges_exclude_non_college_units(self):
        """學院選單若混入通識教育中心，使用者選了學院就再也查不到通識課"""
        options = filters.build_filter_options(SAMPLE)
        names = [c["name"] for c in options["colleges"]]
        self.assertIn("工學院", names)
        self.assertNotIn("通識教育中心", names)

    def test_levels_include_graduate_aggregate(self):
        options = filters.build_filter_options(SAMPLE)
        levels = {level["name"]: level["course_count"] for level in options["levels"]}
        self.assertEqual(levels[filters.LEVEL_GRADUATE],
                         levels.get("碩士班", 0) + levels.get("博士班", 0))


class TestRecommenderScoring(unittest.TestCase):
    BASE = {"上限人數": 50, "登記人數": 30, "學分": 2.0, "星期": "一", "起始節次": 3, "結束節次": 4}

    def score(self, remaining=None, busy=None, **overrides):
        course = {**self.BASE, **overrides}
        return score_course(course, historical_rate=None, semester_settled=True,
                            remaining_credits=remaining, busy_slots=busy or set())

    def test_credit_fit_is_monotonic(self):
        """剩餘 -1 學分曾經比剩餘 1 學分拿到更高分：<=0 被誤當成「沒有限制」"""
        over = self.score(remaining=-1)["credit_fit"]
        exact = self.score(remaining=0)["credit_fit"]
        tight = self.score(remaining=1)["credit_fit"]     # 2 學分塞不進 1
        enough = self.score(remaining=5)["credit_fit"]
        self.assertEqual([over, exact, tight], [0.0, 0.0, 0.0])
        self.assertEqual(enough, 1.0)

    def test_no_target_means_no_constraint(self):
        self.assertEqual(self.score(remaining=None)["credit_fit"], 1.0)

    def test_conflict_lowers_score_but_keeps_course(self):
        clean = self.score()
        clashed = self.score(busy={(1, 3)})
        self.assertTrue(clashed["has_conflict"])
        self.assertLess(clashed["score"], clean["score"])

    def test_chance_source_is_reported(self):
        course = {**self.BASE}
        with_history = score_course(course, historical_rate=0.8, semester_settled=True,
                                    remaining_credits=None, busy_slots=set())
        self.assertEqual(with_history["chance_source"], "歷年中籤率")
        self.assertAlmostEqual(with_history["chance"], 0.8)

    def test_unsettled_semester_does_not_guess_from_registration(self):
        """預選中的學期登記數還在累積，拿來推估會過度樂觀"""
        detail = score_course({**self.BASE}, historical_rate=None, semester_settled=False,
                              remaining_credits=None, busy_slots=set())
        self.assertEqual(detail["chance_source"], "無足夠資料")


class TestRecommenderSchedule(unittest.TestCase):
    def test_occupied_slots_expands_multi_period_course(self):
        slots = occupied_slots(SAMPLE, [{"code": "A001", "serial": "1"}])
        self.assertEqual(slots, {(1, 3), (1, 4)})

    def test_selected_credits_reads_from_dataset(self):
        """前端只送課號，學分要回資料表查"""
        self.assertEqual(selected_credits(SAMPLE, [{"code": "C001", "serial": "5"}]), 3.0)

    def test_rank_orders_by_score_then_registration(self):
        courses = SAMPLE.to_dict("records")
        ranked = rank_courses(courses, full_df=SAMPLE, stats_map={}, semester_settled=True,
                              target_credits=None, current_courses=[], exclude_conflicts=False)
        scores = [c["recommend_score"] for c in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_exclude_conflicts_removes_exactly_the_clashing_ones(self):
        courses = SAMPLE.to_dict("records")
        selected = [{"code": "A001", "serial": "1"}]           # 週一 3-4 節
        kept = rank_courses(courses, full_df=SAMPLE, stats_map={}, semester_settled=True,
                            target_credits=None, current_courses=selected, exclude_conflicts=False)
        pruned = rank_courses(courses, full_df=SAMPLE, stats_map={}, semester_settled=True,
                              target_credits=None, current_courses=selected, exclude_conflicts=True)
        clashing = [c for c in kept if c["has_conflict"]]
        self.assertTrue(clashing)
        self.assertEqual(len(pruned), len(kept) - len(clashing))


class TestSearchRanking(unittest.TestCase):
    def test_exact_name_beats_partial(self):
        exact = {"課程名稱": "英文"}
        partial = {"課程名稱": "大學英文閱讀"}
        self.assertGreater(search.relevance(exact, "英文"), search.relevance(partial, "英文"))

    def test_paginate_reports_has_more(self):
        page, meta = search.paginate(list(range(100)), limit=30, offset=0)
        self.assertEqual(len(page), 30)
        self.assertEqual(meta["total"], 100)
        self.assertTrue(meta["has_more"])

        last, meta = search.paginate(list(range(100)), limit=30, offset=90)
        self.assertEqual(len(last), 10)
        self.assertFalse(meta["has_more"])

    def test_competitive_sort_puts_hardest_first_and_unknown_last(self):
        courses = [
            {"課程名稱": "甲", "historical_acceptance_rate": 0.9},
            {"課程名稱": "乙", "historical_acceptance_rate": 0.2},
            {"課程名稱": "丙", "historical_acceptance_rate": None},
        ]
        ranked = search.rank_results(courses, None, sort="competitive")
        self.assertEqual([c["課程名稱"] for c in ranked], ["乙", "甲", "丙"])

    def test_acceptance_sort_is_the_reverse_direction(self):
        courses = [
            {"課程名稱": "甲", "historical_acceptance_rate": 0.9},
            {"課程名稱": "乙", "historical_acceptance_rate": 0.2},
        ]
        ranked = search.rank_results(courses, None, sort="acceptance")
        self.assertEqual([c["課程名稱"] for c in ranked], ["甲", "乙"])

    def test_registered_sort(self):
        courses = [{"課程名稱": "甲", "登記人數": 10}, {"課程名稱": "乙", "登記人數": 99}]
        ranked = search.rank_results(courses, None, sort="registered")
        self.assertEqual([c["課程名稱"] for c in ranked], ["乙", "甲"])


class TestDashboard(unittest.TestCase):
    SETTLED = {(115, 1)}

    def test_ranking_covers_core_general_only(self):
        """排行榜排除校訂必修通識與系所課——必修沒有選擇權，談不上搶手"""
        rows = dashboard._ratio_ranking(SAMPLE, self.SETTLED, ascending=False)
        names = {r["課程名稱"] for r in rows}
        self.assertLessEqual(names, {"客家文化", "工程與生活", "Python程式設計"})
        self.assertNotIn("大一國文", names)
        self.assertNotIn("資料結構", names)

    def test_teacher_ranking_drops_non_person_values(self):
        """原始資料把論文類課程的教師欄位填成「論文」，會霸佔開課數第一名"""
        stats = dashboard.teacher_stats(SAMPLE, SAMPLE, self.SETTLED)
        self.assertNotIn("論文", {t["name"] for t in stats["most_courses"]})

    def test_college_saturation_skips_non_college_units(self):
        rows = dashboard.college_saturation(SAMPLE, self.SETTLED)
        names = {r["name"] for r in rows}
        self.assertIn("工學院", names)
        self.assertNotIn("通識教育中心", names)

    def test_period_acceptance_uses_saturation_so_it_has_spread(self):
        """改用飽和度前，各時段中籤率都落在 0.88~1.00，整張圖看不出差異"""
        grid = dashboard.period_acceptance(SAMPLE, self.SETTLED)
        values = [v for row in grid["matrix"] for v in row if v is not None]
        self.assertTrue(values)
        self.assertGreater(max(values), 1.0)   # 飽和度不設上限，超額登記會 >100%

    def test_general_competition_reports_每領域(self):
        rows = dashboard.general_domain_competition(SAMPLE, self.SETTLED)
        self.assertTrue(rows)
        self.assertEqual(rows, sorted(rows, key=lambda r: -r["avg_saturation"]))


class TestSettledSemesters(unittest.TestCase):
    def test_pending_semester_is_excluded(self):
        from api.app import get_settled_semesters

        pending = SAMPLE.copy()
        pending["學期"] = 2
        pending["選上人數"] = 0          # 尚未分發
        combined = pd.concat([SAMPLE, pending], ignore_index=True)

        settled = get_settled_semesters(combined)
        self.assertIn((115, 1), settled)
        self.assertNotIn((115, 2), settled)


class TestQueryEndpoint(unittest.TestCase):
    """/api/courses/query 的整合行為（搜尋與推薦共用同一個實作）"""

    def setUp(self):
        from api import app as app_module

        self.app_module = app_module
        self._original = app_module.get_latest_courses_df
        app_module.get_latest_courses_df = lambda: SAMPLE
        app_module._stats_cache.clear()

    def tearDown(self):
        self.app_module.get_latest_courses_df = self._original
        self.app_module._stats_cache.clear()

    def query(self, **kwargs):
        request = self.app_module.CourseQueryRequest(year=115, semester=1, **kwargs)
        return self.app_module._query_courses(request)

    def test_total_reflects_all_matches_not_page_size(self):
        """舊版先 head(50) 再讓前端過濾，符合條件的課會在截斷時就被丟掉"""
        result = self.query(limit=2)
        self.assertEqual(result["total"], len(SAMPLE))
        self.assertEqual(len(result["courses"]), 2)
        self.assertTrue(result["has_more"])

    def test_pagination_does_not_repeat_rows(self):
        first = self.query(limit=3, offset=0)["courses"]
        second = self.query(limit=3, offset=3)["courses"]
        codes = [c["課程代碼"] for c in first] + [c["課程代碼"] for c in second]
        self.assertEqual(len(codes), len(set(codes)))

    def test_every_result_carries_score_regardless_of_sort(self):
        for sort in ("score", "name", "credits", "vacancy", "competitive", "registered"):
            with self.subTest(sort=sort):
                for course in self.query(sort=sort, limit=5)["courses"]:
                    self.assertIsNotNone(course.get("recommend_score"))
                    self.assertIn("score_detail", course)

    def test_keyword_and_filter_combine(self):
        """合併搜尋與推薦的重點：關鍵字與條件要能同時生效"""
        result = self.query(keyword="Python", level="大學部")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["courses"][0]["課程名稱"], "Python程式設計")

    def test_empty_slots_filter_requires_full_coverage(self):
        """整段上課時間都要落在空堂內，跨節課不能只有一半空著"""
        slots = [{"day": 1, "period": 3}]          # 只空週一第 3 節
        result = self.query(empty_slots=slots)
        for course in result["courses"]:
            self.assertNotEqual(course["課程代碼"], "A001")   # 需要 3~4 節

    def test_exclude_college_applies_general_domain_rule(self):
        without = self.query()["total"]
        with_rule = self.query(exclude_college="工學院")["total"]
        self.assertEqual(with_rule, without - 1)             # 少掉跨學院通識(工)


class TestRealDatasetSmoke(unittest.TestCase):
    """有實際資料時才跑：確認整條路徑接得起來"""

    @classmethod
    def setUpClass(cls):
        from config import PROCESSED_DATA_DIR

        if not sorted(Path(PROCESSED_DATA_DIR).glob("all_courses_*.csv")):
            raise unittest.SkipTest("尚未產生處理後的資料檔，略過")

    def test_loader_is_not_recursive(self):
        """曾經把別名函式改名成自己，導致每次呼叫都無限遞迴——import 檢查抓不到"""
        from api.app import get_latest_courses_df

        df = get_latest_courses_df()
        self.assertIsNotNone(df)
        self.assertGreater(len(df), 0)

    def test_dashboard_builds(self):
        import asyncio

        from api.app import get_dashboard

        data = asyncio.new_event_loop().run_until_complete(get_dashboard())
        self.assertGreater(data["overview"]["total_courses"], 0)
        for key in ("general_competition", "period_acceptance", "teachers", "college_saturation"):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
