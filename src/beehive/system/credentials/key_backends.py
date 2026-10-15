"""按职责组织的 key_backends。"""
from __future__ import annotations
from typing import Protocol
from beehive.system.credentials.credential_crypto import CredentialVaultError
KEYRING_SERVICE = "WokBee.Vault"
KEYRING_USER = "master-key"


class KeyBackend(Protocol):
    def get(self) -> str | None: ...
    def set(self, encoded_key: str) -> None: ...


class KeyringBackend:
    """jaraco/keyring，Windows 上落到凭据管理器。"""

    def get(self) -> str | None:
        import keyring

        try:
            val = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception as e:  # noqa: BLE001
            raise CredentialVaultError(f"读取系统凭据失败：{e}") from e
        return val or None

    def set(self, encoded_key: str) -> None:
        import keyring

        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, encoded_key)
        except Exception as e:  # noqa: BLE001
            raise CredentialVaultError(f"写入系统凭据失败：{e}") from e


class MemoryKeyBackend:
    """测试用内存主密钥，不碰系统凭据管理器。"""

    def __init__(self, encoded_key: str | None = None):
        self._key = encoded_key

    def get(self) -> str | None:
        return self._key

    def set(self, encoded_key: str) -> None:
        self._key = encoded_key
