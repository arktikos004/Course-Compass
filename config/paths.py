from pathlib import Path

# 專案根目錄
PROJECT_ROOT = Path(__file__).parent.resolve().parent

# 資料路徑
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"
DICT_DIR = PROJECT_ROOT / "data" / "dict"
WEB_DIR = PROJECT_ROOT / "web"
MODELS_DIR = PROJECT_ROOT / "data" / "models"
SYLLABUS_DIR = PROJECT_ROOT / "data" / "syllabus"
DOCS_DIR = PROJECT_ROOT / "docs"

# 字典檔路徑
TEACHER_DICT_PATH = DICT_DIR / "teacher.csv"
TEACHER_DICT_AUTO_PATH = DICT_DIR / "teacher_dict_auto.csv"
TEACHER_HIGH_RISK_PATH = DICT_DIR / "teacher_high_risk.csv"
