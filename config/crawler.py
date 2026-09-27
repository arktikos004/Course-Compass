# 爬蟲相關設定
BASE_URL = "https://webap0.ncue.edu.tw/DEANV2/Other/OB010"
BASE_DOMAIN = "https://webap0.ncue.edu.tw"
START_YEAR = 111
START_SEMESTER = 1
END_YEAR = 115
END_SEMESTER = 1
CLS_BRANCH = ""
HTML_PARSER = "lxml"

# 網站已從 ASP.NET WebForms 改版為 MVC，需帶瀏覽器 UA 與 AJAX 標頭才會回傳結果表格
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 90
REQUEST_DELAY = 2.0  # 各學期之間的禮貌延遲（秒）
REQUEST_RETRIES = 2
