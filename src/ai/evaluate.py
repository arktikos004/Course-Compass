"""選課助理評估：30 則手寫查詢，各自附「使用者講出來的條件」（人工寫定，不是由解析器產生）。

指標
- 條件滿足率：建議的課逐門檢查是否符合全部條件
- 幻覺率：模型建議了目標學期不存在的課（被防幻覺機制剔除）的比例
- 空答率：資料裡明明有符合條件的課（用工具直接查驗），助理卻一門都沒推薦
結果寫到 docs/agent_eval.md。
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from config import DOCS_DIR, PROJECT_ROOT

from .agent import run_agent
from .context import build_context
from .llm import get_client
from .parse import course_satisfies
from .tools import find_courses

QUERIES = PROJECT_ROOT / "data" / "eval" / "agent_queries.json"


def evaluate_provider(provider: str, queries: List[Dict[str, Any]], ctx) -> pd.DataFrame:
    client = get_client(provider, fallback=False)
    rows = []
    for i, item in enumerate(queries):
        expect = item["expect"]
        oracle = find_courses(ctx, **expect, limit=1)["count"]
        t0 = time.time()
        res = run_agent([{"role": "user", "content": item["q"]}], ctx, client)
        sats = []
        for c in res["courses"]:
            checks = course_satisfies(c, expect, ctx.pred(c))
            sats.append(all(checks.values()) if checks else True)
        rows.append({
            "id": i + 1, "query": item["q"], "provider": provider,
            "n_suggested": len(res["courses"]), "n_hallucinated": len(res["dropped"]),
            "n_satisfying": sum(sats), "feasible": oracle > 0,
            "tools": " → ".join(t["tool"] for t in res["tool_trace"]),
            "sec": round(time.time() - t0, 2),
        })
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
    }


def main(providers: List[str] = None):
    from utils.common import setup_logging

    setup_logging()
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    ctx = build_context()
    providers = providers or ["mock", "ollama"]
    results, summaries = [], {}
    for p in providers:
        try:
            df = evaluate_provider(p, queries, ctx)
        except Exception as e:
            print(f"跳過 {p}：{e}")
            continue
        results.append(df)
        summaries[p] = summarize(df)
        print(p, json.dumps(summaries[p], ensure_ascii=False))
    if not results:
        return
    all_df = pd.concat(results)
    lines = [
        "# 選課助理評估",
        "",
        f"> 目標學期 {ctx.year}-{ctx.semester}；查詢與條件見 `data/eval/agent_queries.json`（條件為人工寫定，與解析器無關）。",
        "> mock 的「理解」就是 `ai/parse.py` 的規則，所以 mock 的分數反映的是規則解析器的涵蓋度，不代表 LLM。",
        "",
        "| provider | 查詢數 | 推薦課數 | 條件滿足率 | 全部符合的查詢比例 | 幻覺率 | 有解卻空答 | 中位秒數 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p, s in summaries.items():
        lines.append(f"| {p} | {s['queries']} | {s['suggested']} | {s['constraint_satisfaction']:.1%} | "
                     f"{s['queries_all_satisfied']:.1%} | {s['hallucination_rate']:.1%} | {s['empty_when_feasible']:.1%} | {s['median_sec']:.1f} |")
    lines += ["", "## 逐題結果", "", "| # | provider | 查詢 | 推薦 | 符合 | 幻覺 | 有解 | 工具 |", "|---|---|---|---|---|---|---|---|"]
    for r in all_df.itertuples(index=False):
        lines.append(f"| {r.id} | {r.provider} | {r.query} | {r.n_suggested} | {r.n_satisfying} | {r.n_hallucinated} | "
                     f"{'✓' if r.feasible else '✗'} | {r.tools} |")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "agent_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"報告：{DOCS_DIR / 'agent_eval.md'}")


# ================================================================ 教學大綱搜尋評估

RAG_QUERIES = PROJECT_ROOT / "data" / "eval" / "rag_queries.json"


def _course_texts(index) -> pd.DataFrame:
    g = index.meta.groupby(["code", "serial"], sort=False)
    return pd.DataFrame({"name": g["name"].first(), "text": g["text"].apply(lambda s: "\n".join(s))}).reset_index()


def _metrics(ranked: List[tuple], relevant: set, k: int = 10) -> Dict[str, float]:
    top = ranked[:k]
    hits = [key in relevant for key in top]
    rr = next((1.0 / (i + 1) for i, h in enumerate(ranked) if h in relevant), 0.0)
    return {"p@5": sum(hits[:5]) / 5, "p@10": sum(hits) / k, "hit@5": float(any(hits[:5])), "mrr": rr}


def main_rag():
    """查詢為換句話說的主題描述；「相關」= 大綱含有事先寫定的任一相關詞（代理標註，詞表見 rag_queries.json）。
    基線為字元 bigram TF-IDF（純字面比對）。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from utils.common import setup_logging

    from .index import SyllabusIndex
    from .syllabus import latest_semester_courses

    setup_logging()
    _, year, semester = latest_semester_courses()
    index = SyllabusIndex.load(year, semester)
    docs = _course_texts(index)
    keys = list(zip(docs["code"].astype(str), docs["serial"].astype(int)))
    tfidf = TfidfVectorizer(analyzer="char", ngram_range=(2, 2), sublinear_tf=True)
    X = tfidf.fit_transform((docs["name"] + "\n" + docs["text"]).tolist())

    queries = json.loads(RAG_QUERIES.read_text(encoding="utf-8"))
    rows = []
    for item in queries:
        relevant = {k for k, t in zip(keys, docs["text"]) if any(term in t for term in item["terms"])}
        dense = [(str(h["code"]), int(h["serial"])) for h in index.search(item["q"], k=30, mode="dense")]
        hybrid = [(str(h["code"]), int(h["serial"])) for h in index.search(item["q"], k=30, mode="hybrid")]
        lex_scores = (X @ tfidf.transform([item["q"]]).T).toarray().ravel()
        lex = [keys[i] for i in lex_scores.argsort()[::-1][:30]]
        for name, ranked in (("混合（系統採用）", hybrid), ("純向量", dense), ("基線：整篇字面 TF-IDF", lex)):
            rows.append({"query": item["q"], "method": name, "n_relevant": len(relevant), **_metrics(ranked, relevant)})
    df = pd.DataFrame(rows)
    summary = df.groupby("method")[["p@5", "p@10", "hit@5", "mrr"]].mean()
    print(summary.to_string(float_format=lambda v: f"{v:.3f}"))

    info = index.info
    lines = [
        "# 教學大綱搜尋評估",
        "",
        f"> 學期 {year}-{semester}，索引 {info['n_courses']} 門課、{info['n_chunks']} 塊，embedding `{info['embed_model']}`（{info['provider']}），建立於 {info['built_at']}。",
        "> 「相關」的定義：大綱全文含有 `data/eval/rag_queries.json` 中事先寫定的任一相關詞。這是**代理標註**，不是人工逐篇判讀；"
        "相關詞本身偏字面，對字面基線較有利。人工盲標留待組員複核。",
    ]
    if info["provider"] == "mock":
        lines.append("> ⚠️ 本次索引使用離線 mock embedding（字元雜湊），**沒有語意能力**，數字只證明流程可運作；"
                     "要評估真正的語意搜尋，請安裝 Ollama 並 `ollama pull bge-m3` 後重建索引再跑。")
    lines += ["", "| 方法 | P@5 | P@10 | Hit@5 | MRR |", "|---|---|---|---|---|"]
    for m, r in summary.iterrows():
        lines.append(f"| {m} | {r['p@5']:.3f} | {r['p@10']:.3f} | {r['hit@5']:.3f} | {r['mrr']:.3f} |")
    lines += ["", "## 逐題", "", "| 查詢 | 相關課數 | 方法 | P@5 | P@10 | MRR |", "|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        lines.append(f"| {r.query} | {r.n_relevant} | {r.method} | {r._3:.2f} | {r._4:.2f} | {r.mrr:.2f} |")
    (DOCS_DIR / "rag_eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"報告：{DOCS_DIR / 'rag_eval.md'}")
