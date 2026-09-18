"""运行环境数据模型与序列化。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class RuntimeEnv:
    """本机环境快照。

    ``cwd`` 是软件进程的启动目录；项目目录在 Agent 运行时通过
    ``project_root`` 叠加，二者语义不同，不能把 ``cwd`` 当成 Agent 工作目录。
    """

    os_name: str = ""
    os_release: str = ""
    machine: str = ""
    cwd: str = ""
    project_root: str = ""
    python_exe: str = ""
    python_version: str = ""
    comspec: str = ""
    pwsh_exe: str = ""
    pwsh_version: str = ""
    powershell_exe: str = ""
    powershell_version: str = ""
    encoding: str = ""
    path_preview: str = ""
    tool_paths: dict[str, str] = field(default_factory=dict)
    tool_versions: dict[str, str] = field(default_factory=dict)
    probed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "os_name": self.os_name,
            "os_release": self.os_release,
            "machine": self.machine,
            "cwd": self.cwd,
            "python_exe": self.python_exe,
            "python_version": self.python_version,
            "comspec": self.comspec,
            "pwsh_exe": self.pwsh_exe,
            "pwsh_version": self.pwsh_version,
            "powershell_exe": self.powershell_exe,
            "powershell_version": self.powershell_version,
            "encoding": self.encoding,
            "path_preview": self.path_preview,
            "tool_paths": dict(self.tool_paths),
            "tool_versions": dict(self.tool_versions),
            "probed_at": self.probed_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> RuntimeEnv:
        return cls(
            os_name=str(raw.get("os_name") or ""),
            os_release=str(raw.get("os_release") or ""),
            machine=str(raw.get("machine") or ""),
            cwd=str(raw.get("cwd") or ""),
            python_exe=str(raw.get("python_exe") or ""),
            python_version=str(raw.get("python_version") or ""),
            comspec=str(raw.get("comspec") or ""),
            pwsh_exe=str(raw.get("pwsh_exe") or ""),
            pwsh_version=str(raw.get("pwsh_version") or ""),
            powershell_exe=str(raw.get("powershell_exe") or ""),
            powershell_version=str(raw.get("powershell_version") or ""),
            encoding=str(raw.get("encoding") or ""),
            path_preview=str(raw.get("path_preview") or ""),
            tool_paths=dict(raw.get("tool_paths") or {}),
            tool_versions=dict(raw.get("tool_versions") or {}),
            probed_at=str(raw.get("probed_at") or ""),
        )

    def with_project_root(self, project_root: str | Path | None) -> RuntimeEnv:
        root = str(Path(project_root).resolve()) if project_root else self.cwd
        return RuntimeEnv(
            os_name=self.os_name,
            os_release=self.os_release,
            machine=self.machine,
            cwd=self.cwd,
            project_root=root,
            python_exe=self.python_exe,
            python_version=self.python_version,
            comspec=self.comspec,
            pwsh_exe=self.pwsh_exe,
            pwsh_version=self.pwsh_version,
            powershell_exe=self.powershell_exe,
            powershell_version=self.powershell_version,
            encoding=self.encoding,
            path_preview=self.path_preview,
            tool_paths=dict(self.tool_paths),
            tool_versions=dict(self.tool_versions),
            probed_at=self.probed_at,
        )

    def powershell_argv_for_file(self, script_path: str | Path) -> list[str]:
        """运行 .ps1：优先 pwsh，无 pwsh 时回退 Windows PowerShell。"""
        script = str(script_path)
        if self.pwsh_exe:
            return [
                self.pwsh_exe,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script,
            ]
        exe = self.powershell_exe or "powershell"
        return [
            exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
        ]

