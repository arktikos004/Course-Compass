"""教師辭典構建器 - 分析原始課程資料以擷取教師姓名片段"""

import pandas as pd
from pathlib import Path
from typing import Set, List, Optional
import logging

from config import RAW_DATA_DIR, DICT_DIR, TEACHER_DICT_AUTO_PATH, TEACHER_HIGH_RISK_PATH
from utils.common import safe_read_csv, safe_write_csv

class TeacherDictBuilder:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    @staticmethod
    def safe_split(text: str) -> List[str]:
        """處理明確分隔符與校際教師"""
        text = str(text).strip()
        if text in ["校際教師", "校外教師"]:
            return ["校際教"]
        return [text]

    @staticmethod
    def extract_single_teacher_set(df: pd.DataFrame) -> Set[str]:
        """建立已確認的三字教師集合（來源：僅有一位老師的課程）"""
        single_set = set()
        for t in df["教師姓名"].dropna():
            parts = TeacherDictBuilder.safe_split(t)
            if len(parts) == 1 and len(parts[0]) == 3:
                single_set.add(parts[0])
        return single_set

    @staticmethod
    def _process_buffer(buffer: str) -> Optional[List[str]]:
        """處理累積的 buffer，返回教師列表或 None"""
        if not buffer:
            return []
        if len(buffer) == 2:
            return [buffer]
        elif len(buffer) % 3 == 0:
            return [buffer[k:k+3] for k in range(0, len(buffer), 3)]
        else:
            return None

    @staticmethod
    def smart_split_preserve_order(name: str, single_set: Set[str]) -> Optional[List[str]]:
        """使用 DP 標記已知教師位置，依序處理並結算 Buffer"""
        s = name
        n = len(s)
        dp = [0] * (n + 1)
        take = [False] * (n + 1)

        for i in range(n - 1, -1, -1):
            best = dp[i + 1]
            best_take = False
            if i + 3 <= n:
                chunk = s[i:i + 3]
                if chunk in single_set:
                    cand = 3 + dp[i + 3]
                    if cand > best:
                        best = cand
                        best_take = True

            dp[i] = best
            take[i] = best_take

        resolved = []
        buffer = ""
        i = 0

        while i < n:
            if take[i]:
                if buffer:
                    buffer_result = TeacherDictBuilder._process_buffer(buffer)
                    if buffer_result is None:
                        return None
                    resolved.extend(buffer_result)
                    buffer = ""
                resolved.append(s[i:i+3])
                i += 3
            else:
                buffer += s[i]
                i += 1

        if buffer:
            buffer_result = TeacherDictBuilder._process_buffer(buffer)
            if buffer_result is None:
                return None
            resolved.extend(buffer_result)

        return resolved

    def load_all_raw_data(self, input_dir: Path) -> pd.DataFrame:
        """讀取指定目錄下所有 courses_*.csv 並合併"""
        csv_files = sorted(input_dir.glob("courses_*.csv"))
        if not csv_files:
            self.logger.warning(f"在 {input_dir} 找不到任何 courses_*.csv 檔案")
            return pd.DataFrame()

        dfs = []
        for f in csv_files:
            df = safe_read_csv(f)
            if df is not None and "教師姓名" in df.columns:
                dfs.append(df)
            else:
                self.logger.warning(f"跳過 {f.name}: 無 '教師姓名' 欄位")

        if not dfs:
            return pd.DataFrame()
        return pd.concat(dfs, ignore_index=True)

    def build_teacher_dict(self) -> None:
        """構建教師字典"""
        self.logger.info("正在讀取原始資料...")
        df = self.load_all_raw_data(RAW_DATA_DIR)

        if df.empty:
            self.logger.error("沒有資料，程式結束。")
            return

        DICT_DIR.mkdir(parents=True, exist_ok=True)
        single_set = self.extract_single_teacher_set(df)
        self.logger.info(f"已知三字教師庫大小: {len(single_set)}")

        resolved_set = set()
        resolved_set.update(single_set)
        high_risk = set()

        for t in df["教師姓名"].dropna():
            raw_parts = self.safe_split(t)
            for name in raw_parts:
                if name in resolved_set:
                    continue
                split_result = self.smart_split_preserve_order(name, single_set)
                if split_result:
                    resolved_set.update(split_result)
                else:
                    high_risk.add(name)

        teacher_df = pd.DataFrame(sorted(resolved_set), columns=["teacher_name"])
        teacher_df.insert(0, "teacher_id", [f"T{idx:03d}" for idx in range(1, len(teacher_df) + 1)])
        teacher_df["alias"] = ""

        safe_write_csv(teacher_df, TEACHER_DICT_AUTO_PATH)

        risk_df = pd.DataFrame(sorted(high_risk), columns=["teacher_name"])
        safe_write_csv(risk_df, TEACHER_HIGH_RISK_PATH)

        self.logger.info("自動教師辭典輸出完成")
        self.logger.info(f"自動確認教師數：{len(teacher_df)}")
        self.logger.info(f"高風險教師數（需人工）：{len(risk_df)}")

def main():
    """主函數"""
    from utils.common import setup_logging
    setup_logging()

    builder = TeacherDictBuilder()
    builder.build_teacher_dict()

if __name__ == "__main__":
    main()