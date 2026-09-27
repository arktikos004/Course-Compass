"""課程爬蟲模組 - 爬取校內課程列表並將原始資料寫入 RAW_DATA_DIR"""

import time
import logging
from typing import List, Tuple, Optional

import pandas as pd
from bs4 import BeautifulSoup

from config import (
    BASE_URL, BASE_DOMAIN, RAW_DATA_DIR,
    START_YEAR, START_SEMESTER, END_YEAR, END_SEMESTER, CLS_BRANCH, HTML_PARSER,
    USER_AGENT, REQUEST_TIMEOUT, REQUEST_DELAY, REQUEST_RETRIES
)
from utils.common import safe_write_csv
from .ncue_client import build_session, fetch_course_table, parse_course_table


class CourseCrawler:
    def __init__(self):
        # 本類別已有自己的重試迴圈（REQUEST_RETRIES + REQUEST_DELAY），且該迴圈連
        # RuntimeError 都會重試，是 session 層做不到的。這裡關掉 session 層重試，
        # 避免 3 次 × 3 次 = 9 趟往返打站台。
        self.session = build_session(USER_AGENT, retries=0)
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def next_semester(year: int, semester: int) -> Tuple[int, int]:
        """回傳下一個學期"""
        if semester == 1:
            return year, 2
        else:
            return year + 1, 1

    @staticmethod
    def generate_semester_range() -> List[Tuple[int, int]]:
        """產生學期區間"""
        result = []
        y, s = START_YEAR, START_SEMESTER

        while True:
            result.append((y, s))
            if y == END_YEAR and s == END_SEMESTER:
                break
            y, s = CourseCrawler.next_semester(y, s)

        return result

    def fetch_course_table(self, year: int, semester: int, cls_branch: str = "") -> Optional[BeautifulSoup]:
        """獲取課程表格，失敗時重試"""
        last_error = None

        for attempt in range(REQUEST_RETRIES + 1):
            try:
                table = fetch_course_table(
                    self.session, BASE_URL, year, semester,
                    cls_branch=cls_branch, html_parser=HTML_PARSER,
                    timeout=REQUEST_TIMEOUT,
                )
                if table is not None:
                    return table
                # 查無資料（例如尚未開放的學期）不需重試
                return None
            except Exception as e:
                last_error = e
                if attempt < REQUEST_RETRIES:
                    self.logger.warning(f"{year}-{semester} 查詢失敗（第 {attempt + 1} 次）：{e}，稍後重試")
                    time.sleep(REQUEST_DELAY)

        raise RuntimeError(f"取得 {year}-{semester} 課程資料失敗：{last_error}")

    @staticmethod
    def parse_course_table(table: BeautifulSoup):
        return parse_course_table(table, BASE_DOMAIN)

    def crawl_semester(self, year: int, semester: int) -> bool:
        """爬取單一學期的課程數據"""
        try:
            self.logger.info(f"開始爬取 {year}-{semester}")
            table = self.fetch_course_table(year, semester, CLS_BRANCH)

            if table is None:
                self.logger.warning(f"{year}-{semester} 查無資料（該學期可能尚未開放）")
                return False

            headers, data = self.parse_course_table(table)

            if not data:
                self.logger.warning(f"{year}-{semester} 查無資料")
                return False

            filename = f"courses_{year}_{semester}.csv"
            filepath = RAW_DATA_DIR / filename
            safe_write_csv(pd.DataFrame(data, columns=headers), filepath)
            self.logger.info(f"成功儲存 {year}-{semester}: {len(data)} 筆資料")
            return True

        except Exception as e:
            self.logger.error(f"爬取 {year}-{semester} 失敗: {e}")
            return False

    def crawl_all_semesters(self, only_latest: Optional[int] = None) -> None:
        """爬取所有學期的課程數據

        only_latest: 只爬最後 N 個學期。過去學期資料已凍結，排程更新時不需重打整段區間。
        """
        semesters = self.generate_semester_range()
        if only_latest:
            semesters = semesters[-only_latest:]

        self.logger.info(f"準備爬取學期: {semesters}")

        RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

        for idx, (year, semester) in enumerate(semesters):
            if idx > 0:
                time.sleep(REQUEST_DELAY)
            self.crawl_semester(year, semester)


def main(only_latest: Optional[int] = None):
    from utils.common import setup_logging
    setup_logging()

    crawler = CourseCrawler()
    crawler.crawl_all_semesters(only_latest=only_latest)


if __name__ == "__main__":
    main()
