import os
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QListWidgetItem, QComboBox

from main import Window, _project_root, read_config
from creative_factory.projects import create_project
from creative_factory.remix import DEFAULTS


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = create_project(self.root / "Produkt A", DEFAULTS)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.window = Window(self.project, settings=self.settings, project_root=self.root / "Projekty")

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_window_has_polish_name_and_no_global_segment_length_fields(self):
        self.assertEqual(self.window.windowTitle(), "Generator filmów Adi")
        self.assertFalse(hasattr(self.window, "hook_seconds"))
        self.assertFalse(hasattr(self.window, "clip_seconds"))
        self.assertEqual(self.window.format.currentData(), "portrait")
        self.assertEqual(self.window.fit.currentData(), "fill")

    def test_series_counts_are_large_dropdowns(self):
        self.assertIsInstance(self.window.ads, QComboBox)
        self.assertIsInstance(self.window.clips, QComboBox)
        self.assertEqual(self.window.ads.currentData(), 5)
        self.assertEqual(self.window.clips.currentData(), 4)
        self.assertIn(10, [self.window.ads.itemData(i) for i in range(self.window.ads.count())])
        self.assertIn(100, [self.window.ads.itemData(i) for i in range(self.window.ads.count())])

    def test_legacy_count_values_are_kept_in_dropdown(self):
        path = self.project / "config.json"
        path.write_text(json.dumps({"ads_count": 42, "clips_per_ad": 17, "output_mode": "portrait",
                                    "use_cta": False}), encoding="utf-8")

        self.window.load_project()

        self.assertEqual(self.window.ads.currentData(), 42)
        self.assertEqual(self.window.clips.currentData(), 17)
        self.assertIn("niestandardowa", self.window.ads.currentText().casefold())

    def test_settings_are_in_collapsible_sections(self):
        self.assertFalse(self.window.asset_section.isExpanded())

        self.window.asset_section.setExpanded(True)

        self.assertTrue(self.window.asset_section.isExpanded())
        self.assertTrue(self.window.asset_section.toggle_button.isCheckable())

    def test_asset_changes_use_status_instead_of_save_button(self):
        self.assertFalse(hasattr(self.window, "save_asset_button"))
        self.assertEqual(self.window.asset_status.text(), "Wybierz ujęcie")

    def test_asset_settings_are_written_and_report_saved_status(self):
        source = self.project / "Hooki" / "hook.mp4"
        source.write_bytes(b"not a real video")
        self.window.media_metadata[source] = {"duration": 2.0, "width": 720, "height": 1280}
        self.window.show_preview = lambda *_: None
        item = QListWidgetItem(source.name)
        item.setData(Qt.UserRole, str(source))
        self.window.lists["hooks"].addItem(item)
        self.window.lists["hooks"].setCurrentRow(0)
        self.window.trim_start.setValue(.25)
        self.window.trim_end.setValue(1.75)

        self.window.save_selected_asset()

        assets = json.loads((self.project / "assets.json").read_text(encoding="utf-8"))
        self.assertEqual(assets["Hooki/hook.mp4"]["trim"], {"start": .25, "end": 1.75})
        self.assertEqual(self.window.asset_status.text(), "Zapisano")

    def test_library_lists_expose_empty_state_and_counts(self):
        self.window.refresh()

        self.assertEqual(self.window.library_counts["hooks"].text(), "0")
        self.assertIn("Przeciągnij", self.window.lists["hooks"].placeholder_text())

    def test_legacy_settings_load_without_forcing_old_length_or_fps(self):
        path = self.project / "config.json"
        path.write_text(json.dumps({"ads_count": 3, "clips_per_ad": 2, "hook_seconds": 2.5,
                                    "clip_seconds": 1.8, "fps": 30, "width": 1080,
                                    "height": 1920, "fit": "crop", "use_cta": False,
                                    "seed": 42, "output_dir": "output"}), encoding="utf-8")

        config = read_config(self.project)

        self.assertEqual(config["ads_count"], 3)
        self.assertEqual(config["clips_per_ad"], 2)
        self.assertIsNone(config["seed"])
        self.assertEqual(config["output_mode"], "portrait")
        self.assertNotIn("hook_seconds", config)

    def test_rendering_disables_project_and_media_edits(self):
        self.window.set_rendering(True)

        self.assertFalse(self.window.project_combo.isEnabled())
        self.assertFalse(self.window.new_project_button.isEnabled())
        self.assertFalse(self.window.lists["clips"].isEnabled())
        self.assertTrue(self.window.cancel_button.isEnabled())

    def test_project_history_shows_all_batches(self):
        output = self.project / "Gotowe filmy"
        (output / "seria_1").mkdir()
        (output / "seria_2").mkdir()

        self.window.refresh_results()

        self.assertEqual(self.window.batch_combo.count(), 2)

    def test_project_history_shows_legacy_videos_in_root_folder(self):
        output = self.project / "Gotowe filmy"
        (output / "AD_001.mp4").write_bytes(b"old")

        self.window.refresh_results()

        self.assertEqual(self.window.batch_combo.count(), 1)
        self.assertEqual(self.window.results.count(), 1)
        self.assertEqual(self.window.results.item(0).text(), "AD_001.mp4")

    def test_selecting_asset_in_another_role_does_not_keep_old_selection(self):
        hook = self.project / "Hooki" / "hook.mp4"
        clip = self.project / "Klipy" / "clip.mp4"
        hook.write_bytes(b"not a real video")
        clip.write_bytes(b"not a real video")
        self.window.refresh()
        self.window.show_preview = lambda *_: None
        for role, path in (("hooks", hook), ("clips", clip)):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.window.lists[role].addItem(item)
        self.window.lists["hooks"].setCurrentRow(0)
        self.window.lists["clips"].setCurrentRow(0)

        role, selected = self.window.selected_asset()

        self.assertEqual(role, "clips")
        self.assertEqual(selected, clip)

    def test_project_settings_are_saved_and_kept_separate(self):
        second = create_project(self.root / "Produkt B", DEFAULTS)
        self.settings.setValue("projects", [str(self.project), str(second)])
        self.window.refresh_project_combo()
        self.window.ads.setCurrentIndex(self.window.ads.findData(7))
        self.window.project_combo.setCurrentIndex(1)

        self.assertEqual(self.window.project, second)
        self.assertEqual(read_config(self.project)["ads_count"], 7)
        self.assertEqual(read_config(second)["ads_count"], 5)

    def test_existing_project_on_g_sets_g_projects_root(self):
        settings = QSettings(str(self.root / "g-drive.ini"), QSettings.Format.IniFormat)
        settings.setValue("projects_root", str(self.root / "Documents"))

        self.assertEqual(_project_root(settings, Path("G:/Generator filmów Adi/Projekty/Dotychczasowy projekt")),
                         Path("G:/Generator filmów Adi/Projekty"))

    def test_explicit_project_on_another_drive_uses_its_parent_for_new_projects(self):
        settings = QSettings(str(self.root / "override.ini"), QSettings.Format.IniFormat)
        window = Window(self.project, settings=settings)
        try:
            self.assertEqual(window.project_root, self.project.parent)
        finally:
            window.close()

    def test_close_waits_for_preview_worker_to_finish(self):
        self.window.preview_workers.append(object())
        event = QCloseEvent()

        self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertTrue(self.window.close_after_preview)

    def test_packaged_smoke_render_does_not_start_a_thumbnail_thread_on_exit(self):
        batch = self.project / "Gotowe filmy" / "test"
        batch.mkdir(parents=True)
        video = batch / "Film_001.mp4"
        video.write_bytes(b"test placeholder")
        self.window.show_preview = Mock()

        with patch.dict(os.environ, {"CREATIVE_FACTORY_SMOKE_RENDER": "1"}):
            self.window.on_complete(str(batch), str(self.project))

        self.window.show_preview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
