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
from ai.llm import ApiClient, LLMUnavailable, MockClient, OllamaClient  # noqa: E402
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

    def test_join_predictions_follows_renumbered_sections(self):
        """評估時接預測檔：序號位移的課要接到原本那一班，班次數變了的課不接"""
        from ml.demand_model import join_predictions

        cur = pd.DataFrame({"學年度": 115, "學期": 1, "課程代碼": ["A", "B", "B", "C", "C", "C"],
                            "序號": [7, 20, 21, 30, 31, 32], "labeled": True}, index=[100, 101, 102, 103, 104, 105])
        preds = pd.DataFrame({"學年度": 115, "學期": 1, "課程代碼": ["A", "B", "B", "C", "C"],
                              "序號": [9, 21, 22, 30, 31], "p_full": [0.1, 0.2, 0.3, 0.4, 0.5]})
        merged = join_predictions(cur, preds)
        self.assertEqual(merged["p_full"].to_dict(), {100: 0.1, 101: 0.2, 102: 0.3})

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


def write_section_predictions(folder: Path, sections) -> None:
    """sections：[(課程代碼, 序號, p_full), …]，都放在 114-2"""
    pd.DataFrame([{
        "學年度": 114, "學期": 2, "課程代碼": code, "序號": serial, "pred_log": 0.0, "q10": 0.0, "q90": 0.0,
        "p_full": p_full, "est_admit": 1.0, "admit_lo": 1.0, "admit_hi": 1.0, "idx": 29, "model_version": "demand-v1",
    } for code, serial, p_full in sections]).to_csv(folder / "demand_predictions.csv", index=False, encoding="utf-8-sig")


def course_rows(sections) -> pd.DataFrame:
    """sections：[(課程代碼, 序號), …]；目前爬到的 114-2 課程清單"""
    return pd.DataFrame([{"學年度": 114, "學期": 2, "課程代碼": code, "序號": serial, "上限人數": 60, "課程性質": "系選修"}
                         for code, serial in sections])


class TestPredictionSerialShift(unittest.TestCase):
    """學校的序號是清單裡的流水號：中途增開或停開別的課，後面的序號就整批位移。
    2026-11 實際發生：重新爬取後 115-1 只剩兩成的課對得上預測。"""

    def lookup(self, predictions, current):
        with tempfile.TemporaryDirectory() as tmp:
            write_section_predictions(Path(tmp), predictions)
            store = PredictionStore(Path(tmp), allow_compute=False)
            table = course_rows(current)
            return [(store.get(row, table) or {}).get("p_full") for row in table.to_dict("records")]

    def test_renumbered_course_keeps_its_prediction(self):
        self.assertEqual(self.lookup([("00139", 1854, 0.42)], [("00139", 1834)]), [0.42])

    def test_sections_of_one_course_pair_up_in_order(self):
        got = self.lookup([("62019", 358, 0.1), ("62019", 359, 0.2)], [("62019", 340), ("62019", 341)])
        self.assertEqual(got, [0.1, 0.2])

    def test_shifted_serial_wins_over_a_coincidental_exact_match(self):
        # 目前的 359 是原本的 358：不能因為預測檔剛好也有 359 就拿錯班的預測
        got = self.lookup([("62019", 358, 0.1), ("62019", 359, 0.2)], [("62019", 359), ("62019", 360)])
        self.assertEqual(got, [0.1, 0.2])

    def test_changed_number_of_sections_shows_no_prediction(self):
        # 班次數變了就分不出誰對誰：寧可不顯示
        got = self.lookup([("62019", 358, 0.1), ("62019", 359, 0.2)], [("62019", 358), ("62019", 359), ("62019", 360)])
        self.assertEqual(got, [None, None, None])

    def test_other_semesters_and_unknown_courses_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_section_predictions(Path(tmp), [("00139", 1854, 0.42)])
            store = PredictionStore(Path(tmp), allow_compute=False)
            table = course_rows([("00139", 1834), ("99999", 7)])
            other_semester = {"學年度": 115, "學期": 1, "課程代碼": "00139", "序號": 1834, "上限人數": 60, "課程性質": "系選修"}
            self.assertIsNone(store.get(table.to_dict("records")[1], table))
            self.assertIsNone(store.get(other_semester, table))

    def test_multiple_time_slot_rows_count_as_one_section(self):
        # 原始資料一門課每個上課時段一列，序號相同
        got = self.lookup([("67030", 213, 0.3)], [("67030", 200), ("67030", 200)])
        self.assertEqual(got, [0.3, 0.3])


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

    def test_attach_predictions_follows_renumbered_courses(self):
        spec = importlib.util.spec_from_file_location("build_static", ROOT / "scripts" / "build_static.py")
        build_static = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_static)
        table = course_rows([("00139", 1834), ("54012", 463)])
        courses = table.to_dict("records")
        with tempfile.TemporaryDirectory() as tmp:
            write_section_predictions(Path(tmp), [("00139", 1854, 0.42), ("54012", 463, 0.08)])
            n = build_static.attach_predictions(courses, PredictionStore(Path(tmp), allow_compute=False), table)
        self.assertEqual(n, 2)
        self.assertEqual([c["admission_pred"]["p_full"] for c in courses], [0.42, 0.08])


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

    def test_stats_flag_invalid_final_json(self):
        class ProseClient(LyingClient):
            def chat(self, messages, tools=None, format=None):
                out = super().chat(messages, tools, format)
                return {**out, "content": "我推薦資料分析", "prompt_tokens": 1234} if format else out

        ctx = semester_context()
        res = run_agent([{"role": "user", "content": "隨便推薦"}], ctx, ProseClient(next(iter(ctx._by_key))))
        self.assertEqual(res["stats"], {"max_prompt_tokens": 1234, "tool_errors": 0, "final_json_ok": False, "truncated": 0})
        self.assertEqual(res["courses"], [])
        self.assertEqual(res["reply"], "我推薦資料分析")


