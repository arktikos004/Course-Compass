"""NCUE 課程缺額監控 - 偵測目標課程出現空位時以 LINE 推播通知。

請求與解析邏輯共用主專案的 src/crawler/ncue_client.py，避免兩份實作各自失準。
"""

import sys
import json
from pathlib import Path

import requests

# 共用主專案的爬蟲底層（純 requests + bs4，不會拉進 pandas）。
# 注意：設定檔命名為 monitor_config 而非 config，因為 src/config.py 會蓋掉同名模組。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from crawler.ncue_client import (  # noqa: E402
    build_session, fetch_course_table, parse_course_table, find_column_index
)

import monitor_config as config  # noqa: E402

# 查詢失敗的兩種性質：暫時性的下一輪排程會自己好，結構性的得有人來看
TRANSIENT = "transient"
STRUCTURAL = "structural"


def send_line_message(message):
    if not config.LINE_TOKEN or not config.LINE_USER_ID:
        print("⚠️ 未設定 LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID，略過推播")
        return False

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_TOKEN}",
    }
    payload = {"to": config.LINE_USER_ID, "messages": [{"type": "text", "text": message}]}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=config.LINE_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        # 推播時的網路抖動不該讓整輪排程 crash
        print(f"❌ LINE 推播失敗（連線）：{type(exc).__name__}: {exc}")
        return False

    if resp.status_code != 200:
        print(f"❌ LINE 推播失敗 ({resp.status_code}): {resp.text}")
        return False

    print("📨 已發送 LINE 通知")
    return True


def load_state():
    try:
        with open(config.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def to_int(value):
    text = str(value).strip()
    return int(text) if text.isdigit() else 0


def fetch_rows(session, course_code):
    """查一門課。回傳 (rows, failure_kind)；failure_kind 為 None 代表這門查成功。"""
    try:
        # 直接用課程代碼查詢，不必每次拉回近兩千筆的全學期表格
        table = fetch_course_table(
            session, config.BASE_URL, config.TARGET_YEAR, config.TARGET_SEMESTER,
            scr_selcode=course_code, html_parser="html.parser",
            timeout=config.REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        # 逾時／連線中斷／重試後仍 5xx。學校站台的問題，不是我們的，下一輪會再試。
        print(f"⚠️ {course_code} 查詢失敗（暫時性）：{type(exc).__name__}: {exc}")
        return None, TRANSIENT
    except RuntimeError as exc:
        # get_request_token 找不到防偽欄位：站台改版，或吐了維護頁面
        print(f"❌ {course_code} 查詢失敗（站台結構異常）：{exc}")
        return None, STRUCTURAL

    if table is None:
        # 請求成功、站台正常回「查無資料」——網路與解析都是活的，不算失敗
        print(f"ℹ️ {course_code} 查無資料（代碼可能有誤或該學期尚未開放）")
        return [], None

    headers, rows = parse_course_table(table)
    # 依表頭名稱定位欄位，避免硬編索引在站台改版時默默錯位
    if find_column_index(headers, "上限人數") is None or find_column_index(headers, "登記人數") is None:
        print(f"❌ 表格缺少人數欄位，站台可能又改版了：{headers}")
        return None, STRUCTURAL

    return rows, None


def check_ncue_course():
    """跑完一輪監控。回傳 (成功門數, [(課程代碼, 失敗性質), ...])"""
    session = build_session()
    state = load_state()
    ok_count = 0
    failures = []

    for course_code in config.TARGET_COURSES:
        print(f"🌐 查詢 {config.TARGET_YEAR}-{config.TARGET_SEMESTER} 課程 {course_code} ...")

        rows, kind = fetch_rows(session, course_code)
        if kind is not None:
            # 查失敗的課不動 state，保留上一輪的狀態，避免誤判成「缺額消失了」
            failures.append((course_code, kind))
            continue

        ok_count += 1
        for row in rows:
            if row.get("課程代碼") != course_code:
                continue

            name = row.get("課程名稱", "")
            serial = row.get("序號", "")
            max_n = to_int(row.get("上限人數"))
            reg_n = to_int(row.get("登記人數"))
            has_vacancy = max_n > reg_n

            key = f"{config.TARGET_YEAR}-{config.TARGET_SEMESTER}-{course_code}-{serial}"
            was_vacant = state.get(key, {}).get("has_vacancy", False)

            print(f"📖 [{course_code}] {name} - 登記/上限: {reg_n}/{max_n}"
                  f"{' ✅有缺額' if has_vacancy else ' ❌額滿'}")

            # 只在「額滿 → 有缺額」的轉換時通知，避免每輪排程重複轟炸
            if has_vacancy and not was_vacant:
                pushed = send_line_message(
                    f"🎯 發現缺額！\n{name} ({course_code})\n"
                    f"登記/上限：{reg_n}/{max_n}\n"
                    f"學期：{config.TARGET_YEAR}-{config.TARGET_SEMESTER}"
                )
                # 推播沒送出去就不能把狀態記成已通知，否則下一輪會判定「無變化」，
                # 這個缺額從此再也不會提醒。未設定 LINE 憑證屬預期情況，不在此列。
                if not pushed and config.LINE_TOKEN and config.LINE_USER_ID:
                    print(f"↩️ {course_code} 推播未成功，保留舊狀態，下一輪會重試")
                    continue

            state[key] = {"has_vacancy": has_vacancy, "name": name,
                          "registered": reg_n, "limit": max_n}

    # 無條件寫回：一份 1KB 的 JSON 不值得為了省 I/O 而多維護一個 changed 旗標，
    # 而且保證檔案存在，cache/save 就不會遇到路徑不存在的狀況。
    save_state(state)
    print(f"💾 狀態已更新：{config.STATE_FILE}")

    return ok_count, failures


if __name__ == "__main__":
    if not config.TARGET_COURSES:
        print("❌ 未設定任何監控課程（TARGET_COURSES）")
        sys.exit(1)

    ok_count, failures = check_ncue_course()
    structural = [code for code, kind in failures if kind == STRUCTURAL]

    if structural:
        # 站台改版／解析失效：不會自己好，必須亮紅燈叫人來看
        print(f"❌ {len(structural)} 門課出現站台結構異常：{', '.join(structural)}")
        sys.exit(1)

    if failures:
        # 純粹是站台連不上。GitHub 排程實際間隔 20–50 分鐘，下一輪自然會補上；
        # 為了學校站台抖一下就每天亮十幾次紅燈，只會讓人開始無視這個 workflow。
        print(f"⚠️ 本輪有 {len(failures)}/{len(config.TARGET_COURSES)} 門課暫時性失敗，"
              f"成功 {ok_count} 門；下一輪排程會重試")

    sys.exit(0)
