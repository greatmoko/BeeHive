"""技能目录分享的下载路径选择与 ZIP 打包。"""
from pathlib import Path
import zipfile

def _downloads_dir() -> Path:
    """系统下载目录；取不到时回退到 ~/Downloads 或用户主目录。"""
    raw = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    if str(raw).strip():
        return Path(raw).expanduser()
    cand = Path.home() / "Downloads"
    if cand.is_dir():
        return cand
    return Path.home()

def _zip_skill(skill_path: Path, dest_zip: Path) -> bool:
    """把技能目录整体打成 zip（顶层含技能文件夹名）。"""
    try:
        with zipfile.ZipFile(str(dest_zip), "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(skill_path.rglob("*")):
                if p.is_file():
                    arc = p.relative_to(skill_path.parent).as_posix()
                    zf.write(str(p), arc)
        return True
    except OSError:
        return False