def fake_ollama(installed, model="qwen2.5:7b", embed_model="bge-m3"):
    """不連 Ollama、也不需要裝 ollama 套件的 OllamaClient（CI 沒裝 requirements-ai.txt）"""
    from types import SimpleNamespace

    client = OllamaClient.__new__(OllamaClient)
    client.model, client.embed_model, client.num_ctx, client.name = model, embed_model, 8192, f"ollama:{model}"
    client.client = SimpleNamespace(list=lambda: SimpleNamespace(models=[SimpleNamespace(model=n) for n in installed]))
    return client


class TestOllamaClient(unittest.TestCase):
    def test_ping_requires_both_chat_and_embed_model(self):
        from ai.llm import LLMUnavailable

        fake_ollama(["qwen2.5:7b", "bge-m3:latest"]).ping()
        with self.assertRaisesRegex(LLMUnavailable, "bge-m3"):
            fake_ollama(["qwen2.5:7b"]).ping()
        with self.assertRaisesRegex(LLMUnavailable, "qwen2.5:7b"):
            fake_ollama(["qwen2.5:14b", "bge-m3:latest"]).ping()  # 同系列不同大小不算

    def test_get_client_parses_model_and_falls_back(self):
        from unittest import mock

        from ai import llm

        made = []

        def factory(model):
            made.append(model)
            return fake_ollama(["qwen2.5:14b", "bge-m3"] if model == "qwen2.5:14b" else [], model=model)

        with mock.patch.object(llm, "OllamaClient", side_effect=factory), mock.patch.dict(llm._client_cache, clear=True):
            c = llm.get_client("ollama:qwen2.5:14b")
            self.assertEqual(c.name, "ollama:qwen2.5:14b")
            self.assertIs(llm.get_client("ollama:qwen2.5:14b"), c)  # 快取，不重新 ping
            self.assertEqual(made, ["qwen2.5:14b"])
            fallback = llm.get_client("ollama:not-pulled")
            self.assertTrue(fallback.name.startswith("mock"))
            self.assertIn("not-pulled", fallback.fallback_reason)
            with self.assertRaises(Exception):
                llm.get_client("ollama:not-pulled", fallback=False)


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _reply(content="", tool_calls=()):
    calls = [_Obj(function=_Obj(name=n, arguments=json.dumps(a))) for n, a in tool_calls]
    return _Obj(choices=[_Obj(message=_Obj(content=content, tool_calls=calls or None))])


class _BadRequest(Exception):
    status_code = 400


class StubOpenAI:
    """假的 OpenAI SDK：記下每次送出的參數，依序回傳預先寫好的回覆，不連網"""

    def __init__(self, replies, reject_json_schema=False):
        self.replies, self.sent, self.reject = list(replies), [], reject_json_schema
        self.chat = _Obj(completions=_Obj(create=self._create))

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        if self.reject and kwargs.get("response_format", {}).get("type") == "json_schema":
            raise _BadRequest("json_schema not supported")
        return self.replies.pop(0)


