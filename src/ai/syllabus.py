"""下載教學大綱 PDF、抽出文字、清掉樣板雜訊並依段落切塊。

- 只處理單一學期（預設為資料中最新學期），每秒最多 1 個請求，已下載的檔案不重抓
- 文字直接讀 PDF 文字層（pypdfium2），不做 OCR；讀不到文字的份數會記在 manifest 與涵蓋率統計
"""

import glob
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import PROCESSED_DATA_DIR, SYLLABUS_DIR

DOWNLOAD_DELAY_SEC = 1.0  # 大綱 PDF 逐份下載，每秒最多一個請求

log = logging.getLogger(__name__)

# 段落標題 → 統一的段落名稱（順序即優先度）
SECTION_PATTERNS: List[Tuple[str, str]] = [
    (r"教學目標[：:]", "教學目標"),
    (r"教學大綱[：:]", "教學大綱"),
    (r"必讀經典或名著[：:]", "教材"),
    (r"☆\s*主要教材[：:]", "教材"),
    (r"☆\s*參考教材[：:]", "教材"),
    (r"☆\s*(必修|建議)先導課程[：:]", "先導課程"),
    (r"教學方法[：:]", "教學方法"),
    (r"評量方式項目|評量方式[：:]", "評量方式"),
    (r"課程對核心能力的幫助[：:]|核心能力項目", "核心能力"),
    (r"教學內容與進度[：:]", "教學進度"),
]
SECTION_RE = re.compile("|".join(f"(?:{p})" for p, _ in SECTION_PATTERNS))


def manifest_path(year: int, semester: int) -> Path:
    return SYLLABUS_DIR / f"manifest_{year}-{semester}.csv"


def pdf_dir(year: int, semester: int) -> Path:
    return SYLLABUS_DIR / "pdf" / f"{year}{semester}"


def latest_semester_courses(year: Optional[int] = None, semester: Optional[int] = None) -> Tuple[pd.DataFrame, int, int]:
    files = sorted(PROCESSED_DATA_DIR.glob("all_courses_*.csv"))
    if not files:
        raise RuntimeError("找不到 processed 資料，請先執行 python main.py process")
    df = pd.read_csv(files[-1], encoding="utf-8-sig", low_memory=False, dtype={"課程代碼": str})
    if year is None or semester is None:
        year = int(df["學年度"].max())
        semester = int(df[df["學年度"] == year]["學期"].max())
    cur = df[(df["學年度"] == year) & (df["學期"] == semester)].drop_duplicates(["課程代碼", "序號"])
    return cur, year, semester


def extract_text(pdf_file: Path) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_file))
    try:
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            pages.append(tp.get_text_range())
            tp.close()
            page.close()
        return "\n".join(pages)
    finally:
        pdf.close()


def clean_text(raw: str) -> str:
    t = raw.replace("\r\n", "\n").replace("\r", "\n")
    # 每頁重複的頁首
    t = re.sub(r"國立彰化師範大學\s*\d+學年度\s*第\d學期\s*\n?\s*課程大綱暨教學計畫表", "", t)
    # 問卷類型、教學型態的選項清單：所有大綱都一樣，對搜尋只有干擾
    t = re.sub(r"教學意見反應問卷類型[：:].*?(?=本課程學習融入議題|教學目標[：:])", "", t, flags=re.S)
    t = re.sub(r"＜註:[^＞]*＞", "", t)
    # 直書表格被拆成一字一行：把連續的單字行接回來
    lines = [ln.strip() for ln in t.split("\n")]
    merged, buf = [], ""
    for ln in lines:
        if not ln:
            continue
        if len(ln) <= 2:
            buf += ln
            continue
        if buf:
            merged.append(buf)
            buf = ""
        merged.append(ln)
    if buf:
        merged.append(buf)
    t = "\n".join(merged)
    t = re.sub(r"[ \t　]+", " ", t)
    return t.strip()


