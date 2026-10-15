"""网关项目管理指令；不属于协议连接层。"""
from __future__ import annotations
import logging
from beehive.features.wokbee.projects.models import MAX_PROJECT_TITLE_LEN
from beehive.system.gateway.base import ChannelMessage
logger = logging.getLogger("wokbee")
def _run_command(self, cmd: str, arg: str, msg: ChannelMessage) -> None:
    if cmd == "#new":
        _cmd_new(self, arg, msg)
    elif cmd == "#list":
        _cmd_list(self, msg)
    elif cmd == "#run":
        _cmd_run(self, msg)
    elif cmd == "#help":
        _cmd_help(self, msg)

def _cmd_new(self, goal: str, msg: ChannelMessage) -> None:
    """`#new 描述`：新建项目并把背景描述设为项目目标，同时绑定为当前频道默认。"""
    goal = (goal or "").strip()
    proj = self.project_store.create(title=_title_from_goal(goal), goal=goal)
    pid = proj.id
    cfg = self.store.get_config()
    cfg.set_default_project(msg.channel, pid)
    self.store.save_config(cfg)
    self.notifier.log_line.emit(f"[#new] 已创建项目 {proj.title}（{pid}）并绑定为 {msg.channel} 默认")
    goal_line = goal if goal else "（未设置目标；到桌面详情里补充，或用 #new 描述 直接带上）"
    self._reply(msg, (
        "✔ 已创建新项目，并绑定为当前公众号的默认项目：\n"
            f"名称：{proj.title}\n标识：{pid}\n目标：{goal_line}\n\n"
            "想让它干活，发送 #run 即可按目标运行。"
    ))

def _cmd_list(self, msg: ChannelMessage) -> None:
    """`#list`：返回全部项目清单（与「未绑定默认项目」时提示一致；含各自项目ID）。"""
    self._reply(msg, self._router._project_listing())

def _cmd_run(self, msg: ChannelMessage) -> None:
    """`#run`：用当前频道默认项目的「目标」作为提示词运行 Agent。"""
    cfg = self.store.get_config()
    pid = cfg.default_project_for(msg.channel)
    if not pid:
        self._reply(msg, "当前频道还没有绑定默认项目。\n" + self._router._project_listing())
        return
    project = self.project_store.get(pid)
    if project is None:
        self._reply(msg, f"默认项目不存在或已删除：{pid}。\n" + self._router._project_listing())
        return
    goal = (project.goal or "").strip()
    if not goal:
        self._reply(msg, f"项目「{project.title}」还没有目标，无法运行。请用 #new 描述 建立带目标的项目。")
        return
    self.notifier.log_line.emit(f"[#run] 用 {msg.channel} 默认项目「{project.title}」的目标运行")
    self._run_chat(msg, pid, goal)

def _cmd_help(self, msg: ChannelMessage) -> None:
    """`#help`：返回可与 WokBee 交互的系统指令说明。"""
    self._reply(msg, self._router._command_help())

def _title_from_goal(goal: str) -> str:
    """`#new` 未显式给名称时：取目标前 N 字作标题（更好认），否则给默认名。"""
    goal = (goal or "").strip()
    if goal:
        return goal[:MAX_PROJECT_TITLE_LEN]
    return "手机新建项目"
