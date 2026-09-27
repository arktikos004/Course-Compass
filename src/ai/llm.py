"""LLM 用戶端：本機 Ollama，或離線的規則式替身（MockClient）。

介面統一為：
    chat(messages, tools=None, format=None) -> {"content": str, "tool_calls": [{"name": str, "arguments": dict}]}
    embed(texts) -> List[List[float]]
寫法參考 smart-doc-archiver/src/analyzer.py 的 OllamaAnalyzer（ollama.Client + format 強制 JSON + temperature 0）。
"""

import hashlib
import json
import logging
import math
import re
from typing import Any, Dict, List, Optional

from config import AI_PROVIDER, CHAT_MODEL, EMBED_MODEL, OLLAMA_HOST, OLLAMA_TIMEOUT

from .parse import parse_request

log = logging.getLogger(__name__)


# 代理迴圈最後要求輸出 JSON 的指令；MockClient 解析需求時要跳過它，改看使用者真正的問題
FINAL_INSTRUCTION = ("請根據上面的工具結果，輸出最終回覆的 JSON：reply 是給使用者的說明，"
                     "suggestions 是推薦的課（code、serial 照抄工具結果，reason 寫具體理由）。")


class LLMUnavailable(RuntimeError):
    pass


class OllamaClient:
    name = "ollama"

    def __init__(self, host: str = OLLAMA_HOST, model: str = CHAT_MODEL, embed_model: str = EMBED_MODEL,
                 timeout: float = OLLAMA_TIMEOUT):
        import ollama

        self.model = model
        self.embed_model = embed_model
        self.client = ollama.Client(host=host, timeout=timeout)

    def ping(self) -> None:
        try:
            names = {m.model for m in self.client.list().models}
        except Exception as e:
            raise LLMUnavailable(f"連不到 Ollama（{OLLAMA_HOST}）：{e}") from e
        if not any(n == self.model or n.startswith(self.model + ":") or n.split(":")[0] == self.model for n in names):
            raise LLMUnavailable(f"Ollama 尚未下載模型 {self.model}，請執行 ollama pull {self.model}")

    def chat(self, messages: List[Dict[str, Any]], tools: Optional[list] = None,
             format: Optional[dict] = None) -> Dict[str, Any]:
        resp = self.client.chat(
            model=self.model,
            messages=messages,
            tools=tools,
            format=format,
            # 選課建議要可重現，不需要創意；工具定義＋結果超過 Ollama 預設的 context，拉到 8k
            options={"temperature": 0, "num_ctx": 8192},
        )
        msg = resp.message
        calls = []
        for tc in msg.tool_calls or []:
            args = tc.function.arguments
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": tc.function.name, "arguments": dict(args or {})})
        return {"content": msg.content or "", "tool_calls": calls}

    def embed(self, texts: List[str]) -> List[List[float]]:
        resp = self.client.embed(model=self.embed_model, input=texts)
        return [list(v) for v in resp.embeddings]


