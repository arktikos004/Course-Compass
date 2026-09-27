"""載入專案根目錄下的 config package，避免遞迴與名稱衝突"""
from pathlib import Path
import importlib.util
import sys

this_file = Path(__file__).resolve()
project_root = this_file.parent.parent
pkg_init = project_root / 'config' / '__init__.py'

spec = importlib.util.spec_from_file_location(
    '_project_config',
    str(pkg_init)
)
_project_config = importlib.util.module_from_spec(spec)

sys.modules['_project_config'] = _project_config
spec.loader.exec_module(_project_config)
for _k, _v in vars(_project_config).items():
    if not _k.startswith('_'):
        globals()[_k] = _v

__all__ = [k for k in globals().keys() if not k.startswith('_')]
