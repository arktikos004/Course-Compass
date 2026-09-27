import os

from .paths import PROJECT_ROOT

# 專案根目錄的 .env（不進版控）可以放 API 金鑰；沒有安裝 python-dotenv 時只讀環境變數
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# AI 相關設定（皆可用環境變數覆寫）
# AI_PROVIDER:
#   "api"    走雲端的 OpenAI 相容 API（OpenAI、Gemini、Groq、OpenRouter…），本機不用跑模型
#   "ollama" 走本機 Ollama
#   "mock"   用規則式替身，沒有 GPU / Ollama / 金鑰也能展示完整流程
# 沒有指定時：有設定 LLM_API_KEY 就用 api，否則用 ollama（連不上會自動退回 mock）
AI_PROVIDER = os.getenv("AI_PROVIDER") or ("api" if os.getenv("LLM_API_KEY") else "ollama")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:7b")     # 中文能力佳、支援 tool calling（需先 ollama pull）
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")       # 多語 embedding，用於教學大綱語意搜尋
AGENT_MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "5"))

# OpenAI 相容 API（AI_PROVIDER=api）。模型名稱依服務商而定，範例見 .env.example
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_CHAT_MODEL = os.getenv("LLM_API_CHAT_MODEL", "")
LLM_API_EMBED_MODEL = os.getenv("LLM_API_EMBED_MODEL", "")  # 留空：只用 API 聊天，大綱索引沿用既有的
LLM_API_TIMEOUT = float(os.getenv("LLM_API_TIMEOUT", "60"))
