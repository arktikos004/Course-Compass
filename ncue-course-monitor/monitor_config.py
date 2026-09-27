import os

# --- 課程監控設定 ---
TARGET_YEAR = os.getenv("TARGET_YEAR", "115")
TARGET_SEMESTER = os.getenv("TARGET_SEMESTER", "1")

# 監控的課程代碼。可用環境變數 TARGET_COURSES 以逗號分隔覆寫，換課不必改程式。
TARGET_COURSES = [
    c.strip()
    for c in os.getenv("TARGET_COURSES", "54019,00342").split(",")
    if c.strip()
]

# --- 從環境變數讀取秘密資料 ---
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# 可用環境變數覆寫，方便在本機把它指到會丟包／不含 token 的位址來驗證失敗路徑
BASE_URL = os.getenv("BASE_URL", "https://webap0.ncue.edu.tw/DEANV2/Other/OB010")

# 記錄上一輪各課程的缺額狀態，避免有缺額時每次排程都重複推播
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# (連線, 讀取)。連線 40 秒是關鍵：Linux 的 SYN 重送落在 t=0/1/3/7/15/31/63 秒，
# 原本的 30 秒剛好在第 6 次重送（t=31）前一秒放棄，等於白等 15 秒又丟掉正要發出的那次。
# 這正是本監控會逾時、而 timeout=90 的每日爬蟲從不逾時的原因。
REQUEST_TIMEOUT = (40, 25)

# LINE API 一向秒回，不需要那麼長的連線等待
LINE_TIMEOUT = (10, 20)
