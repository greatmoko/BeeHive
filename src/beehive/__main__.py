"""BeeHive 应用统一入口。"""

import sys

sys.dont_write_bytecode = True

from beehive.system.runtime.logger import setup_crash_reporting, setup_logger


def main():
    logger = setup_logger()
    setup_crash_reporting(logger)
    logger.info("BeeHive 启动中...")

    try:
        from beehive.app.bootstrap import Application

        app = Application()
        exit_code = app.run()
    except Exception:
        logger.exception("BeeHive 启动或运行失败")
        return 1

    logger.info("BeeHive 已退出")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
