import csv
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creative_factory import remix
from creative_factory.projects import create_project


class RenderPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("CF_TEST_FFMPEG_DIR")
        cls.ffmpeg_dir = Path(configured) if configured else None
        if not cls.ffmpeg_dir or not (cls.ffmpeg_dir / "ffmpeg.exe").is_file() or not (cls.ffmpeg_dir / "ffprobe.exe").is_file():
            raise unittest.SkipTest("Ustaw CF_TEST_FFMPEG_DIR na folder z ffmpeg.exe i ffprobe.exe.")

    def make_video(self, path, *, seconds, fps, audio):
        args = [str(self.ffmpeg_dir / "ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", f"color=c=teal:s=1280x720:r={fps}:d={seconds}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
        args += ["-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            args += ["-c:a", "aac", "-ar", "48000", "-ac", "2"]
        else:
            args += ["-an"]
        args.append(str(path))
        subprocess.run(args, check=True, capture_output=True)

    def test_run_keeps_full_inputs_and_emits_social_video_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            project = create_project(Path(temp) / "Produkt żółty", remix.DEFAULTS)
            self.make_video(project / "Hooki" / "hook.mp4", seconds=1.2, fps=30, audio=True)
            self.make_video(project / "Klipy" / "clip.mp4", seconds=1.6, fps=60, audio=False)
            settings = {
                **remix.DEFAULTS,
                "ads_count": 1,
                "clips_per_ad": 1,
                "output_mode": "portrait",
                "output_dir": "Gotowe filmy",
                "seed": 17,
            }
            (project / "config.json").write_text(json.dumps(settings), encoding="utf-8")

            with patch.object(remix, "RESOURCES", self.ffmpeg_dir.parent.parent):
                output = remix.run(project / "config.json")

            video_path = output / "Film_001.mp4"
            self.assertTrue(video_path.is_file())
            info = remix.probe(video_path, str(self.ffmpeg_dir / "ffprobe.exe"))["data"]
            video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
            audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
            self.assertEqual((video["width"], video["height"]), (720, 1280))
            self.assertAlmostEqual(float(video["avg_frame_rate"].split("/")[0]) / float(video["avg_frame_rate"].split("/")[1]), 60.0, places=2)
            self.assertEqual(audio["codec_name"], "aac")
            self.assertAlmostEqual(float(info["format"]["duration"]), 2.8, delta=0.12)
            with (output / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["role"] for row in rows], ["hook", "clip"])
            self.assertEqual({row["fit"] for row in rows}, {"fill"})
            self.assertAlmostEqual(float(rows[0]["segment_seconds"]), 1.2, delta=0.1)
            self.assertAlmostEqual(float(rows[1]["segment_seconds"]), 1.6, delta=0.1)

    def test_run_applies_per_clip_trim_and_optional_ending(self):
        with tempfile.TemporaryDirectory() as temp:
            project = create_project(Path(temp) / "Produkt CTA", remix.DEFAULTS)
            self.make_video(project / "Hooki" / "hook.mp4", seconds=1.4, fps=30, audio=False)
            self.make_video(project / "Klipy" / "clip.mp4", seconds=2.0, fps=30, audio=True)
            self.make_video(project / "Zakończenia" / "ending.mp4", seconds=.6, fps=30, audio=False)
            settings = {
                **remix.DEFAULTS,
                "ads_count": 1,
                "clips_per_ad": 1,
                "output_mode": "source",
                "use_cta": True,
                "output_dir": "Gotowe filmy",
                "seed": 18,
            }
            (project / "config.json").write_text(json.dumps(settings), encoding="utf-8")
            (project / "assets.json").write_text(json.dumps({
                "Klipy/clip.mp4": {"trim": {"start": .25, "end": 1.25}, "fit": "fill", "x": .8, "y": .5}
            }), encoding="utf-8")

            with patch.object(remix, "RESOURCES", self.ffmpeg_dir.parent.parent):
                output = remix.run(project / "config.json")

            info = remix.probe(output / "Film_001.mp4", str(self.ffmpeg_dir / "ffprobe.exe"))["data"]
            video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1280, 720))
            self.assertAlmostEqual(float(info["format"]["duration"]), 3.0, delta=.15)
            with (output / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["role"] for row in rows], ["hook", "clip", "cta"])
            self.assertAlmostEqual(float(rows[1]["source_start_seconds"]), .25, delta=.01)
            self.assertAlmostEqual(float(rows[1]["source_duration_seconds"]), 1.0, delta=.01)
            self.assertEqual(rows[1]["fit"], "fill")
            self.assertEqual(rows[1]["crop_x"], "0.8")


if __name__ == "__main__":
    unittest.main()
