import unittest
from fractions import Fraction
from pathlib import Path

from creative_factory.remix import choose_output_geometry, choose_output_fps, resolve_trim


class RenderPolicyTests(unittest.TestCase):
    def test_portrait_format_uses_modal_short_side_and_caps_at_full_hd(self):
        sizes = {
            Path("hook.mp4"): (1920, 1080),
            Path("clip-a.mp4"): (1920, 1080),
            Path("clip-b.mp4"): (720, 1280),
        }

        self.assertEqual(choose_output_geometry(sizes, "portrait"), (1080, 1920))

    def test_portrait_format_does_not_force_full_hd_for_smaller_sources(self):
        sizes = {Path("hook.mp4"): (1280, 720), Path("clip.mp4"): (1280, 720)}

        self.assertEqual(choose_output_geometry(sizes, "portrait"), (720, 1280))

    def test_source_format_keeps_most_common_dimensions_and_even_sizes(self):
        sizes = {Path("hook.mp4"): (1280, 720), Path("clip.mp4"): (640, 360)}

        self.assertEqual(choose_output_geometry(sizes, "source"), (1280, 720))

    def test_output_fps_is_highest_used_rate_capped_at_sixty(self):
        rates = {Path("hook.mp4"): Fraction(30000, 1001), Path("clip.mp4"): Fraction(60000, 1001)}

        self.assertEqual(choose_output_fps(rates.values()), Fraction(60000, 1001))
        self.assertEqual(choose_output_fps([Fraction(120, 1)]), Fraction(60, 1))

    def test_default_trim_uses_whole_source_and_custom_trim_is_clamped(self):
        duration = 5.25

        self.assertEqual(resolve_trim(duration, None), (0.0, duration))
        self.assertEqual(resolve_trim(duration, {"start": 1.0, "end": 4.0}), (1.0, 4.0))

    def test_rejects_invalid_trim_range(self):
        with self.assertRaises(ValueError):
            resolve_trim(4.0, {"start": 3.0, "end": 2.0})


if __name__ == "__main__":
    unittest.main()
