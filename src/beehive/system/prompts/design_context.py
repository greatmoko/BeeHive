"""集中维护design_context提示词。"""

def build_design_prompt(req) -> str:
    """对话首轮的需求上下文；后续变更由 runner 显式通知。"""
    shell = (getattr(req, "device_shell", "") or "").strip().lower()
    if shell not in {"phone", "tablet", "browser"}:
        desc = ((req.description or "") + (req.title or "")).lower()
        if any(k in desc for k in ("桌面", "网页", "web", "后台", "管理", "pc")):
            shell = "browser"
        elif any(k in desc for k in ("平板", "ipad")):
            shell = "tablet"
        else:
            shell = "phone"
    return (
        f"【需求上下文】\n当前需求：{req.title}\n需求描述：{req.description or '（无）'}\n"
        f"工作目录：{req.root}\n默认设备外壳：{shell}"
    )
