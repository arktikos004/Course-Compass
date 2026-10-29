import os

# API 相關設定（可用環境變數或 .env 覆寫；自架主機上若 8000 已被其他服務占用，改 API_PORT）
API_HOST = os.getenv("API_HOST", "localhost")
API_PORT = int(os.getenv("API_PORT", "8000"))
