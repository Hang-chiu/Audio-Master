"""Regression checks for the macOS bundle metadata kept in the PyInstaller spec."""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "Audio Master.spec"
DOCUMENT_ICON = PROJECT_ROOT / "icons" / "AudioProject.icns"


class MacOSDocumentIconPackagingTests(unittest.TestCase):
    def test_abproj_document_icon_is_bundled_and_declared(self):
        """Finder can only resolve a document icon that ships in Resources."""
        spec = SPEC_PATH.read_text(encoding="utf-8")

        self.assertTrue(DOCUMENT_ICON.is_file())
        self.assertIn("datas.append((DOCUMENT_ICON, '.'))", spec)
        self.assertIn("'CFBundleTypeIconFile': 'AudioProject.icns'", spec)
        self.assertIn("'UTTypeIconFile': 'AudioProject.icns'", spec)


if __name__ == "__main__":
    unittest.main()
