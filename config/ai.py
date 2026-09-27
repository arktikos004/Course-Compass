import os

# AI 相關設定（皆可用環境變數覆寫）
# AI_PROVIDER: "ollama" 走本機 Ollama；"mock" 用規則式替身，沒有 GPU / Ollama 也能展示完整流程
AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")     # 中文能力佳、支援 tool calling（需先 ollama pull）
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")       # 多語 embedding，用於教學大綱語意搜尋
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "5"))