class MockClient:
    """規則式替身：用 parse_request 解析需求 → 固定呼叫 find_courses（有主題時再加大綱語意搜尋）
    → 依工具結果組出最終 JSON。行為可預期，給測試與沒有 GPU 的展示用。"""

    name = "mock"
    EMBED_DIM = 1024  # 與 bge-m3 同維度；太小的話中文 bigram 雜湊碰撞嚴重

    def ping(self) -> None:
        return None

    @staticmethod
    def _last_user(messages):
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content") != FINAL_INSTRUCTION:
                return m.get("content", "")
        return ""

    @staticmethod
    def _tool_results(messages):
        out = []
        for m in messages:
            if m.get("role") == "tool":
                try:
                    out.append((m.get("tool_name"), json.loads(m.get("content") or "{}")))
                except json.JSONDecodeError:
                    pass
        return out

    def chat(self, messages, tools=None, format=None):
        text = self._last_user(messages)
        cond = parse_request(text)
        results = self._tool_results(messages)
        tool_names = {t["function"]["name"] for t in (tools or [])}

        if format is None and not results and tools:
            args = {k: v for k, v in cond.items() if k != "limit"}
            calls = [{"name": "find_courses", "arguments": {**args, "limit": 15}}]
            if cond.get("keyword") and "semantic_search_syllabus" in tool_names:
                calls.append({"name": "semantic_search_syllabus", "arguments": {"query": cond["keyword"], "k": 15}})
            return {"content": "", "tool_calls": calls}

        # 關鍵字在課名找不到時，放寬關鍵字再查一次（大綱搜尋會補上主題相關性）
        found = [r for name, r in results if name == "find_courses"]
        if format is None and found and found[-1].get("count", 0) == 0 and cond.get("keyword") \
                and len([1 for n, _ in results if n == "find_courses"]) == 1:
            args = {k: v for k, v in cond.items() if k not in ("limit", "keyword")}
            return {"content": "", "tool_calls": [{"name": "find_courses", "arguments": {**args, "limit": 30}}]}

        if format is None:
            return {"content": "", "tool_calls": []}
        return {"content": json.dumps(self._final(cond, results), ensure_ascii=False), "tool_calls": []}

    @staticmethod
    def _final(cond, results):
        limit = cond.get("limit", 5)
        candidates, semantic = [], {}
        for name, r in results:
            if name == "find_courses":
                candidates += r.get("courses", [])
            elif name == "semantic_search_syllabus":
                for h in r.get("hits", []):
                    semantic.setdefault(f"{h['code']}_{h['serial']}", h)
        seen, ranked = set(), []
        # 同時符合條件且大綱相關的排最前面，其次是只符合條件的
        for c in sorted(candidates, key=lambda c: (f"{c['code']}_{c['serial']}" not in semantic,
                                                    c.get("p_full") if c.get("p_full") is not None else 0.5)):
            k = f"{c['code']}_{c['serial']}"
            if k in seen:
                continue
            seen.add(k)
            ranked.append(c)
        picks = ranked[:limit]
        suggestions = []
        for c in picks:
            k = f"{c['code']}_{c['serial']}"
            parts = [f"{c.get('time', '時間未定')}"]
            if c.get("p_full") is not None:
                parts.append(f"爆滿機率 {round(c['p_full'] * 100)}%")
            if k in semantic:
                parts.append(f"大綱提到：「{semantic[k]['snippet'][:30]}…」")
            suggestions.append({"code": c["code"], "serial": c["serial"], "reason": "；".join(parts)})
        if suggestions:
            reply = f"依照你的條件找到 {len(ranked)} 門候選課，先列出最符合的 {len(suggestions)} 門。"
        else:
            reply = "照目前的條件找不到符合的課，可以放寬一些條件（例如星期或學分）再試一次。"
        return {"reply": reply, "suggestions": suggestions}

    def embed(self, texts: List[str]) -> List[List[float]]:
        """字元 bigram 雜湊向量：沒有語意理解，只是讓 RAG 流程在離線時也能跑通"""
        vecs = []
        for t in texts:
            v = [0.0] * self.EMBED_DIM
            s = re.sub(r"\s+", "", t)
            for i in range(len(s) - 1):
                h = int(hashlib.md5(s[i:i + 2].encode("utf-8")).hexdigest(), 16)
                v[h % self.EMBED_DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / norm for x in v])
        return vecs


_client_cache: Dict[str, Any] = {}


def get_client(provider: Optional[str] = None, fallback: bool = True):
    """依設定取得用戶端。Ollama 連不上時（fallback=True）退回 MockClient，並在 name 註明。"""
    provider = (provider or AI_PROVIDER).lower()
    if provider == "mock":
        return _client_cache.setdefault("mock", MockClient())
    if "ollama" in _client_cache:
        return _client_cache["ollama"]
    try:
        client = OllamaClient()
        client.ping()
        _client_cache["ollama"] = client
        return client
    except Exception as e:
        if not fallback:
            raise
        log.warning("Ollama 無法使用，改用離線規則模式：%s", e)
        mock = MockClient()
        mock.name = "mock（Ollama 無法使用，已自動改用離線規則模式）"
        mock.fallback_reason = str(e)
        return mock
