"""全局授权目录与 Skills 加载目录，保留原路径校验和整段配置锁。"""
from pathlib import Path
from beehive.system.settings.section import SettingsSection


class DirectorySettings(SettingsSection):
    @property
    def additional_directories(self) -> list[dict[str, str]]:
        """已授权附加目录（项目外）。仅返回真实存在且为目录的项。"""
        raw = self.get("additional_directories") or []
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                path = str(item.get("path") or "").strip()
            elif isinstance(item, str):
                path, name = item.strip(), ""
            else:
                continue
            if not path:
                continue
            try:
                rp = Path(path).expanduser().resolve()
            except OSError:
                continue
            if not rp.is_dir():
                continue
            out.append({
                "name": name or rp.name,
                "path": str(rp),
                "slug": str(item.get("slug") or "") if isinstance(item, dict) else "",
            })
        return out

    @additional_directories.setter
    def additional_directories(self, value: list[dict[str, str]]) -> None:
        self.set("additional_directories", list(value or []))

    def add_additional_directory(self, name: str, path: str | Path, slug: str = "") -> dict[str, str] | None:
        """把目录加入全局白名单（去重、保存）。目录不存在则返回 None。

        slug 是 /ext/<slug>/ 虚拟路由段；持久化它，下次会话重建 composite 时能复命同一个
        虚拟路径，避免旧 /ext/<slug>/ 引用（经验/脚本里的路径）跨会话失效或串路由。

        此方法可能从 worker 线程（request_access 工具）调用，与主线程设置页并发读-改-写同一
        单例 Config → 用其锁串行化整段操作，避免更新丢失。
        """
        with self._config.lock:
            try:
                rp = Path(str(path)).expanduser().resolve()
            except OSError:
                return None
            if not rp.is_dir():
                return None
            cur = self._canonicalize(self.get("additional_directories") or []) or []
            key = str(rp)
            for item in cur:
                if item["path"] == key:
                    item["name"] = name or item.get("name") or rp.name
                    if slug:
                        item["slug"] = slug  # 更新为最新选中的 slug
                    self._config.set("wokbee.additional_directories", cur)
                    self._config.save()
                    return {"name": item["name"], "path": item["path"], "slug": item.get("slug", "")}
            entry = {"name": name or rp.name, "path": key}
            if slug:
                entry["slug"] = slug
            cur.append(entry)
            self._config.set("wokbee.additional_directories", cur)
            self._config.save()
            return entry

    def remove_additional_directory(self, path: str | Path) -> bool:
        """按路径移除白名单项。"""
        with self._config.lock:
            try:
                key = str(Path(str(path)).expanduser().resolve())
            except OSError:
                return False
            cur = self._canonicalize(self.get("additional_directories") or []) or []
            new = [x for x in cur if x["path"] != key]
            if len(new) != len(cur):
                self._config.set("wokbee.additional_directories", new)
                self._config.save()
                return True
            return False

    @staticmethod
    def _canonicalize(items: list) -> list[dict[str, str]]:
        """把 [str] 或 [dict] 混合列表统一成 [{"name","path","slug"}]。"""
        out: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict) and item.get("path"):
                path = str(item["path"]).strip()
                name = str(item.get("name") or "").strip()
            elif isinstance(item, str) and item.strip():
                path = item.strip()
                name = ""
            else:
                continue
            try:
                rp = Path(path).expanduser().resolve()
            except OSError:
                continue
            if not rp.is_dir():
                continue
            out.append({
                "name": name or rp.name,
                "path": str(rp),
                "slug": str(item.get("slug") or "") if isinstance(item, dict) else "",
            })
        return out

    @property
    def skills_dirs(self) -> list[str]:
        """额外 Skills 加载目录（绝对路径）。仅返回真实存在且为目录的项。"""
        raw = self.get("skills_dirs") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            path = str(item or "").strip()
            if not path:
                continue
            try:
                rp = Path(path).expanduser().resolve()
            except OSError:
                continue
            if not rp.is_dir():
                continue
            if str(rp) not in out:
                out.append(str(rp))
        return out

    @skills_dirs.setter
    def skills_dirs(self, value: list[str]) -> None:
        paths: list[str] = []
        for item in (value or []):
            path = str(item or "").strip()
            if not path:
                continue
            try:
                rp = Path(path).expanduser().resolve()
            except OSError:
                continue
            s = str(rp)
            if s not in paths:
                paths.append(s)
        self.set("skills_dirs", paths)

    def add_skills_dir(self, path: str | Path) -> bool:
        """把目录加入额外 Skills 加载列表（去重、保存）。目录不存在返回 False。"""
        with self._config.lock:
            try:
                rp = Path(str(path)).expanduser().resolve()
            except OSError:
                return False
            if not rp.is_dir():
                return False
            key = str(rp)
            cur = self.skills_dirs
            if key not in cur:
                cur.append(key)
                self.skills_dirs = cur
                self.save()
            return True

    def remove_skills_dir(self, path: str | Path) -> bool:
        with self._config.lock:
            try:
                key = str(Path(str(path)).expanduser().resolve())
            except OSError:
                return False
            cur = self.skills_dirs
            new = [x for x in cur if x != key]
            if len(new) != len(cur):
                self.skills_dirs = new
                self.save()
                return True
            return False
