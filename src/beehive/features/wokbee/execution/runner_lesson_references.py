"""经验使用的技能和参考材料快照。"""
from __future__ import annotations
import logging

logger = logging.getLogger("wokbee")

def snapshot_references(self, req, lesson, ai_fields):
    # 保存本次用到的 Skills 快照与参考材料到 memory/（归档不清理）
    try:
        from beehive.features.wokbee.projects.references import (
            snapshot_used_skills,
            write_reference_manifest,
        )

        used_skills: list[str] = []
        mats: list[dict] = []
        if isinstance(ai_fields, dict):
            raw_skills = ai_fields.get("used_skills")
            if isinstance(raw_skills, list):
                used_skills = [str(s) for s in raw_skills if str(s).strip()]
            raw_mats = ai_fields.get("reference_materials")
            if isinstance(raw_mats, list):
                mats = [
                    m
                    for m in raw_mats
                    if isinstance(m, dict)
                    and (str(m.get("path") or "").strip() or str(m.get("note") or "").strip())
                ]
        written = snapshot_used_skills(
            req.project_root,
            used_skills,
        )
        manifest_path = write_reference_manifest(
            req.project_root,
            used_skills=used_skills,
            materials=mats,
            goal=lesson.goal or "",
        )
        snap_msg = f"已保存 {len(written)} 个 Skill 快照到 memory/"
        if manifest_path:
            try:
                mrel = manifest_path.relative_to(req.project_root).as_posix()
            except ValueError:
                mrel = str(manifest_path)
            snap_msg += f"，并登记 {mrel}"
        if written or manifest_path:
            self._emit("info", snap_msg + "（memory/ 不会被归档）")
    except Exception:
        logger.exception("保存参考材料失败（经验仍会写入）")
