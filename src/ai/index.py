"""教學大綱向量索引：切塊 → embedding → numpy 檔。查詢時用 cosine 相似度，再加一點關鍵字加分（混合搜尋）。

不另外引入向量資料庫：一學期約一萬多塊 × 1024 維，numpy 直接乘就夠快。
索引會記住當初用哪個 provider 做 embedding，查詢時必須用同一個，否則向量空間不一致。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from config import EMBED_MODEL, SYLLABUS_DIR

from .syllabus import chunk_text, clean_text, extract_text, manifest_path, pdf_dir

log = logging.getLogger(__name__)

KEYWORD_BONUS = 0.15  # 查詢字串原文出現在課名或段落時的加分；讓專有名詞（Python、SPSS）不被語意平均掉
DENSE_WEIGHT = 0.5    # 混合搜尋：向量相似度與字元 bigram TF-IDF 各半（固定值，未針對評估題目調整）
BATCH = 32


def _paths(year: int, semester: int) -> Dict[str, Path]:
    stem = SYLLABUS_DIR / f"index_{year}-{semester}"
    return {"vec": stem.with_suffix(".npy"), "meta": stem.with_suffix(".csv"), "info": stem.with_suffix(".json")}


def _normalize(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


class SyllabusIndex:
    def __init__(self, vectors: np.ndarray, meta: pd.DataFrame, info: Dict[str, Any], client=None):
        self.vectors = vectors.astype(np.float32)
        self.meta = meta.reset_index(drop=True)
        self.info = info
        self.client = client
        self.year, self.semester = info["year"], info["semester"]
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.tfidf = TfidfVectorizer(analyzer="char", ngram_range=(2, 2), sublinear_tf=True)
        self.lex = self.tfidf.fit_transform((self.meta["name"].astype(str) + "｜" + self.meta["text"].astype(str)).tolist())

    # ------------------------------------------------------------ build / load
    @classmethod
    def build(cls, year: int, semester: int, client) -> "SyllabusIndex":
        mf = pd.read_csv(manifest_path(year, semester), encoding="utf-8-sig", dtype={"課程代碼": str})
        mf = mf[(mf["n_chars"] > 50) & (mf["file"].astype(str) != "")]
        rows = []
        for r in mf.itertuples(index=False):
            try:
                text = clean_text(extract_text(pdf_dir(year, semester) / r.file))
            except Exception as e:
                log.warning("讀不到 %s：%s", r.file, e)
                continue
            for section, chunk in chunk_text(text):
                rows.append({"code": str(r.課程代碼), "serial": int(r.序號), "name": r.課程名稱,
                             "section": section, "text": chunk})
        meta = pd.DataFrame(rows)
        if meta.empty:
            raise RuntimeError("沒有可建索引的大綱文字，請先執行 python main.py fetch-syllabi")
        inputs = [f"{n}｜{s}：{t}" for n, s, t in zip(meta["name"], meta["section"], meta["text"])]
        vecs = []
        for i in range(0, len(inputs), BATCH):
            vecs += client.embed(inputs[i:i + BATCH])
            if (i // BATCH) % 20 == 0:
                log.info("embedding %d/%d", min(i + BATCH, len(inputs)), len(inputs))
        vectors = _normalize(np.asarray(vecs, dtype=np.float32))
        provider = "mock" if getattr(client, "name", "").startswith("mock") else "ollama"
        info = {
            "year": year, "semester": semester, "provider": provider,
            "embed_model": EMBED_MODEL if provider == "ollama" else "mock-char-bigram",
            "dim": int(vectors.shape[1]), "n_chunks": int(len(meta)), "n_courses": int(meta[["code", "serial"]].drop_duplicates().shape[0]),
            "built_at": datetime.now().isoformat(timespec="seconds"),
        }
        p = _paths(year, semester)
        SYLLABUS_DIR.mkdir(parents=True, exist_ok=True)
        np.save(p["vec"], vectors)
        meta.to_csv(p["meta"], index=False, encoding="utf-8-sig")
        p["info"].write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return cls(vectors, meta, info, client)

    @classmethod
    def load(cls, year: int, semester: int, client=None) -> "SyllabusIndex":
        p = _paths(year, semester)
        if not p["info"].exists():
            raise FileNotFoundError(f"找不到 {year}-{semester} 的大綱索引")
        info = json.loads(p["info"].read_text(encoding="utf-8"))
        if client is None:
            from .llm import get_client
            client = get_client(info["provider"], fallback=False)
        meta = pd.read_csv(p["meta"], encoding="utf-8-sig", dtype={"code": str})
        return cls(np.load(p["vec"]), meta, info, client)

    # ------------------------------------------------------------ query
    def _scores(self, query: str, mode: str = "hybrid") -> np.ndarray:
        """mode：dense（只用向量）、lexical（只用字元 TF-IDF）、hybrid（各半，預設）"""
        dense = self.vectors @ _normalize(np.asarray(self.client.embed([query]), dtype=np.float32))[0] if mode != "lexical" else 0.0
        lexical = (self.lex @ self.tfidf.transform([query]).T).toarray().ravel() if mode != "dense" else 0.0
        if mode == "dense":
            scores = dense
        elif mode == "lexical":
            scores = lexical
        else:
            scores = DENSE_WEIGHT * dense + (1 - DENSE_WEIGHT) * lexical
        q_low = query.strip().lower()
        if q_low:
            hit = self.meta["text"].str.lower().str.contains(q_low, regex=False, na=False) | \
                  self.meta["name"].astype(str).str.lower().str.contains(q_low, regex=False, na=False)
            scores = scores + KEYWORD_BONUS * hit.to_numpy()
        return scores

    def search(self, query: str, k: int = 8, year: Optional[int] = None, semester: Optional[int] = None,
               mode: str = "hybrid") -> List[Dict[str, Any]]:
        if (year, semester) != (None, None) and (year, semester) != (self.year, self.semester):
            return []
        scores = self._scores(query, mode)
        df = self.meta.assign(score=scores)
        best = df.sort_values("score", ascending=False).drop_duplicates(["code", "serial"]).head(k)
        return best[["code", "serial", "name", "section", "text", "score"]].to_dict("records")

    def course_chunks(self, code: str, serial, question: str, k: int = 3) -> List[Dict[str, Any]]:
        mask = (self.meta["code"].astype(str) == str(code)) & (self.meta["serial"].astype(int) == int(serial))
        sub = self.meta[mask]
        if sub.empty:
            return []
        q = _normalize(np.asarray(self.client.embed([question]), dtype=np.float32))[0]
        scores = self.vectors[sub.index.to_numpy()] @ q
        return sub.assign(score=scores).sort_values("score", ascending=False).head(k)[["section", "text", "score"]].to_dict("records")


QA_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "cited": {"type": "array", "items": {"type": "integer"}}},
    "required": ["answer", "cited"],
}


def answer_from_syllabus(index: SyllabusIndex, code: str, serial, question: str, client) -> Dict[str, Any]:
    """只根據這門課的大綱段落回答，並回傳引用的原文。找不到相關段落就直說。"""
    chunks = index.course_chunks(code, serial, question, k=3)
    if not chunks:
        return {"answer": "這門課沒有可讀的教學大綱文字（可能沒有上傳，或是掃描檔）。", "citations": [], "provider": getattr(client, "name", "")}
    numbered = "\n\n".join(f"[{i + 1}]（{c['section']}）{c['text']}" for i, c in enumerate(chunks))
    if getattr(client, "name", "").startswith("mock"):
        best = chunks[0]
        return {"answer": f"離線模式不生成文字，以下是大綱中最相關的段落（{best['section']}）。",
                "citations": [{"n": 1, **best}], "provider": client.name}
    out = client.chat([
        {"role": "system", "content": "你是選課助理。只能根據使用者提供的教學大綱段落回答，用繁體中文，"
                                      "句末標註引用編號如 [1]。段落裡沒有的資訊就回答「大綱沒有寫」，不要推測。"},
        {"role": "user", "content": f"教學大綱段落：\n{numbered}\n\n問題：{question}"},
    ], format=QA_SCHEMA)
    try:
        data = json.loads(out["content"])
    except json.JSONDecodeError:
        data = {"answer": out["content"], "cited": []}
    cited = [n for n in data.get("cited", []) if isinstance(n, int) and 1 <= n <= len(chunks)] or [1]
    return {"answer": data.get("answer", ""), "citations": [{"n": n, **chunks[n - 1]} for n in cited],
            "provider": getattr(client, "name", "")}


def main(semester_label: Optional[str] = None):
    from utils.common import setup_logging

    from .llm import get_client
    from .syllabus import latest_semester_courses

    setup_logging()
    if semester_label:
        y, s = semester_label.split("-")
        year, semester = int(y), int(s)
    else:
        _, year, semester = latest_semester_courses()
    client = get_client()
    if getattr(client, "name", "").startswith("mock"):
        print(f"⚠️  使用離線 mock embedding（字元雜湊，沒有語意理解）。原因：{getattr(client, 'fallback_reason', 'AI_PROVIDER=mock')}")
    idx = SyllabusIndex.build(year, semester, client)
    print(f"已建立 {year}-{semester} 大綱索引：{idx.info['n_courses']} 門課、{idx.info['n_chunks']} 塊，"
          f"embedding={idx.info['embed_model']}，維度 {idx.info['dim']}")
