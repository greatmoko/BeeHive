"""BeeHive 启动入口。"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from beehive.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
