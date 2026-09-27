import logging
import pandas as pd
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import re
from datetime import datetime

from config import LOG_LEVEL, LOG_FORMAT, LOG_FILE, LOG_DIR

def setup_logging():
    """初始化日誌"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE, encoding='utf-8')
        ]
    )

def extract_year_semester_from_filename(filepath: Path) -> Tuple[Optional[str], Optional[str]]:
    """從檔名擷取學年度與學期"""
    filename = filepath.name
    match = re.search(r'courses_(\d{3})_(\d)', filename)
    if not match:
        return None, None
    return match.group(1), match.group(2)

from .io import safe_read_csv, safe_write_csv

def get_timestamp() -> str:
    """獲取當前時間戳"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def validate_dataframe_columns(df: pd.DataFrame, required_columns: List[str]) -> bool:
    """驗證 DataFrame 是否包含所需列"""
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        logging.error(f"DataFrame 缺少必要列: {missing_columns}")
        return False
    return True