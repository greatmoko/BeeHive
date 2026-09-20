import tempfile
import unittest
from pathlib import Path

from tokbee.core.config import Config, default_data_dir
from wokbee.core.settings import WokBeeSettings


class WorkspaceDefaultsTests(unittest.TestCase):
    def test_defaults_and_saved_custom_directories(self):
        self.assertEqual(default_data_dir(), Path.home() / ".wokbee")
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            config = Config(str(config_path))
            settings = WokBeeSettings(config)
            self.assertEqual(settings.workspace_root, Path.home() / "BeeHive" / "WokBee")
            self.assertEqual(settings.dezibee_work_root, Path.home() / "BeeHive" / "DeziBee")
            settings.workspace_root = Path(temporary) / "custom-wokbee"
            settings.dezibee_work_root = Path(temporary) / "custom-dezibee"
            settings.save()
            config.reload()
            settings = WokBeeSettings(config)
            self.assertEqual(settings.workspace_root, Path(temporary) / "custom-wokbee")
            self.assertEqual(settings.dezibee_work_root, Path(temporary) / "custom-dezibee")


if __name__ == "__main__":
    unittest.main()
