"""I/O 工具 - 安全的 CSV 讀寫函式"""

import logging
import pandas as pd
from pathlib import Path
from typing import Optional


def safe_read_csv(filepath: Path, encoding: str = 'utf-8-sig') -> Optional[pd.DataFrame]:
    """安全讀取 CSV"""
    try:
        return pd.read_csv(filepath, encoding=encoding)
    except Exception as e:
        logging.error(f"讀取文件失敗 {filepath}: {e}")
        return None


def safe_write_csv(df: pd.DataFrame, filepath: Path, index: bool = False, encoding: str = 'utf-8-sig'):
    """安全寫入 CSV"""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(filepath, index=index, encoding=encoding)
        logging.info(f"成功寫入文件: {filepath}")
    except Exception as e:
        logging.error(f"寫入文件失敗 {filepath}: {e}")
