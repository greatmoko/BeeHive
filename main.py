"""WokBee 启动入口。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from tokbee.utils.logger import setup_crash_reporting, setup_logger


def main():
    logger = setup_logger()
    setup_crash_reporting(logger)
    logger.info("WokBee 启动中...")

    try:
        from tokbee.app import Application

        app = Application()
        exit_code = app.run()
    except Exception:
        logger.exception("WokBee 启动或运行失败")
        return 1

    logger.info("WokBee 已退出")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
