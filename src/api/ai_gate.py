"""公開模式的門禁：共用密鑰＋限流。

正式網站是靜態的，/api/ai/* 由 Cloudflare Pages Function（functions/api/ai/[[path]].js）
轉發到自架主機，主機再經 Cloudflare Tunnel 對外。設定 AI_SHARED_SECRET 就進入公開模式：

- /api/ai/* 必須帶 X-Backend-Secret，只有 Pages Function 知道，直接打主機網址會被拒絕。
- 使用者 IP 由 Pages Function 放在 X-Client-IP；密鑰正確才相信這個標頭。
- 語言模型與 embedding 會用掉 API 額度，所以呼叫模型的端點依 IP 限流，另有全站每日總量。
  計數放在記憶體、重啟歸零；主機只跑一個程序，這樣就夠了。
- 經 Cloudflare 進來（帶 CF-Ray）的其他路徑一律拒絕：主機只負責 AI，其餘由靜態網站提供，
  不讓外人把主機當成查缺額的跳板。直接在主機上開 localhost 不受影響。

沒設定密鑰（本機開發）時完全不檢查。
"""

import hmac
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Mapping, Optional, Tuple

SECRET_HEADER = "x-backend-secret"
CLIENT_IP_HEADER = "x-client-ip"
AI_PREFIX = "/api/ai/"
# 會呼叫語言模型或 embedding 的端點；status 很便宜，前端每次載入都會打，不限流
LIMITED_PATHS = {"/api/ai/chat", "/api/ai/syllabus-qa", "/api/ai/syllabus-search"}


def secret_ok(provided: Optional[str], secret: str) -> bool:
    if not secret or provided is None:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))


class RateLimiter:
    """每個來源每分鐘、每天各有上限，另有全站每天總量；日期以主機時區換日"""

    def __init__(self, per_minute: int, per_day: int, global_per_day: int, clock: Callable[[], float] = time.time):
        self.per_minute, self.per_day, self.global_per_day = per_minute, per_day, global_per_day
        self.clock = clock
        self._lock = threading.Lock()
        self._day = None
        self._recent = defaultdict(deque)
        self._daily = defaultdict(int)
        self._total = 0

    def check(self, key: str) -> Optional[str]:
        """允許就記一次並回傳 None；超過上限就回傳給使用者看的原因"""
        now = self.clock()
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            if today != self._day:
                self._day, self._total = today, 0
                self._recent.clear()
                self._daily.clear()
            recent = self._recent[key]
            while recent and now - recent[0] >= 60:
                recent.popleft()
            if self._daily[key] >= self.per_day:
                return f"每人每天最多使用 {self.per_day} 次，明天再試"
            if self._total >= self.global_per_day:
                return "今天全站的 AI 使用量已達上限，明天再試"
            if len(recent) >= self.per_minute:
                return "問得太快了，請等一分鐘再試"
            recent.append(now)
            self._daily[key] += 1
            self._total += 1
            return None


def gate(path: str, headers: Mapping[str, str], client_host: Optional[str], secret: str,
         limiter: RateLimiter) -> Optional[Tuple[int, str]]:
    """判斷一個請求能不能放行。放行回傳 None，否則回傳 (HTTP 狀態碼, 說明)。
    headers 的鍵要是小寫（Starlette 的 request.headers 查詢不分大小寫，測試時請用小寫）。"""
    if not secret:
        return None
    if not path.startswith(AI_PREFIX):
        return (404, "not found") if "cf-ray" in headers else None
    if not secret_ok(headers.get(SECRET_HEADER), secret):
        return 403, "forbidden"
    if path in LIMITED_PATHS:
        reason = limiter.check(headers.get(CLIENT_IP_HEADER) or client_host or "unknown")
        if reason:
            return 429, reason
    return None
