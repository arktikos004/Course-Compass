"""列印解析後的設定屬性，確認是否載入專案頂層的 config package"""

import sys
from pathlib import Path
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(1, str(BASE_DIR / 'src'))
import config
print('config file:', getattr(config, '__file__', None))
print('has PROCESSED_DATA_DIR:', hasattr(config, 'PROCESSED_DATA_DIR'))
print('attrs sample:', [a for a in dir(config) if not a.startswith('_')][:60])
