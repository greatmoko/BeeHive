"""Provider API Key 的主密钥与加解密适配。"""

from __future__ import annotations

import logging

from wokbee.core.credential_crypto import (
    CredentialVaultError,
    decode_key,
    encode_key,
    generate_key,
    open_sealed,
    seal,
)

_logger = logging.getLogger(__name__)
_master_backend_ref: object | None = None

def _master_backend():
    """复用保险箱的 Keyring 主密钥后端（Windows 凭据管理器 / DPAPI）。"""
    global _master_backend_ref
    if _master_backend_ref is None:
        from wokbee.core.credential_store import KeyringBackend
        _master_backend_ref = KeyringBackend()
    return _master_backend_ref


def _get_master_key() -> bytes | None:
    """取主密钥；缺失则生成并写入系统凭据管理器。不可用时返回 None（降级明文）。"""
    try:
        encoded = _master_backend().get()
        if encoded:
            return decode_key(encoded)
        key = generate_key()
        _master_backend().set(encode_key(key))
        return key
    except Exception as e:  # noqa: BLE001
        _logger.warning("无法访问系统凭据管理器，API Key 将退回明文保存: %s", e)
        return None


def _seal_key(plain: str) -> str:
    """把明文 API Key 封成信封（密文 JSON 文本）；无法加密时降级为明文。"""
    if not plain:
        return ""
    key = _get_master_key()
    if key is None:
        return plain
    try:
        return seal({"v": 1, "s": plain}, key)
    except Exception as e:  # noqa: BLE001
        _logger.warning("API Key 加密失败，退回明文保存: %s", e)
        return plain


def _open_key(blob: str) -> str:
    """解开信封得到明文 API Key；兼容旧版明文；解密失败返回空串（不崩溃）。"""
    if not blob:
        return ""
    if not blob.lstrip().startswith("{"):
        return blob  # 旧版明文
    key = _get_master_key()
    if key is None:
        _logger.warning("凭据管理器不可用，无法解密 API Key，已置空")
        return ""
    try:
        data = open_sealed(blob, key)
        return str(data.get("s") or "")
    except CredentialVaultError as e:
        _logger.warning("解密 API Key 失败，已置空: %s", e)
        return ""



__all__ = ["_master_backend", "_get_master_key", "_seal_key", "_open_key"]
