import tempfile
import unittest
import json
from pathlib import Path

from creative_factory.projects import create_project, migrate_project, role_dir
from creative_factory.remix import DEFAULTS


class ProjectLayoutTests(unittest.TestCase):
    def test_new_project_uses_polish_folders_and_config(self):
        with tempfile.TemporaryDirectory() as temp:
            project = create_project(Path(temp) / "Produkt żółty")

            self.assertEqual(project, Path(temp) / "Produkt żółty")
            for folder in ("Hooki", "Klipy", "Zakończenia", "Gotowe filmy", "_robocze"):
                self.assertTrue((project / folder).is_dir(), folder)
            self.assertTrue((project / "config.json").is_file())

    def test_legacy_english_folders_remain_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "hooks").mkdir()

            self.assertEqual(role_dir(project, "hooks"), project / "hooks")

    def test_migration_copies_user_files_but_skips_tools_and_work_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "stary"
            destination = Path(temp) / "nowy" / "Projekt"
            (source / "hooks").mkdir(parents=True)
            (source / "clips").mkdir()
            (source / "tools").mkdir()
            (source / "_work").mkdir()
            (source / "output" / "seria").mkdir(parents=True)
            (source / "hooks" / "hook.mp4").write_bytes(b"hook")
            (source / "clips" / "clip.mp4").write_bytes(b"clip")
            (source / "tools" / "ffmpeg.exe").write_bytes(b"tool")
            (source / "_work" / "cache.tmp").write_bytes(b"cache")
            (source / "output" / "seria" / "AD_001.mp4").write_bytes(b"result")

            migrate_project(source, destination)

            self.assertEqual((destination / "Hooki" / "hook.mp4").read_bytes(), b"hook")
            self.assertEqual((destination / "Klipy" / "clip.mp4").read_bytes(), b"clip")
            self.assertEqual((destination / "Gotowe filmy" / "seria" / "AD_001.mp4").read_bytes(), b"result")
            self.assertFalse((destination / "tools").exists())
            self.assertFalse((destination / "_robocze" / "cache.tmp").exists())
            self.assertTrue((source / "hooks" / "hook.mp4").exists())

    def test_migration_updates_relative_paths_and_uses_new_random_seed_default(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "stary"
            destination = Path(temp) / "nowy"
            (source / "hooks").mkdir(parents=True)
            (source / "clips").mkdir()
            (source / "output" / "seria").mkdir(parents=True)
            (source / "hooks" / "hook.mp4").write_bytes(b"hook")
            (source / "clips" / "clip.mp4").write_bytes(b"clip")
            (source / "config.json").write_text(json.dumps({"ads_count": 3, "seed": 42, "output_dir": "output", "fit": "crop"}), encoding="utf-8")
            (source / "assets.json").write_text(json.dumps({"hooks/hook.mp4": {"fit": "fill"}}), encoding="utf-8")
            (source / "output" / "seria" / "manifest.csv").write_text(
                "ad,source\nAD_001.mp4,hooks/hook.mp4\n", encoding="utf-8-sig"
            )

            migrate_project(source, destination, DEFAULTS)

            config = json.loads((destination / "config.json").read_text(encoding="utf-8"))
            assets = json.loads((destination / "assets.json").read_text(encoding="utf-8"))
            manifest = (destination / "Gotowe filmy" / "seria" / "manifest.csv").read_text(encoding="utf-8-sig")
            self.assertIsNone(config["seed"])
            self.assertEqual(config["output_dir"], "Gotowe filmy")
            self.assertIn("Hooki/hook.mp4", assets)
            self.assertEqual(assets["Klipy/clip.mp4"]["fit"], "fill")
            self.assertIn("Hooki/hook.mp4", manifest)

    def test_failed_migration_keeps_source_and_does_not_publish_partial_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "stary"
            destination = Path(temp) / "nowy" / "Projekt"
            (source / "hooks").mkdir(parents=True)
            (source / "hooks" / "hook.mp4").write_bytes(b"source remains safe")
            (source / "assets.json").write_text("{invalid json", encoding="utf-8")

            with self.assertRaises(ValueError):
                migrate_project(source, destination, DEFAULTS)

            self.assertTrue((source / "hooks" / "hook.mp4").is_file())
            self.assertFalse(destination.exists())
            self.assertFalse(list(destination.parent.glob(".Projekt.migrating-*")))

    def test_migration_rewrites_manifest_paths_when_folder_name_length_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "stary"
            destination = Path(temp) / "nowy"
            (source / "cta").mkdir(parents=True)
            (source / "output" / "seria").mkdir(parents=True)
            (source / "cta" / "ending.mp4").write_bytes(b"ending")
            manifest = source / "output" / "seria" / "manifest.csv"
            manifest.write_text("ad,source\nAD_001.mp4,cta/ending.mp4\n", encoding="utf-8-sig")

            migrate_project(source, destination, DEFAULTS)

            migrated = (destination / "Gotowe filmy" / "seria" / "manifest.csv").read_text(encoding="utf-8-sig")
            self.assertIn("Zakończenia/ending.mp4", migrated)


if __name__ == "__main__":
    unittest.main()
