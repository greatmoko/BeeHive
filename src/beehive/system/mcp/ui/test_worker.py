"""按职责组织的 test_worker。"""
from __future__ import annotations
from PySide6.QtCore import QThread, Signal
from beehive.system.mcp.mcp_store import McpServerConfig


class _McpTestWorker(QThread):
    """后台测试 MCP 连接：带超时 via asyncio.wait_for，结果经信号回主线程。"""

    result = Signal(str)

    def __init__(self, server: McpServerConfig, parent=None):
        super().__init__(parent)
        self._server = server

    def run(self):
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            import asyncio

            async def _test():
                conn = self._server.to_connection()
                # 超时保护：>=0.1.0 不支持 async with；get_tools() 由库内部管理连接生命周期。
                client = MultiServerMCPClient({
                    self._server.name or self._server.id: conn,
                })
                return await asyncio.wait_for(client.get_tools(), timeout=8)

            tools = asyncio.run(_test())
            names = ", ".join(getattr(t, "name", str(t)) for t in tools[:12])
            more = f" 等共 {len(tools)} 个" if len(tools) > 12 else f"（共 {len(tools)} 个）"
            self.result.emit(f"连接成功，工具：{names or '(无)'}{more}")
        except Exception as e:
            if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__:
                self.result.emit("连接超时：服务器无响应（超过 8 秒）。")
            else:
                self.result.emit(f"连接失败：{e}")
