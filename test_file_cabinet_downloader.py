import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("file_cabinet_downloader.py")
SPEC = importlib.util.spec_from_file_location("file_cabinet_downloader", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class SizeTests(unittest.TestCase):
    def test_size_units(self):
        self.assertEqual(MODULE.parse_size("587.92 KB"), int(587.92 * 1024))
        self.assertEqual(MODULE.parse_size("441.23 MB"), int(441.23 * 1024**2))
        self.assertEqual(MODULE.parse_size("2.06 GB"), int(2.06 * 1024**3))

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            MODULE.parse_size("unknown")


class HtmlTests(unittest.TestCase):
    def test_supplied_html(self):
        source = (
            Path(__file__).parents[2]
            / "upload"
            / "Pasted text (3)(3).txt"
        )
        records = MODULE.parse_folder_rows(
            source.read_text(encoding="utf-8", errors="replace"),
            page_index=0,
            base_url=MODULE.ACCOUNT_BASE_URL,
        )
        self.assertGreater(len(records), 0)
        first = records[0]
        self.assertEqual(first.internal_id, "14840")
        self.assertEqual(first.name, "(December, 2021)")
        self.assertEqual(first.size_text, "441.23 MB")
        self.assertIn("/core/media/downloadfolder.nl?id=14840", first.download_url)


if __name__ == "__main__":
    unittest.main()
