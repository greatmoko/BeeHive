"""项目磁盘目录套约。

新目录结构（v2，仅新项目使用；旧项目目录不做迁移）：
- archives/    归档快照（每次归档一个 arch_时间戳/；Agent 禁止访问）
- uploads/     用户上传文件（同名加时间后缀不覆盖；长期保留，归档不清空）
- output/      项目交付物（最终成果必须写在这里；归档时复制后清空）
- memory/      记忆与事件：events.jsonl（事件日志）、exp_时间戳.md（经验）、
               pipeline.json（管线）、复用脚本与技能快照直接存放
- .project/    系统维护：project.json（项目配置）+ Agent.md（用户自定义项目规则）
"""

from __future__ import annotations

from pathlib import Path


_APP_SOURCE_ROOT = Path(__file__).resolve().parents[5]

# 每个项目目录下的标准子目录
PROJECT_SUBDIRS = (
    "archives",
    "uploads",
    "output",
    "memory",
    ".project",
)

PROJECT_CONFIG_DIR = ".project"
PROJECT_META_REL = ".project/project.json"
AGENT_RULES_REL = ".project/Agent.md"
PROJECT_META = PROJECT_META_REL  # 兼容旧名：现指向 .project/project.json
LEGACY_PROJECT_META = "project.json"  # 旧版项目元数据位置（只读兼容）
EVENTS_REL = "memory/events.jsonl"
ARCHIVES_DIR = "archives"
MEMORY_DIR = "memory"
DELIVERABLES_DIR = "output"
UPLOADS_DIR = "uploads"
# 旧版目录（仅历史项目存在，新项目不再创建）
LEGACY_SCRIPTS_DIR = "scripts"
LEGACY_RUNS_DIR = "runs"
LEGACY_WORKSPACE_DIR = "workspace"

# 归档时复制后清空的目录（根目录散落的临时材料由归档逻辑单独处理；
# archives / memory / uploads / .project 不随会话归档清空）
ARCHIVABLE_DIRS = ("output",)


def project_dir(workspace_root: Path, project_id: str) -> Path:
    return workspace_root / project_id


def ensure_project_layout(root: Path) -> None:
    """创建项目根目录及标准子目录（不清理、不覆盖已有内容）。"""
    root = Path(root).expanduser()
    if root.resolve() == _APP_SOURCE_ROOT:
        raise ValueError(
            "不能在 BeeHive 应用源码目录创建 WokBee 项目。"
            "请在 WokBee 设置中选择专用工作文件夹。"
        )
    root.mkdir(parents=True, exist_ok=True)
    for name in PROJECT_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    # 交付物目录说明。
    readme = root / DELIVERABLES_DIR / "README.txt"
    if not readme.exists():
        try:
            readme.write_text(
                "本目录存放项目交付物（最终成果）。\n"
                "归档时会随本次会话一并归档并清空。\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    ureadme = root / UPLOADS_DIR / "README.txt"
    if not ureadme.exists():
        try:
            ureadme.write_text(
                "本目录存放用户上传的文件，Agent 可直接读取调用。\n"
                "若有同名或内容相近的多份文件，默认以修改时间最新的一份为准。\n"
                "归档时**不会**归档本目录，上传资料会长期保留。\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    # 用户自定义项目规则文件：新建为空文件，已有内容不覆盖。
    agent_rules = root / AGENT_RULES_REL
    if not agent_rules.exists():
        try:
            agent_rules.parent.mkdir(parents=True, exist_ok=True)
            agent_rules.write_text("", encoding="utf-8")
        except OSError:
            pass


def meta_path(project_root: Path) -> Path:
    """项目元数据：新项目位于 .project/project.json。"""
    return project_root / PROJECT_META_REL


def legacy_meta_path(project_root: Path) -> Path:
    """旧版项目元数据位置（根目录 project.json，只读兼容）。"""
    return project_root / LEGACY_PROJECT_META


def agent_rules_path(project_root: Path) -> Path:
    """用户自定义项目规则文件（.project/Agent.md）。"""
    return project_root / AGENT_RULES_REL


def events_path(project_root: Path) -> Path:
    return project_root / EVENTS_REL


def artifacts_dir(project_root: Path) -> Path:
    """兼容旧名（历史遗留，已不新建）；新代码请用 deliverables_dir。"""
    return project_root / "artifacts"


def deliverables_dir(project_root: Path) -> Path:
    return project_root / DELIVERABLES_DIR


def uploads_dir(project_root: Path) -> Path:
    return project_root / UPLOADS_DIR


def memory_dir(project_root: Path) -> Path:
    return project_root / MEMORY_DIR


def workspace_sandbox(project_root: Path) -> Path:
    """已废弃：新项目不再有 workspace/。保留函数避免旧调用点崩溃。"""
    return project_root / LEGACY_WORKSPACE_DIR


def archives_dir(project_root: Path) -> Path:
    return project_root / ARCHIVES_DIR


def scripts_dir(project_root: Path) -> Path:
    """可复用脚本目录：v2 起直接位于 memory/ 下。"""
    return project_root / MEMORY_DIR


def list_deliverable_names(project_root: Path, limit: int = 8) -> list[str]:
    """交付物文件名（忽略 README）。"""
    names: list[str] = []
    for folder in (deliverables_dir(project_root), project_root / "deliverables", artifacts_dir(project_root)):
        if not folder.exists():
            continue
        for p in folder.iterdir():
            if not p.is_file():
                continue
            if p.name.lower() in ("readme.txt", "readme.md"):
                continue
            names.append(p.name)
            if len(names) >= limit:
                return names
    return names
