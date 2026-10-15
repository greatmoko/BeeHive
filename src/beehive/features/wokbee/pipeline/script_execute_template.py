"""本地命令执行脚本模板。"""
from __future__ import annotations
import json
from beehive.features.wokbee.pipeline.script_templates import _EXECUTE_HEADER, _hdr
def _script_execute(command: str, label: str = "execute", *, timeout: int = 180) -> str:
    cmd = json.dumps(command, ensure_ascii=False)
    lab = json.dumps(label or "execute", ensure_ascii=False)
    return (
        _hdr(_EXECUTE_HEADER)
        + f"""
CMD_TEMPLATE = {cmd}
LABEL = {lab}
TIMEOUT = {int(timeout)}

def main() -> None:
    skills = _skills_home()
    cmd = CMD_TEMPLATE.replace("{{SKILLS}}", str(skills).replace("\\\\", "/"))
    # 本脚本位于 memory/，工作目录应为项目根
    root = Path(__file__).resolve().parents[1]
    argv = _pwsh_argv(cmd)
    try:
        proc = subprocess.run(
            argv if argv is not None else cmd,
            cwd=str(root),
            capture_output=True,
            shell=argv is None,
            timeout=TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        _save(f"脚本执行失败：超时（>{{TIMEOUT}}s）", label=LABEL)
        sys.exit(1)
    except OSError as e:
        _save(f"脚本执行失败：{{e}}", label=LABEL)
        sys.exit(1)
    out = _decode_bytes(proc.stdout)
    err = _decode_bytes(proc.stderr)
    body = out if out else err
    if proc.returncode != 0:
        msg = body or f"exit={{proc.returncode}}"
        if err and out:
            msg = f"{{out}}\\n\\n[stderr]\\n{{err}}"
        _save(f"脚本执行失败：{{msg}}", label=LABEL)
        sys.exit(proc.returncode or 1)
    if not body:
        body = "(无输出)"
    _save(body, label=LABEL)

if __name__ == "__main__":
    main()
"""
    )
