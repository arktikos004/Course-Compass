# 將常用設定匯出到 package namespace，保持向後相容性
from .paths import *
from .crawler import *
from .api import *
from .logging_config import *
from .ai import *

__all__ = [
    # paths
    'PROJECT_ROOT', 'RAW_DATA_DIR', 'PROCESSED_DATA_DIR', 'DICT_DIR', 'WEB_DIR',
    'TEACHER_DICT_PATH', 'TEACHER_DICT_AUTO_PATH', 'TEACHER_HIGH_RISK_PATH',
    'MODELS_DIR', 'SYLLABUS_DIR', 'DOCS_DIR',
    # crawler
    'BASE_URL', 'BASE_DOMAIN', 'START_YEAR', 'START_SEMESTER', 'END_YEAR', 'END_SEMESTER', 'CLS_BRANCH', 'HTML_PARSER',
    'USER_AGENT', 'REQUEST_TIMEOUT', 'REQUEST_DELAY', 'REQUEST_RETRIES',
    # api
    'API_HOST', 'API_PORT',
    # logging
    'LOG_DIR', 'LOG_FILE', 'LOG_LEVEL', 'LOG_FORMAT',
    # ai
    'AI_PROVIDER', 'OLLAMA_HOST', 'OLLAMA_TIMEOUT', 'CHAT_MODEL', 'EMBED_MODEL', 'AGENT_MAX_STEPS',
]
