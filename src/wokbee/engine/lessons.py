"""兼容入口：经验实现位于 :mod:`lesson_service`。"""

import sys

from . import lesson_service as _implementation

sys.modules[__name__] = _implementation
