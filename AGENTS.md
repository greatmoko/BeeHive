# Project Instructions

## GitHub Updates

- When updating this project on GitHub, only commit and push source-code changes.
- Do not build, package, or upload EXE files or EXE build artifacts unless the user explicitly requests it.

## 测试目录规则

- 所有正式测试、临时验证脚本、测试夹具及测试产物统一放在项目 `tests/` 下，不得在项目根目录或业务源码目录新增测试文件、假项目、日志、报告、截图或临时数据。
- 临时测试数据使用 `tests/.tmp/`，测试报告、截图等产物使用 `tests/artifacts/`，测试工具缓存使用 `tests/.cache/`；测试结束后清理不再需要的临时文件。
- 测试路径必须以当前项目的 `tests/` 绝对路径为基准，不依赖启动时的工作目录。使用 `tempfile` 时显式指定 `tests/.tmp/` 下的目录，不使用系统默认临时目录保存测试内容。
- 运行 Python 测试使用 `-B` 或 `PYTHONDONTWRITEBYTECODE=1`，避免在源码目录生成 `__pycache__/`；其他测试工具的日志、缓存和输出目录也必须指向 `tests/` 下。
- 保留仍有用途的正式回归测试；清理仅针对确认无用的临时脚本、缓存和测试残留，不删除业务数据、依赖环境或已有用户改动。
