# WOKBEE / DeziBee 文件工具梳理

日期：2026-09-16

## 结论

系统已经使用开源 Deep Agents 的 FilesystemMiddleware、LocalShellBackend 和 CompositeBackend。
本次保留其工具协议、目录路由和权限边界，修复外围封装与 Qt 线程生命周期，没有新增 MCP 服务或依赖。

已确认的代码问题：

- 提示词强制超过约 3000 字就分块写，编辑还必须搜索、读取、插入，造成大量可避免调用。
- 原分块工具每次都读取旧文件、拼接并覆盖，未完成的 HTML 直接暴露给预览；中断后留下半成品。
- 上游写入/编辑使用截断原文件后写入的方式，编码错误或磁盘写入失败可能损坏原文件。
- 自定义读取结果还会经过 12000 字的通用截断，长单行没有有效的字符游标。
- 自定义工具未纳入读取/写入审批分类。
- DeziBee 自定义结果/模型错误信号发出时，QThread.run 可能尚未返回，UI 就调用 deleteLater。
- 主窗口关闭只调用 WokBee 的 shutdown；DeziBee 的 shutdown 即使等待超时也清除线程引用。

线程问题是已定位的闪退风险；未取得用户实际闪退堆栈，不能确认所有闪退均由它导致。

## 工具职责

| 工具 | 用途与调用规则 |
|---|---|
| ls / glob | 列目录、按路径模式找文件，继续复用 Deep Agents |
| grep | 按内容跨文件搜索，继续复用 Deep Agents |
| read_file | 通用读取；小文件一次读，较大文件按任务定位，不机械翻遍全文 |
| read_file_range | 读取指定行窗口；明确文件末尾，超长单行支持 char_offset / next_char_offset |
| find_in_file | 单文件字面查询，给行号与有限上下文；已有上下文足够时直接编辑 |
| write_file | 完整创建/替换，取消固定 3000 字分块要求；项目后端原子替换 |
| edit_file | 精确替换，复用 Deep Agents 的匹配函数；项目后端原子写入 |
| insert_text | 锚点插入/替换，通过 backend.edit 比较快照，检测读取后的并发变化 |
| write_file_chunk | 仅超出模型输出预算时使用；暂存、顺序校验、最终一次提交 |
| delete | 复用原有删除及路径守卫；仍属于写入审批，目录删除仍为递归删除 |

分块新参数：首块 `mode=overwrite, offset=0`；后续 `mode=append, offset=上次返回的 next_offset`；最后 `final=True`。
offset 是 Python Unicode 字符数，不是字节数。暂存上限为每个工具实例共 800 万字符，实例销毁会丢弃未提交内容。
中断时原文件保留；未提交内容不作交付。失败重试须遵循返回的 next_offset，不能盲目重发追加。

## 写入和运行保护

- 项目文件先写同目录临时文件、flush/fsync，成功后 os.replace；写失败不截断旧文件。
- 项目编辑在进程内锁中完成读取、替换、原子提交；不提供跨进程事务或外部编辑器锁。
- DeziBee 的 demo/index.html 在提交前检查 HTML 结束标签及 WORKBENCH_DATA 的括号/字符串闭合。
  这能拦截明显残缺文件，不是完整 JavaScript 语法检查，也不保证任意 HTML/CSS/JS 的语义正确。
- 自定义写工具不再套不可取消的线程池超时，避免报超时之后仍在后台写入，与重试相互竞争。
- 线程在 QThread.finished 后才释放；关闭窗口先请求取消，线程未退出则延迟关闭。
- 这些原子写入增强针对项目后端；额外挂载目录仍使用原有后端，execute 仍按原有命令路径执行。

## 开源方案比较

1. [Deep Agents 后端](https://docs.langchain.com/oss/python/deepagents/backends)：系统已在使用，保留现有 Python 集成最直接。
2. [官方 Filesystem MCP](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md)：提供读写、搜索、目录管理、多处编辑及 dryRun；需要 Node.js/MCP 进程，并重新适配虚拟目录与审批策略。
   write_file 仍接收完整 content，不能消除模型输出预算限制；也不能修复 Qt 线程释放问题。本次不替换。
3. [Aider 编辑格式](https://github.com/Aider-AI/aider/blob/main/aider/website/docs/more/edit-formats.md)：SEARCH/REPLACE 适合局部修改；其完整代理运行方式不适合作为当前后端直接替换件。现有 edit_file 已具备精确替换能力。

## 验证与使用

执行 `.venv/Scripts/python.exe -m unittest discover -s tests -v`。
回归覆盖长中文/emoji 文件读写查改删、超长单行游标、原子写入失败、错误编码、乱序/重复分块、越界/归档拒绝、二进制拒改、原型截断、并发快照冲突及真实 Qt 线程结果先于退出的场景。

Windows 沙箱内 tempfile 创建的目录出现访问拒绝，测试改在正常权限下执行。
未调用付费模型或进行真实模型驱动的 GUI 全流程测试；模型是否遵循新规则仍需实际会话验证。
重启应用并在 DeziBee 新对话中验证，避免旧会话里的分块指令继续干扰。已有需求的 GUIDE 随框架文件升级更新，index.html 用户内容不覆盖。
