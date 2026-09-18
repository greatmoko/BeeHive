"""兼容入口：脚本实现位于 :mod:`script_pipeline`。"""
import sys
from . import script_pipeline as _implementation
sys.modules[__name__] = _implementation
