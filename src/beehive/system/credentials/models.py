"""按职责组织的 models。"""
from __future__ import annotations
from dataclasses import dataclass
import re


@dataclass
class CredentialRecord:
    id: str
    alias: str
    title: str
    url: str
    username: str
    password: str
    notes: str
    updated_at: str

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "alias": self.alias,
            "title": self.title,
            "url": self.url,
            "username": self.username,
            "updated_at": self.updated_at,
        }

    def agent_list_dict(self) -> dict[str, str]:
        """给 Agent 的列表项：不含账号密码。"""
        return {
            "alias": self.alias,
            "title": self.title,
            "url": self.url,
        }

    def env_prefix(self) -> str:
        return cred_env_prefix(self.alias)

    def to_dict(self) -> dict[str, str]:
        d = self.public_dict()
        d["password"] = self.password
        d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CredentialRecord:
        return cls(
            id=str(d.get("id") or ""),
            alias=str(d.get("alias") or "").strip(),
            title=str(d.get("title") or "").strip(),
            url=str(d.get("url") or "").strip(),
            username=str(d.get("username") or ""),
            password=str(d.get("password") or ""),
            notes=str(d.get("notes") or ""),
            updated_at=str(d.get("updated_at") or ""),
        )


def cred_env_prefix(alias: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]+", "_", (alias or "").strip()).strip("_").upper()
    if not raw:
        raw = "ITEM"
    if raw[0].isdigit():
        raw = "C_" + raw
    return f"WOKBEE_CRED_{raw}"
