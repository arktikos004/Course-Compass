"""選課助理評估：30 則手寫查詢，各自附「使用者講出來的條件」（人工寫定，不是由解析器產生）。

指標
- 條件滿足率：建議的課逐門檢查是否符合全部條件
- 幻覺率：模型建議了目標學期不存在的課（被防幻覺機制剔除）的比例
- 空答率：資料裡明明有符合條件的課（用工具直接查驗），助理卻一門都沒推薦
- 診斷用：prompt 最大用量（接近 num_ctx 代表 system prompt 可能被截掉）、工具出錯次數、最終 JSON 失敗次數
結果寫到 docs/agent_eval.md；逐題原始輸出寫到 logs/eval/（不進版控），用來人工檢查失誤。
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import AGENT_MAX_STEPS, AI_PROVIDER, DOCS_DIR, LOG_DIR, OLLAMA_NUM_CTX, OLLAMA_NUM_PREDICT, PROJECT_ROOT

from .agent import run_agent
from .context import build_context
from .llm import get_client
from .parse import course_satisfies
from .tools import course_key, find_courses, time_text

QUERIES = PROJECT_ROOT / "data" / "eval" / "agent_queries.json"
RUN_LOG_DIR = LOG_DIR / "eval"


def _write_runs(name: str, runs: List[Dict[str, Any]]) -> Path:
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = RUN_LOG_DIR / f"{name}_{re.sub(r'[^0-9A-Za-z.-]+', '_', runs[0]['provider'])}.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in runs), encoding="utf-8")
    return path


def evaluate_provider(provider: str, queries: List[Dict[str, Any]], ctx) -> pd.DataFrame:
    client = get_client(provider, fallback=False)
    label = getattr(client, "name", provider)  # 用戶端的名稱帶模型（api · <模型>、ollama:<模型>）
    rows, runs = [], []
    for i, item in enumerate(queries):
        expect = item["expect"]
        oracle = find_courses(ctx, **expect, limit=1)["count"]
        t0 = time.time()
        res = run_agent([{"role": "user", "content": item["q"]}], ctx, client)
        sec = round(time.time() - t0, 2)
        sats, detail = [], []
        for c in res["courses"]:
            checks = course_satisfies(c, expect, ctx.pred(c))
            ok = all(checks.values()) if checks else True
            sats.append(ok)
            code, serial = course_key(c)
            detail.append({"code": code, "serial": serial, "name": c.get("課程名稱"), "time": time_text(c),
                           "ok": ok, "failed": [k for k, v in checks.items() if not v], "reason": c.get("ai_reason", "")})
        stats = res["stats"]
        rows.append({
            "id": i + 1, "query": item["q"], "provider": label,
            "n_suggested": len(res["courses"]), "n_hallucinated": len(res["dropped"]),
            "n_satisfying": sum(sats), "feasible": oracle > 0,
            "tools": " → ".join(t["tool"] for t in res["tool_trace"]),
            "sec": sec, "max_prompt_tokens": stats["max_prompt_tokens"],
            "tool_errors": stats["tool_errors"], "json_ok": stats["final_json_ok"], "truncated": stats["truncated"],
        })
        runs.append({"id": i + 1, "provider": provider, "query": item["q"], "expect": expect, "feasible": oracle > 0,
                     "reply": res["reply"], "suggested": detail, "dropped": res["dropped"],
                     "tool_trace": res["tool_trace"], "stats": stats, "sec": sec})
        print(f"  [{provider}] {i + 1:2d}/{len(queries)} 推薦 {len(detail)}、符合 {sum(sats)}、{sec}s")
    print(f"逐題輸出：{_write_runs('agent', runs)}")
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> Dict[str, Any]:
    suggested = df["n_suggested"].sum()
    proposed = suggested + df["n_hallucinated"].sum()
    feasible = df[df["feasible"]]
    return {
        "queries": len(df),
        "suggested": int(suggested),
        "constraint_satisfaction": float(df["n_satisfying"].sum() / suggested) if suggested else float("nan"),
        "queries_all_satisfied": float(((df["n_satisfying"] == df["n_suggested"]) & (df["n_suggested"] > 0)).sum() / max(1, (df["n_suggested"] > 0).sum())),
        "hallucination_rate": float(df["n_hallucinated"].sum() / proposed) if proposed else 0.0,
        "empty_when_feasible": float((feasible["n_suggested"] == 0).mean()) if len(feasible) else float("nan"),
        "median_sec": float(df["sec"].median()),
        "max_prompt_tokens": int(df["max_prompt_tokens"].max()),
        "tool_errors": int(df["tool_errors"].sum()),
        "json_fail": int((~df["json_ok"].astype(bool)).sum()),
        "truncated": int(df["truncated"].sum()),
    }


def main(providers: Optional[List[str]] = None):
    from utils.common import setup_logging

    setup_logging()
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    ctx = build_context()
    providers = providers or ["mock"] + ([AI_PROVIDER] if AI_PROVIDER != "mock" else [])
    results, summaries = [], {}
    for p in providers:
        try:
            df = evaluate_provider(p, queries, ctx)
        except Exception as e:
            print(f"跳過 {p}：{e}")
            continue
        results.append(df)
        label = df["provider"].iloc[0]
        summaries[label] = summarize(df)
        print(label, json.dumps(summaries[label], ensure_ascii=False))
    if not results:
        return
    all_df = pd.concat(results)
    index_info = ctx.syllabus_index.info if ctx.syllabus_index is not None else None
    lines = [
        "# 選課助理評估",
        "",
        f"> 目標學期 {ctx.year}-{ctx.semester}；查詢與條件見 `data/eval/agent_queries.json`（條件為人工寫定，與解析器無關）。",
        "> mock 的「理解」就是 `ai/parse.py` 的規則，所以 mock 的分數反映的是規則解析器的涵蓋度，不代表 LLM。",
        f"> Ollama 設定：temperature 0、num_ctx {OLLAMA_NUM_CTX}、單次輸出上限 {OLLAMA_NUM_PREDICT} tokens、最多 {AGENT_MAX_STEPS} 輪工具呼叫；"
        f"大綱搜尋工具用的索引：{index_info['embed_model'] if index_info else '未建立'}。",
        "> 逐題原始輸出（回覆、推薦理由、每門課沒過的條件）在 `logs/eval/agent_*.jsonl`，不進版控。",
        "",
        "| provider | 查詢數 | 推薦課數 | 條件滿足率 | 全部符合的查詢比例 | 幻覺率 | 有解卻空答 | 中位秒數 | 最大 prompt tokens | 工具錯誤 | JSON 失敗 | 輸出被截斷 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for p, s in summaries.items():
        lines.append(f"| {p} | {s['queries']} | {s['suggested']} | {s['constraint_satisfaction']:.1%} | "
                     f"{s['queries_all_satisfied']:.1%} | {s['hallucination_rate']:.1%} | {s['empty_when_feasible']:.1%} | "
                     f"{s['median_sec']:.1f} | {s['max_prompt_tokens'] or '—'} | {s['tool_errors']} | {s['json_fail']} | {s['truncated']} |")
    lines += ["", "## 逐題結果", "", "| # | provider | 查詢 | 推薦 | 符合 | 幻覺 | 有解 | 秒 | 工具 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in all_df.sort_values(["id"], kind="stable").itertuples(index=False):
        lines.append(f"| {r.id} | {r.provider} | {r.query} | {r.n_suggested} | {r.n_satisfying} | {r.n_hallucinated} | "
                     f"{'✓' if r.feasible else '✗'} | {r.sec:.1f} | {r.tools} |")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "agent_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"報告：{DOCS_DIR / 'agent_eval.md'}")


# ================================================================ 教學大綱搜尋評估

RAG_QUERIES = PROJECT_ROOT / "data" / "eval" / "rag_queries.json"
QA_QUERIES = PROJECT_ROOT / "data" / "eval" / "qa_queries.json"
# 「大綱沒有寫」是 system prompt 指定的說法；模型換句話說時也算拒答
ABSTAIN_PHRASES = ("大綱沒有寫", "沒有寫", "未提及", "沒有提到", "未說明", "沒有說明", "無法得知", "沒有相關資訊")


def _course_texts(index) -> pd.DataFrame:
    g = index.meta.groupby(["code", "serial"], sort=False)
    return pd.DataFrame({"name": g["name"].first(), "text": g["text"].apply(lambda s: "\n".join(s))}).reset_index()


def _metrics(ranked: List[tuple], relevant: set, k: int = 10) -> Dict[str, float]:
    top = ranked[:k]
    hits = [key in relevant for key in top]
    rr = next((1.0 / (i + 1) for i, h in enumerate(ranked) if h in relevant), 0.0)
    return {"p@5": sum(hits[:5]) / 5, "p@10": sum(hits) / k, "hit@5": float(any(hits[:5])), "mrr": rr}


def _norm(s: Any) -> str:
    """比對用：去空白、全形百分號轉半形（PDF 文字層和模型輸出的寫法不一）"""
    return re.sub(r"\s+", "", str(s)).replace("％", "%")


def _ranked(index, query: str, mode: str) -> List[tuple]:
    return [(str(h["code"]), int(h["serial"])) for h in index.search(query, k=30, mode=mode)]


def eval_qa(index, providers: List[str]) -> List[str]:
    """大綱問答：可回答題看答對與引用，不可回答題看是否說「大綱沒有寫」。題目與預期詞在跑模型前寫定。"""
    from .index import answer_from_syllabus

    items = json.loads(QA_QUERIES.read_text(encoding="utf-8"))
    meta = index.meta
    # 標註自我檢查：預期詞必須真的在該課大綱裡，「不可回答」的相關詞必須真的不在
    for it in items:
        mask = (meta["code"].astype(str) == str(it["code"])) & (meta["serial"].astype(int) == int(it["serial"]))
        full = _norm("\n".join(meta.loc[mask, "text"].astype(str)))
        if it["answerable"]:
            it["valid"] = any(_norm(t) in full for t in it["expect_any"])
        else:
            it["valid"] = bool(full) and not any(_norm(t) in full for t in it["absent"])
        if not it["valid"]:
            print(f"⚠️  問答題標註不成立，已排除：{it['name']}「{it['q']}」")
    items = [it for it in items if it["valid"]]
    answerable = [it for it in items if it["answerable"]]

    # 檢索與模型無關：top-3 段落有沒有含預期詞
    for it in answerable:
        chunks = index.course_chunks(it["code"], it["serial"], it["q"], k=3)
        it["retrieved"] = any(_norm(t) in _norm(c["text"]) for c in chunks for t in it["expect_any"])

    rows = []
    for p in providers:
        if p.lower() == "mock":
            continue  # mock 不生成文字，只有上面的檢索命中可比
        try:
            client = get_client(p, fallback=False)
        except Exception as e:
            print(f"跳過 {p}：{e}")
            continue
        for it in items:
            t0 = time.time()
            ans = answer_from_syllabus(index, it["code"], it["serial"], it["q"], client)
            answer = _norm(ans["answer"])
            abstained = any(a in answer for a in ABSTAIN_PHRASES)
            cited = {c["n"]: _norm(c["text"]) for c in ans["citations"]}
            if it["answerable"]:
                correct = any(_norm(t) in answer for t in it["expect_any"])
                cite_ok = any(_norm(t) in cited.get(n, "") for n in ans["cited_by_model"] for t in it["expect_any"])
            else:
                correct, cite_ok = abstained, None
            rows.append({"provider": getattr(client, "name", p), "name": it["name"], "q": it["q"], "answerable": it["answerable"],
                         "retrieved": it.get("retrieved"), "correct": correct, "cite_ok": cite_ok,
                         "abstained": abstained, "truncated": bool(ans.get("truncated")),
                         "sec": round(time.time() - t0, 2), "answer": ans["answer"]})
            print(f"  [QA {p}] {it['name']}：{'✓' if correct else '✗'} {ans['answer'][:50]}")

    lines = ["", "## 大綱問答", "",
             f"> 題目見 `data/eval/qa_queries.json`：{len(answerable)} 題可回答（答案在大綱裡，附預期詞）、"
             f"{len(items) - len(answerable)} 題不可回答（先確認整份大綱都沒有相關詞）。"
             "題目與預期詞在開發時（AI 程式助理協助）讀過大綱原文後寫定，跑模型前就固定，沒有依模型輸出修改；待組員複核。",
             "> 「答對」＝回答含任一預期詞（代理判定）；「引用有效」＝模型自己標的引用段落含預期詞；"
             "「正確拒答」＝不可回答題的回答說大綱沒有寫。",
             "",
             f"檢索命中（該課 top-3 段落含預期詞，與模型無關）：{sum(it['retrieved'] for it in answerable)}/{len(answerable)}",
             ""]
    if not rows:
        lines.append("（沒有可用的對話模型，未評估生成）")
        return lines
    df = pd.DataFrame(rows)
    lines += ["| 模型 | 可回答：答對 | 可回答：引用有效 | 可回答：誤答「沒有寫」 | 不可回答：正確拒答 | 輸出被截斷 | 中位秒數 |",
              "|---|---|---|---|---|---|---|"]
    for p, g in df.groupby("provider", sort=False):
        a, u = g[g["answerable"]], g[~g["answerable"]]
        wrongly_abstained = (a["abstained"] & ~a["correct"]).sum()
        lines.append(f"| {p} | {a['correct'].sum()}/{len(a)} | {a['cite_ok'].sum()}/{len(a)} | {wrongly_abstained}/{len(a)} | "
                     f"{u['correct'].sum()}/{len(u)} | {g['truncated'].sum()}/{len(g)} | {g['sec'].median():.1f} |")
    lines += ["", "| 模型 | 課程 | 問題 | 可答 | 檢索 | 結果 | 回答（節錄） |", "|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        answer = re.sub(r"\s+", " ", r.answer).replace("|", "／")[:80]
        lines.append(f"| {r.provider} | {r.name} | {r.q} | {'✓' if r.answerable else '✗'} | "
                     f"{'—' if r.retrieved is None else ('✓' if r.retrieved else '✗')} | {'✓' if r.correct else '✗'} | {answer} |")
    return lines


def main_rag(providers: Optional[List[str]] = None):
    """查詢為換句話說的主題描述；「相關」= 大綱含有事先寫定的任一相關詞（代理標註，詞表見 rag_queries.json）。
    基線為字元 bigram TF-IDF（純字面比對）。有 mock 索引備份時一併列出，作為換 embedding 前後的對照。
    providers 指定大綱問答要評估哪些對話模型。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from utils.common import setup_logging

    from .index import DENSE_WEIGHT, SyllabusIndex
    from .syllabus import latest_semester_courses

    setup_logging()
    _, year, semester = latest_semester_courses()
    index = SyllabusIndex.load(year, semester)
    info = index.info
    variants = [(f"混合（系統採用，{info['embed_model']}）", index, "hybrid"),
                (f"純向量（{info['embed_model']}）", index, "dense")]
    if info["provider"] != "mock":
        try:
            mock_index = SyllabusIndex.load(year, semester, client=get_client("mock"), variant="mock")
            variants += [("混合（前次：mock embedding）", mock_index, "hybrid"),
                         ("純向量（前次：mock embedding）", mock_index, "dense")]
        except FileNotFoundError:
            pass
    docs = _course_texts(index)
    keys = list(zip(docs["code"].astype(str), docs["serial"].astype(int)))
    tfidf = TfidfVectorizer(analyzer="char", ngram_range=(2, 2), sublinear_tf=True)
    X = tfidf.fit_transform((docs["name"] + "\n" + docs["text"]).tolist())

    queries = json.loads(RAG_QUERIES.read_text(encoding="utf-8"))
    rows = []
    for item in queries:
        relevant = {k for k, t in zip(keys, docs["text"]) if any(term in t for term in item["terms"])}
        lex_scores = (X @ tfidf.transform([item["q"]]).T).toarray().ravel()
        ranked = [(name, _ranked(idx, item["q"], mode)) for name, idx, mode in variants]
        ranked.append(("基線：整篇字面 TF-IDF", [keys[i] for i in lex_scores.argsort()[::-1][:30]]))
        for name, r in ranked:
            rows.append({"query": item["q"], "method": name, "n_relevant": len(relevant), **_metrics(r, relevant)})
    df = pd.DataFrame(rows)
    order = list(dict.fromkeys(df["method"]))
    summary = df.groupby("method")[["p@5", "p@10", "hit@5", "mrr"]].mean().loc[order]
    print(summary.to_string(float_format=lambda v: f"{v:.3f}"))

    lines = [
        "# 教學大綱搜尋評估",
        "",
        f"> 學期 {year}-{semester}，索引 {info['n_courses']} 門課、{info['n_chunks']} 塊，embedding `{info['embed_model']}`（{info['provider']}），建立於 {info['built_at']}。",
        "> 「相關」的定義：大綱全文含有 `data/eval/rag_queries.json` 中事先寫定的任一相關詞。這是**代理標註**，不是人工逐篇判讀；"
        "相關詞本身偏字面，對字面基線較有利。人工盲標留待組員複核。",
        f"> 混合搜尋的權重固定為向量 {DENSE_WEIGHT}／字面 {1 - DENSE_WEIGHT}，查詢原文命中再加分；沒有針對這些題目調整。",
    ]
    if info["provider"] == "mock":
        lines.append("> ⚠️ 本次索引使用離線 mock embedding（字元雜湊），**沒有語意能力**，數字只證明流程可運作；"
                     "要評估真正的語意搜尋，請在 .env 設定 `LLM_API_EMBED_MODEL`（或安裝 Ollama 並 `ollama pull bge-m3`）後重建索引再跑。")
    elif len(variants) > 2:
        lines.append("> 「前次：mock embedding」是換成真 embedding 前的索引（切塊完全相同，只有向量不同），留作對照。")
    lines += ["", "| 方法 | P@5 | P@10 | Hit@5 | MRR |", "|---|---|---|---|---|"]
    for m, r in summary.iterrows():
        lines.append(f"| {m} | {r['p@5']:.3f} | {r['p@10']:.3f} | {r['hit@5']:.3f} | {r['mrr']:.3f} |")
    lines += ["", "## 逐題", "", "| 查詢 | 相關課數 | 方法 | P@5 | P@10 | MRR |", "|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        lines.append(f"| {r.query} | {r.n_relevant} | {r.method} | {r._3:.2f} | {r._4:.2f} | {r.mrr:.2f} |")

    lines += eval_qa(index, providers or ([AI_PROVIDER] if AI_PROVIDER != "mock" else []))
    (DOCS_DIR / "rag_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"報告：{DOCS_DIR / 'rag_eval.md'}")
