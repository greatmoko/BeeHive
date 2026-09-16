"""BeeHive 统一错误类型体系。"""

from __future__ import annotations

from typing import Any


class WokBeeError(Exception):
    """所有 BeeHive 业务异常的基类。"""


class AIError(WokBeeError):
    """AI 调用相关错误：网络失败、API 异常、模型不可用等。

    附带可选的归一化分类 ``kind``（见 ``wokbee.engine.ai_errors.AIErrorKind``）。
    ``kind`` 为 None 时表示尚未分类，调用方可按需 ``classify_error``。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Any = None,
        status_code: int | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        # 直接属性穿越（异常是可哈希对象，属性赋值安全）
        self.message = message
        self.kind = kind
        self.status_code = status_code
        self.code = code

    def classify(self):
        """惰性返回归一化分类；未设分类时现场归类。"""
        if self.kind is not None:
            return self.kind
        try:
            from wokbee.engine.ai_errors import classify_error

            self.kind = classify_error(self.__cause__ or self) if (self.__cause__ is not None) else classify_error(self)
            return self.kind
        except Exception:
            return None

    @property
    def ai_kind(self) -> Any:
        return self.kind


class StorageError(WokBeeError):
    """数据持久化错误：JSON 损坏、写入失败等。"""