def split_sections(text: str) -> List[Tuple[str, str]]:
    """依段落標題切開，回傳 [(段落名稱, 內容)]；第一個標題前的內容歸為「基本資料」"""
    parts: List[Tuple[str, str]] = []
    last_pos, last_name = 0, "基本資料"
    for m in SECTION_RE.finditer(text):
        body = text[last_pos:m.start()].strip()
        if body:
            parts.append((last_name, body))
        name = next(n for p, n in SECTION_PATTERNS if re.match(p, m.group(0)))
        last_pos, last_name = m.end(), name
    tail = text[last_pos:].strip()
    if tail:
        parts.append((last_name, tail))
    return parts


def chunk_text(text: str, size: int = 500, overlap: int = 80) -> List[Tuple[str, str]]:
    """段落內再切成約 size 字的塊，相鄰塊重疊 overlap 字，避免關鍵句剛好被切斷"""
    out = []
    for section, body in split_sections(text):
        body = re.sub(r"\n+", "\n", body)
        if len(body) < 10:
            continue
        start = 0
        while start < len(body):
            out.append((section, body[start:start + size]))
            if start + size >= len(body):
                break
            start += size - overlap
    return out


def _download_session():
    """沿用爬蟲的 build_session（瀏覽器 UA＋連線層重試）。
    學校憑證缺 Subject Key Identifier：Python 3.13 起預設開啟 X.509 strict 檢查會拒絕，
    這裡只關掉 strict 旗標，憑證鏈與主機名稱照常驗證（CI 用的 3.11 不受影響）。"""
    import ssl

    from requests.adapters import HTTPAdapter

    from crawler.ncue_client import build_session

    session = build_session()
    retry = session.get_adapter("https://").max_retries

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = ssl.create_default_context()
            ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
            kwargs["ssl_context"] = ctx
            return super().init_poolmanager(*args, **kwargs)

    session.mount("https://", _Adapter(max_retries=retry))
    return session


def fetch_syllabi(year: Optional[int] = None, semester: Optional[int] = None, limit: Optional[int] = None) -> pd.DataFrame:
    cur, year, semester = latest_semester_courses(year, semester)
    cur = cur[cur["教學大綱連結"].astype(str).str.startswith("http")]
    out_dir = pdf_dir(year, semester)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = _download_session()

    rows, downloaded = [], 0
    for r in cur.itertuples(index=False):
        url = str(r.教學大綱連結)
        f = out_dir / url.rsplit("/", 1)[-1]
        status = "cached" if f.exists() and f.stat().st_size > 0 else "pending"
        if status == "pending" and (limit is None or downloaded < limit):
            try:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
                f.write_bytes(resp.content)
                status = "downloaded"
            except Exception as e:
                status = f"error: {e}"[:200]
            downloaded += 1
            time.sleep(DOWNLOAD_DELAY_SEC)
        n_chars = 0
        if f.exists() and f.stat().st_size > 0:
            try:
                n_chars = len(clean_text(extract_text(f)))
            except Exception as e:
                status = f"unreadable: {e}"[:200]
        rows.append({"課程代碼": r.課程代碼, "序號": int(r.序號), "課程名稱": r.課程名稱, "url": url,
                     "file": f.name if f.exists() else "", "status": status, "n_chars": n_chars})
    manifest = pd.DataFrame(rows)
    SYLLABUS_DIR.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path(year, semester), index=False, encoding="utf-8-sig")
    return manifest


def main(semester_label: Optional[str] = None, limit: Optional[int] = None):
    from utils.common import setup_logging

    setup_logging()
    year = semester = None
    if semester_label:
        y, s = semester_label.split("-")
        year, semester = int(y), int(s)
    manifest = fetch_syllabi(year, semester, limit)
    ok = manifest["n_chars"] > 50
    total_courses = len(latest_semester_courses(year, semester)[0])
    print(f"大綱連結 {len(manifest)} 份（該學期共 {total_courses} 門課）；可讀文字 {ok.sum()} 份；"
          f"狀態：{manifest['status'].str.split(':').str[0].value_counts().to_dict()}")