class TestApiClient(unittest.TestCase):
    def test_agent_loop_over_openai_format(self):
        ctx = semester_context()
        real = next(iter(ctx._by_key))
        stub = StubOpenAI([
            _reply(tool_calls=[("find_courses", {"limit": 3})]),
            _reply(),
            _reply(json.dumps({"reply": "ok", "suggestions": [{"code": real[0], "serial": real[1], "reason": "r"}]})),
        ])
        client = ApiClient(api_key="test", model="m", client=stub)
        res = run_agent([{"role": "user", "content": "隨便推薦"}], ctx, client)
        self.assertEqual([c["課程代碼"] for c in res["courses"]], [real[0]])

        # 第二次請求：assistant 的 tool_calls 要有 id、arguments 是字串，tool 訊息用 tool_call_id 對應
        msgs = stub.sent[1]["messages"]
        call = next(m for m in msgs if m["role"] == "assistant")["tool_calls"][0]
        tool = next(m for m in msgs if m["role"] == "tool")
        self.assertEqual(tool["tool_call_id"], call["id"])
        self.assertEqual(json.loads(call["function"]["arguments"]), {"limit": 3})
        self.assertEqual(stub.sent[2]["response_format"]["type"], "json_schema")

    def test_falls_back_to_json_object_and_strips_code_fence(self):
        stub = StubOpenAI([_reply('```json\n{"answer": "a", "cited": [1]}\n```')], reject_json_schema=True)
        client = ApiClient(api_key="test", model="m", client=stub)
        out = client.chat([{"role": "user", "content": "q"}], format={"type": "object"})
        self.assertEqual(json.loads(out["content"]), {"answer": "a", "cited": [1]})
        self.assertEqual(stub.sent[-1]["response_format"], {"type": "json_object"})

    def test_missing_key_is_unavailable(self):
        with self.assertRaises(LLMUnavailable):
            ApiClient(api_key="", model="m", client=StubOpenAI([]))


class TestAiGate(unittest.TestCase):
    """公開模式：密鑰、限流、只開放 /api/ai/*（規則見 src/api/ai_gate.py）"""

    def setUp(self):
        from api.ai_gate import RateLimiter, gate

        self.now = [1_000_000.0]
        self.limiter = RateLimiter(per_minute=2, per_day=3, global_per_day=4, clock=lambda: self.now[0])
        self.gate = lambda path, headers, host="127.0.0.1", secret="s3cret": gate(path, headers, host, secret, self.limiter)
        self.ok = {"x-backend-secret": "s3cret", "x-client-ip": "1.1.1.1", "cf-ray": "abc"}

    def test_local_development_is_unrestricted(self):
        for _ in range(10):
            self.assertIsNone(self.gate("/api/ai/chat", {}, secret=""))

    def test_secret_required_for_ai_paths(self):
        self.assertEqual(self.gate("/api/ai/status", {"cf-ray": "abc"})[0], 403)
        self.assertEqual(self.gate("/api/ai/status", {"x-backend-secret": "wrong"})[0], 403)
        self.assertIsNone(self.gate("/api/ai/status", self.ok))

    def test_only_ai_paths_are_public(self):
        self.assertEqual(self.gate("/api/vacancy", {"cf-ray": "abc"})[0], 404)
        self.assertIsNone(self.gate("/api/vacancy", {}))   # 直接在主機上開 localhost

    def test_per_minute_then_per_day_limits(self):
        chat = lambda ip: self.gate("/api/ai/chat", {**self.ok, "x-client-ip": ip})
        self.assertIsNone(chat("1.1.1.1"))
        self.assertIsNone(chat("1.1.1.1"))
        self.assertEqual(chat("1.1.1.1")[0], 429)          # 同一分鐘第 3 次
        self.assertIsNone(chat("2.2.2.2"))                 # 別人不受影響
        self.now[0] += 61
        self.assertIsNone(chat("1.1.1.1"))                 # 一分鐘後恢復，今天第 3 次
        self.now[0] += 61
        self.assertIn("每天", chat("1.1.1.1")[1])          # 每人每天 3 次
        self.assertIn("全站", chat("3.3.3.3")[1])          # 全站 4 次用完
        for _ in range(10):
            self.assertIsNone(self.gate("/api/ai/status", self.ok))   # status 不計次

    def test_counts_reset_on_new_day(self):
        for _ in range(2):
            self.gate("/api/ai/chat", self.ok)
        self.now[0] += 86400
        self.assertIsNone(self.gate("/api/ai/chat", self.ok))


