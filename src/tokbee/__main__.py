"""python -m tokbee 入口。"""

import sys

from tokbee.utils.logger import setup_crash_reporting, setup_logger


def main():
    logger = setup_logger()
    setup_crash_reporting(logger)
    logger.info("BeeHive 启动中...")

    try:
        from tokbee.app import Application

        app = Application()
        exit_code = app.run()
    except Exception:
        logger.exception("BeeHive 启动或运行失败")
        return 1

    logger.info("BeeHive 已退出")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
