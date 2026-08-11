import unittest

import numpy as np

from track_mixer import (
    TRACK_GAIN_MAX_DB,
    TRACK_GAIN_MIN_DB,
    apply_track_mix_controls,
    normalize_track_metadata,
    track_pan_label,
)


class TrackMetadataTests(unittest.TestCase):
    def test_missing_or_malformed_metadata_gets_safe_defaults(self):
        default = normalize_track_metadata(
            {"name": "  ", "color": "not-a-colour", "gain_db": "nan", "pan": 99, "order": "bad"},
            fallback_name="Kick", fallback_color="#2A4D6E", fallback_order=3,
        )
        self.assertEqual(default["name"], "Kick")
        self.assertEqual(default["color"], "#2A4D6E")
        self.assertEqual(default["gain_db"], 0.0)
        self.assertEqual(default["pan"], 1.0)
        self.assertEqual(default["order"], 3)

    def test_metadata_clamps_audio_controls_and_keeps_valid_colour(self):
        metadata = normalize_track_metadata(
            {"name": "Voice", "color": "#aa33cc", "gain_db": 500, "pan": -500, "order": "8"},
            fallback_name="Fallback", fallback_color="#2A4D6E", fallback_order=0,
        )
        self.assertEqual(metadata["name"], "Voice")
        self.assertEqual(metadata["color"], "#AA33CC")
        self.assertEqual(metadata["gain_db"], TRACK_GAIN_MAX_DB)
        self.assertEqual(metadata["pan"], -1.0)
        self.assertEqual(metadata["order"], 8)


class TrackMixControlTests(unittest.TestCase):
    def test_gain_changes_level_without_mutating_input(self):
        source = np.array([[0.5, -0.5], [0.25, -0.25]], dtype=np.float32)
        result = apply_track_mix_controls(source, gain_db=6.020599913, pan=0)
        np.testing.assert_allclose(result, source * 2.0, atol=1e-5)
        np.testing.assert_array_equal(source, [[0.5, -0.5], [0.25, -0.25]])

    def test_balance_attenuates_only_the_opposite_stereo_channel(self):
        source = np.ones((2, 2), dtype=np.float32)
        left = apply_track_mix_controls(source, pan=-0.5)
        right = apply_track_mix_controls(source, pan=0.25)
        np.testing.assert_allclose(left, [[1.0, 0.5], [1.0, 0.5]])
        np.testing.assert_allclose(right, [[0.75, 1.0], [0.75, 1.0]])

    def test_mono_and_non_stereo_buffers_only_receive_gain(self):
        mono = np.array([0.5, -0.5], dtype=np.float32)
        surround = np.ones((1, 4), dtype=np.float32)
        np.testing.assert_allclose(apply_track_mix_controls(mono, gain_db=-6.020599913, pan=1), mono * 0.5)
        np.testing.assert_allclose(apply_track_mix_controls(surround, gain_db=0, pan=-1), surround)

    def test_pan_label(self):
        self.assertEqual(track_pan_label(0), "C")
        self.assertEqual(track_pan_label(-0.4), "L40")
        self.assertEqual(track_pan_label(0.25), "R25")
        self.assertEqual(track_pan_label(-99), "L100")


if __name__ == "__main__":
    unittest.main()