@unittest.skipUnless(importlib.util.find_spec("httpx"), "需要 httpx（fastapi TestClient）")
class TestAiGateMiddleware(unittest.TestCase):
    def test_middleware_is_wired(self):
        from fastapi.testclient import TestClient

        from api import app as app_module

        original = app_module.AI_SHARED_SECRET
        app_module.AI_SHARED_SECRET = "s3cret"
        try:
            client = TestClient(app_module.app)
            self.assertEqual(client.get("/api/ai/status").status_code, 403)
            self.assertEqual(client.get("/api/semesters", headers={"CF-Ray": "abc"}).status_code, 404)
        finally:
            app_module.AI_SHARED_SECRET = original


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

    def test_stale_index_keys_are_reported_not_silently_empty(self):
        from ai.tools import semantic_search_syllabus

        # 索引的課程鍵（A1、B2）在課程資料裡都不存在：不能默默回傳 0 筆
        ctx = CourseContext(semester_df=filter_by_semester(HISTORY, 114, 2), history_df=HISTORY,
                            year=115, semester=1, syllabus_index=self.index)
        res = semantic_search_syllabus(ctx, "Python 資料分析")
        self.assertFalse(res["available"])
        self.assertIn("重建索引", res["note"])

    def test_qa_reports_whether_model_cited(self):
        from ai.index import answer_from_syllabus

        class NoCiteClient:
            name = "ollama:fake"

            def chat(self, messages, tools=None, format=None):
                return {"content": json.dumps({"answer": "期中考 30%", "cited": [7]}), "tool_calls": []}

        ans = answer_from_syllabus(self.index, "A1", 1, "期中考占多少", NoCiteClient())
        self.assertEqual(ans["cited_by_model"], [])  # 7 超出範圍，等於沒標
        self.assertEqual([c["n"] for c in ans["citations"]], [1])  # 介面上仍附最相關段落

    def test_qa_truncated_output_is_not_shown_raw(self):
        from ai.index import answer_from_syllabus

        class LoopingClient:
            name = "ollama:fake"

            def chat(self, messages, tools=None, format=None):
                return {"content": '{"answer": "占 30%。[1]引用編號：[1]引用編號：[1]引用', "tool_calls": [], "truncated": True}

        ans = answer_from_syllabus(self.index, "A1", 1, "期中考占多少", LoopingClient())
        self.assertTrue(ans["truncated"])
        self.assertNotIn("引用編號", ans["answer"])
        self.assertEqual(ans["citations"][0]["section"], "評量方式")


class FakeEmbedClient:
    """代替 Ollama embedding：固定的 8 維向量"""
    name = "ollama:fake"
    provider = "ollama"
    embed_model = "fake-embed"

    def embed(self, texts):
        return [[float(len(t) % (i + 2)) + 1.0 for i in range(8)] for t in texts]


@unittest.skipUnless(HAS_SKLEARN, "需要 scikit-learn（pip install -r requirements-ai.txt）")
class TestIndexReembed(unittest.TestCase):
    def setUp(self):
        from unittest import mock

        from ai import index as index_module

        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(index_module, "SYLLABUS_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.meta = pd.DataFrame([
            {"code": "A1", "serial": 1, "name": "資料分析入門", "section": "教學目標", "text": "學會用 Python 做資料分析"},
            {"code": "B2", "serial": 2, "name": "西洋美術史", "section": "教材", "text": "藝術的故事"},
        ])
        index_module.SyllabusIndex._embed_and_save(115, 1, self.meta, MockClient())

    def test_reembed_keeps_chunks_and_backs_up_mock_index(self):
        from ai.index import SyllabusIndex

        idx = SyllabusIndex.reembed(115, 1, FakeEmbedClient())
        self.assertEqual(idx.info["provider"], "ollama")
        self.assertEqual(idx.info["embed_model"], "fake-embed")
        self.assertEqual(idx.vectors.shape, (2, 8))
        self.assertEqual(idx.meta["text"].tolist(), self.meta["text"].tolist())
        backup = json.loads((Path(self.tmp.name) / "index_115-1.mock.json").read_text(encoding="utf-8"))
        self.assertEqual(backup["embed_model"], "mock-char-bigram")
        self.assertEqual(SyllabusIndex.load(115, 1, client=MockClient(), variant="mock").vectors.shape[1], MockClient.EMBED_DIM)

    def test_load_rejects_different_embedding_model(self):
        from ai.index import SyllabusIndex

        SyllabusIndex.reembed(115, 1, FakeEmbedClient())
        self.assertEqual(SyllabusIndex.load(115, 1, client=FakeEmbedClient()).info["embed_model"], "fake-embed")
        with self.assertRaisesRegex(RuntimeError, "fake-embed"):
            SyllabusIndex.load(115, 1, client=MockClient())


if __name__ == "__main__":
    unittest.main(verbosity=2)
