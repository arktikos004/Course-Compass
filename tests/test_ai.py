"""AI 功能（中籤預測、選課助理、教學大綱搜尋）的自動測試。

與 test_api.py 同一套慣例：標準庫 unittest、手工造的小資料集、不依賴 data/ 下的實際檔案。
需要 scikit-learn 的測試（模型訓練、大綱索引）在沒裝 requirements-ai.txt 時自動略過，
其餘只用 pandas，CI 會照常執行。

    python -m unittest discover -s tests -v
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from ai.agent import run_agent  # noqa: E402
from ai.llm import MockClient  # noqa: E402
from ai.parse import course_satisfies, parse_request  # noqa: E402
from ai.syllabus import chunk_text, clean_text, split_sections  # noqa: E402
from ai.tools import CourseContext, check_schedule_conflict, find_courses, run_tool  # noqa: E402
from api.filters import filter_by_semester  # noqa: E402
from api.predictions import PredictionStore, applicable  # noqa: E402
from ml.demand_features import FEATURES, build_course_table, build_features, label_to_idx  # noqa: E402

HAS_SKLEARN = importlib.util.find_spec("sklearn") is not None
SEMESTERS = ((113, 1), (113, 2), (114, 1), (114, 2))


def synthetic_history(n_courses: int = 45, seed: int = 0) -> pd.DataFrame:
    """多學期的合成資料：每門課有穩定的「熱門程度」，逐學期加上雜訊，模型才有東西可學"""
    rng = np.random.default_rng(seed)
    popularity = rng.lognormal(mean=-0.3, sigma=0.9, size=n_courses)
    rows = []
    for year, semester in SEMESTERS:
        for i in range(n_courses):
            cap = int(rng.choice([30, 50, 70]))
            reg = max(1, int(cap * popularity[i] * rng.lognormal(0, 0.3)))
            group = ("核心通識", "精進英外文", "資工二")[i % 3]
            start = 1 + (i % 8)
            rows.append({
                "學年度": year, "學期": semester, "課程代碼": f"C{i:03d}", "序號": i + 1,
                "課程名稱": f"課程{i}", "英文課程名稱": "", "教師姓名": f"老師{i % 10}",
                "開課班別(代表)": group, "學院": "工學院" if group == "資工二" else "通識教育中心",
                "科系": "資訊工程學系" if group == "資工二" else "通識教育中心", "年級": None,
                "學制": "日間部", "部別": "大學部",
                "課程性質": "跨學院通識(文)" if group == "核心通識" else "系選修", "課程性質2": "",
                "學分": 2.0 + (i % 2), "星期": "一二三四五"[i % 5], "起始節次": start, "結束節次": start + 1,
                "上課地點": "T101", "上限人數": cap, "登記人數": reg, "選上人數": min(cap, reg),
                "全英語授課": False, "可跨班": "可跨班系", "備註": "", "教學大綱連結": "",
            })
    return pd.DataFrame(rows)


HISTORY = synthetic_history()


def semester_context(year=114, semester=2, current=None) -> CourseContext:
    return CourseContext(semester_df=filter_by_semester(HISTORY, year, semester), history_df=HISTORY,
                         year=year, semester=semester, current_courses=current or [])


# ============================================================ 中籤預測：資料與防洩漏

class TestDemandFeatures(unittest.TestCase):
    def setUp(self):
        self.courses = build_course_table(HISTORY)

    def test_features_ignore_current_and_future_outcomes(self):
        """預測第 t 學期只能用選課前就知道的資訊：竄改 t 以後的登記結果，特徵必須完全不變"""
        for label in ("114-1", "114-2"):
            with self.subTest(semester=label):
                t = label_to_idx(label)
                before = build_features(self.courses, t)
                tampered = self.courses.copy()
                future = tampered["idx"] >= t
                rng = np.random.default_rng(1)
                tampered.loc[future, "登記人數"] = rng.integers(0, 500, future.sum())
                tampered.loc[future, "選上人數"] = rng.integers(0, 500, future.sum())
                cap, reg = tampered["上限人數"], tampered["登記人數"]
                tampered["y_log"] = np.where(reg > 0, np.log(reg.clip(lower=1) / cap), np.nan)
                tampered["y_full"] = np.where(reg > 0, (reg > cap).astype(float), np.nan)
                pd.testing.assert_frame_equal(before, build_features(tampered, t))

    def test_features_do_change_when_past_changes(self):
        """反向檢查：竄改過去要能改變特徵，證明上面的測試不是空轉"""
        t = label_to_idx("114-2")
        tampered = self.courses.copy()
        past = tampered["idx"] < t
        tampered.loc[past, "y_log"] += 1.0
        self.assertFalse(build_features(self.courses, t)["ct_mean"].equals(build_features(tampered, t)["ct_mean"]))

    def test_unsettled_semester_is_not_labeled(self):
        """仍在預選中的學期登記人數還在累積，不能當標籤"""
        table = build_course_table(HISTORY, settled={(113, 1), (113, 2), (114, 1)})
        self.assertFalse(table.loc[table["學年度"].eq(114) & table["學期"].eq(2), "labeled"].any())
        self.assertTrue(table.loc[table["學年度"].eq(114) & table["學期"].eq(1), "labeled"].all())

    def test_one_row_per_section_and_all_features(self):
        self.assertFalse(self.courses.duplicated(["學年度", "學期", "課程代碼", "序號"]).any())
        self.assertEqual(list(build_features(self.courses, label_to_idx("114-2")).columns), FEATURES)


@unittest.skipUnless(HAS_SKLEARN, "需要 scikit-learn（pip install -r requirements-ai.txt）")
class TestDemandModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ml.demand_model import _train_calibrated, first_train_idx

        cls.courses = build_course_table(HISTORY)
        first = first_train_idx(cls.courses)
        cls.feats = {t: build_features(cls.courses, t) for t in range(first, int(cls.courses["idx"].max()) + 1)}
        cls.t = label_to_idx("114-2")
        cls.model = _train_calibrated(cls.courses, cls.feats, list(range(first, cls.t)))

    def test_prediction_ranges(self):
        p = self.model.predict(self.feats[self.t])
        self.assertEqual(len(p), len(self.feats[self.t]))
        self.assertTrue(p["p_full"].between(0, 1).all())
        self.assertTrue(p["est_admit"].between(0, 1).all())
        self.assertTrue((p["admit_lo"] <= p["admit_hi"] + 1e-12).all())

    def test_model_roundtrips_through_joblib(self):
        import joblib

        with tempfile.TemporaryDirectory() as tmp:
            joblib.dump(self.model, Path(tmp) / "m.joblib")
            loaded = joblib.load(Path(tmp) / "m.joblib")
        np.testing.assert_allclose(loaded.predict(self.feats[self.t])["p_full"],
                                   self.model.predict(self.feats[self.t])["p_full"])

    def test_baseline_only_uses_past(self):
        from ml.demand_model import baseline_predict

        tampered = self.courses.copy()
        tampered.loc[tampered["idx"] >= self.t, ["y_log", "y_full", "y_admit"]] = 99.0
        self.assertTrue(baseline_predict(self.courses, self.t, "mean").equals(baseline_predict(tampered, self.t, "mean")))

    def test_predict_missing_fills_new_semester_without_cached_model(self):
        """新學期公告後補預測：沒有模型快取（或快取讀不了）時，要依 meta 重訓凍結模型"""
        from ml.demand_model import predict_missing

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            csv_path = tmp / "all_courses_test.csv"
            HISTORY.to_csv(csv_path, index=False, encoding="utf-8-sig")
            (tmp / "demand_model.meta.json").write_text(json.dumps(
                {"frozen_train_semesters": ["113-2", "114-1"]}), encoding="utf-8")
            keys = ["學年度", "學期", "課程代碼", "序號"]
            prior = self.courses[self.courses["idx"] == label_to_idx("114-1")]
            prior[keys].assign(p_full=0.1, est_admit=1.0, admit_lo=1.0, admit_hi=1.0, idx=prior["idx"],
                               model_version="demand-v1").to_csv(tmp / "demand_predictions.csv", index=False,
                                                                 encoding="utf-8-sig")
            self.assertEqual(predict_missing(csv_path, tmp), ["114-2"])
            filled = pd.read_csv(tmp / "demand_predictions.csv")
            self.assertEqual(set(filled["學期"][filled["學年度"] == 114]), {1, 2})
            self.assertTrue((tmp / "demand_model.joblib").exists())
            self.assertEqual(predict_missing(csv_path, tmp), [])   # 已補過就不重複

    def test_evaluate_perfect_prediction(self):
        from ml.demand_model import evaluate

        truth = pd.DataFrame({"y_log": [0.5, -1.0, 0.2], "y_full": [1.0, 0.0, 1.0], "y_admit": [0.6, 1.0, 0.8]})
        pred = pd.DataFrame({"pred_log": [0.5, -1.0, 0.2], "p_full": [1.0, 0.0, 1.0], "est_admit": [0.6, 1.0, 0.8]})
        m = evaluate(truth, pred)
        self.assertEqual((m["mae_log"], m["mae_admit"], m["brier"], m["pr_auc"]), (0, 0, 0, 1))


# ============================================================ 中籤預測：讀取層與靜態資料包

def write_predictions(folder: Path) -> None:
    pd.DataFrame([{
        "學年度": 114, "學期": 2, "課程代碼": "00227", "序號": 5, "pred_log": 0.5, "q10": 0.1, "q90": 0.9,
        "p_full": 0.81234, "est_admit": 0.6, "admit_lo": 0.4, "admit_hi": 0.9, "idx": 29, "model_version": "demand-v1",
    }]).to_csv(folder / "demand_predictions.csv", index=False, encoding="utf-8-sig")


class TestPredictionStore(unittest.TestCase):
    def test_lookup_normalizes_key_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_predictions(Path(tmp))
            store = PredictionStore(Path(tmp), allow_compute=False)
            course = {"學年度": "114", "學期": 2.0, "課程代碼": "00227", "序號": "5", "上限人數": 60, "課程性質": "跨學院通識"}
            self.assertAlmostEqual(store.get(course)["p_full"], 0.8123)

    def test_not_applicable_courses_get_none(self):
        base = {"學年度": 114, "學期": 2, "課程代碼": "00227", "序號": 5, "上限人數": 60}
        with tempfile.TemporaryDirectory() as tmp:
            write_predictions(Path(tmp))
            store = PredictionStore(Path(tmp), allow_compute=False)
            self.assertIsNone(store.get({**base, "課程性質": "自由選修"}))
            self.assertIsNone(store.get({**base, "課程性質": "系選修", "上限人數": 0}))
        self.assertTrue(applicable({**base, "課程性質": "系選修"}))

    def test_missing_model_dir_is_harmless(self):
        store = PredictionStore(Path(tempfile.gettempdir()) / "no-such-models-dir", allow_compute=False)
        courses = [{"學年度": 114, "學期": 2, "課程代碼": "X", "序號": 1, "上限人數": 30, "課程性質": "系選修"}]
        store.attach(courses)
        self.assertIsNone(courses[0]["admission_pred"])
        self.assertEqual(store.meta(), {})


class TestStaticBundlePredictions(unittest.TestCase):
    """靜態資料包只讀預測檔：CI 不裝 scikit-learn 也要能建置"""

    def test_attach_predictions_rounds_and_counts(self):
        spec = importlib.util.spec_from_file_location("build_static", ROOT / "scripts" / "build_static.py")
        build_static = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_static)
        courses = [
            {"學年度": 114, "學期": 2, "課程代碼": "00227", "序號": 5, "上限人數": 60, "課程性質": "跨學院通識"},
            {"學年度": 114, "學期": 2, "課程代碼": "99999", "序號": 1, "上限人數": 60, "課程性質": "系選修"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            write_predictions(Path(tmp))
            n = build_static.attach_predictions(courses, PredictionStore(Path(tmp), allow_compute=False))
        self.assertEqual(n, 1)
        self.assertEqual(courses[0]["admission_pred"], {"p_full": 0.812, "est_admit": 0.6, "admit_lo": 0.4, "admit_hi": 0.9})
        self.assertNotIn("admission_pred", courses[1])


# ============================================================ 選課助理

class TestParseRequest(unittest.TestCase):
    def test_common_phrasings(self):
        cases = [
            ("週五不要有課", "avoid_days", ["五"]),
            ("只要週二或週四", "preferred_days", ["二", "四"]),
            ("2 學分通識", "credits", 2),
            ("2 學分通識", "category", "核心通識"),
            ("不要太難搶", "max_p_full", 0.3),
            ("不要早八", "avoid_first_period", True),
            ("給我三門", "limit", 3),
            ("跟資料分析有關", "keyword", "資料分析"),
        ]
        for text, key, value in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_request(text)[key], value)


class LyingClient:
    """先正常查課，最後卻推薦一門不存在的課＋一門真的課"""
    name = "lying"

    def __init__(self, real):
        self.real = real

    def chat(self, messages, tools=None, format=None):
        if format is None:
            called = any(m.get("role") == "tool" for m in messages)
            return {"content": "", "tool_calls": [] if called else [{"name": "find_courses", "arguments": {"limit": 3}}]}
        return {"content": json.dumps({"reply": "ok", "suggestions": [
            {"code": "FAKE1", "serial": "999", "reason": "編的"},
            {"code": self.real[0], "serial": self.real[1], "reason": "真的"},
        ]}, ensure_ascii=False), "tool_calls": []}


class TestAgent(unittest.TestCase):
    def test_hallucinated_course_is_dropped(self):
        ctx = semester_context()
        real = next(iter(ctx._by_key))
        res = run_agent([{"role": "user", "content": "隨便推薦"}], ctx, LyingClient(real))
        self.assertEqual([d["code"] for d in res["dropped"]], ["FAKE1"])
        self.assertEqual([c["課程代碼"] for c in res["courses"]], [real[0]])
        self.assertEqual(res["tool_trace"][0]["tool"], "find_courses")

    def test_mock_agent_respects_user_constraints(self):
        q = "星期一和三不想上課，找通識"
        res = run_agent([{"role": "user", "content": q}], semester_context(), MockClient())
        self.assertTrue(res["courses"])
        for c in res["courses"]:
            self.assertTrue(all(course_satisfies(c, parse_request(q)).values()), c["課程名稱"])

    def test_find_courses_excludes_current_and_conflicts(self):
        first = filter_by_semester(HISTORY, 114, 2).iloc[0].to_dict()
        ctx = semester_context(current=[first])
        keys = {(c["code"], c["serial"]) for c in find_courses(ctx, limit=30)["courses"]}
        self.assertNotIn((first["課程代碼"], str(first["序號"])), keys)
        conflict = check_schedule_conflict(ctx, [{"code": first["課程代碼"], "serial": str(first["序號"])}])
        self.assertTrue(conflict["conflicts"], "同一門課和自己一定衝堂")

    def test_avoid_days_and_category(self):
        res = find_courses(semester_context(), category="核心通識", avoid_days=["一", "二"], limit=30)
        self.assertGreater(res["count"], 0)
        for c in res["courses"]:
            self.assertEqual(c["class"], "核心通識")
            self.assertFalse(c["time"].startswith(("週一", "週二")))

    def test_tool_errors_are_returned_not_raised(self):
        ctx = semester_context()
        self.assertIn("error", run_tool(ctx, "rm_rf", {}))
        self.assertIn("error", run_tool(ctx, "find_courses", {"limit": "not-a-number"}))


class TestChatEndpoint(unittest.TestCase):
    """/api/ai/chat 的整合行為：直接呼叫端點函式，不需要 HTTP client"""

    def setUp(self):
        from api import app as app_module

        self.app_module = app_module
        self._originals = (app_module.get_latest_courses_df, app_module.get_raw_courses_df,
                           app_module.prediction_store, app_module.get_syllabus_index)
        app_module.get_latest_courses_df = lambda: HISTORY
        app_module.get_raw_courses_df = lambda: HISTORY
        app_module.prediction_store = PredictionStore(Path(tempfile.gettempdir()) / "no-such-models-dir", allow_compute=False)
        app_module.get_syllabus_index = lambda year, semester: None

    def tearDown(self):
        (self.app_module.get_latest_courses_df, self.app_module.get_raw_courses_df,
         self.app_module.prediction_store, self.app_module.get_syllabus_index) = self._originals

    def test_chat_returns_verified_courses(self):
        request = self.app_module.ChatRequest(messages=[{"role": "user", "content": "找通識"}],
                                              year=114, semester=2, provider="mock")
        body = self.app_module.ai_chat(request)
        self.assertEqual(body["provider"], "mock")
        self.assertEqual(body["semester"], "114-2")
        self.assertTrue(body["courses"])
        self.assertTrue(all("ai_reason" in c and "admission_pred" in c for c in body["courses"]))

    def test_last_message_must_be_user(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.app_module.ai_chat(self.app_module.ChatRequest(messages=[]))
        self.assertEqual(ctx.exception.status_code, 400)


# ============================================================ 教學大綱

RAW_SYLLABUS = """國立彰化師範大學 115學年度 第1學期
課程大綱暨教學計畫表
授課教師：王小明
科目名稱：資料分析入門
教學意見反應問卷類型：
1.講述 2.討論 3.實習
教學型態：(0)
教學目標：
學會用 Python 做資料分析與視覺化。
評量方式項目 百分比
期中考 30%
期末報告 40%
課
程
對
核
心
能
力
教學內容與進度：
1 課程介紹
"""


class TestSyllabusText(unittest.TestCase):
    def test_clean_text_removes_boilerplate_and_joins_vertical_text(self):
        t = clean_text(RAW_SYLLABUS.replace("\n", "\r\n"))
        self.assertNotIn("課程大綱暨教學計畫表", t)
        self.assertNotIn("問卷類型", t)
        self.assertIn("課程對核心能力", t)

    def test_sections_and_overlapping_chunks(self):
        names = [n for n, _ in split_sections(clean_text(RAW_SYLLABUS))]
        self.assertEqual(names[:3], ["基本資料", "教學目標", "評量方式"])
        self.assertEqual(names[-1], "教學進度")
        chunks = chunk_text("教學目標：" + "資料分析" * 400, size=500, overlap=80)
        self.assertTrue(all(len(c) <= 500 for _, c in chunks))
        self.assertEqual(chunks[0][1][-80:], chunks[1][1][:80])


@unittest.skipUnless(HAS_SKLEARN, "需要 scikit-learn（pip install -r requirements-ai.txt）")
class TestSyllabusIndex(unittest.TestCase):
    def setUp(self):
        from ai.index import SyllabusIndex

        meta = pd.DataFrame([
            {"code": "A1", "serial": 1, "name": "資料分析入門", "section": "教學目標", "text": "學會用 Python 做資料分析與視覺化"},
            {"code": "A1", "serial": 1, "name": "資料分析入門", "section": "評量方式", "text": "期中考 30% 期末報告 40%"},
            {"code": "B2", "serial": 2, "name": "西洋美術史", "section": "教學目標", "text": "欣賞文藝復興繪畫與雕塑"},
        ])
        client = MockClient()
        vecs = np.asarray(client.embed([f"{n}｜{s}：{t}" for n, s, t in zip(meta.name, meta.section, meta.text)]), dtype=np.float32)
        info = {"year": 115, "semester": 1, "provider": "mock", "embed_model": "mock", "dim": vecs.shape[1]}
        self.index = SyllabusIndex(vecs, meta, info, client)

    def test_search_ranks_relevant_course_first(self):
        hits = self.index.search("Python 資料分析", k=2)
        self.assertEqual(hits[0]["code"], "A1")
        self.assertEqual(len({(h["code"], h["serial"]) for h in hits}), len(hits))
        self.assertEqual(self.index.search("Python", year=114, semester=2), [])

    def test_qa_cites_the_course_own_chunks(self):
        from ai.index import answer_from_syllabus

        ans = answer_from_syllabus(self.index, "A1", 1, "期中考占多少", MockClient())
        self.assertEqual(ans["citations"][0]["section"], "評量方式")
        self.assertEqual(answer_from_syllabus(self.index, "ZZZ", 9, "?", MockClient())["citations"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
