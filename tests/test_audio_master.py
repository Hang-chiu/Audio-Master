import math
import types
import unittest
from unittest import mock

import numpy as np
from pydub import AudioSegment
from scipy.signal import resample_poly

import audio_master as am


def measure_true_peak(samples, oversample=4):
    return am.AudioBalancerApp._measure_true_peak_db(
        None,
        samples,
        oversample,
    )


def full_true_peak_reference(samples, oversample=4):
    samples = np.asarray(samples)
    if samples.ndim == 1:
        samples = samples[:, np.newaxis]
    peak = float(np.max(np.abs(samples)))
    for channel in range(samples.shape[1]):
        upsampled = resample_poly(
            samples[:, channel].astype(np.float32),
            oversample,
            1,
            padtype="constant",
        )
        peak = max(peak, float(np.max(np.abs(upsampled))))
    return 20.0 * math.log10(max(peak, 1e-10))


class TruePeakTests(unittest.TestCase):
    def test_detects_inter_sample_peak(self):
        sample_rate = 48_000
        frame = np.arange(sample_rate, dtype=np.float64)
        samples = (
            0.98
            * np.sin(
                2 * np.pi * (sample_rate / 4) * frame / sample_rate
                + np.pi / 4
            )
        ).astype(np.float32)

        sample_peak_db = 20 * np.log10(np.max(np.abs(samples)))
        true_peak_db = measure_true_peak(samples)

        self.assertLess(sample_peak_db, -3.1)
        self.assertGreater(true_peak_db, sample_peak_db + 2.8)
        self.assertGreater(true_peak_db, -0.2)
        self.assertLess(true_peak_db, 0.2)

    def test_chunk_boundary_matches_full_resample(self):
        boundary = am._TRUE_PEAK_CHUNK_FRAMES
        rng = np.random.default_rng(20260729)
        samples = np.zeros(boundary + 512, dtype=np.float32)
        samples[boundary - 96:boundary + 96] = rng.uniform(
            -0.95,
            0.95,
            192,
        ).astype(np.float32)

        self.assertAlmostEqual(
            measure_true_peak(samples),
            full_true_peak_reference(samples),
            places=5,
        )

    def test_multichannel_uses_highest_channel(self):
        sample_rate = 48_000
        frame = np.arange(sample_rate, dtype=np.float64)
        high = (
            0.98
            * np.sin(
                2 * np.pi * (sample_rate / 4) * frame / sample_rate
                + np.pi / 4
            )
        ).astype(np.float32)
        stereo = np.column_stack((high * 0.2, high))

        self.assertAlmostEqual(
            measure_true_peak(stereo),
            full_true_peak_reference(stereo),
            places=5,
        )

    def test_resampling_memory_is_chunk_bounded(self):
        real_resample_poly = am.resample_poly
        seen = []

        def spy(block, *args, **kwargs):
            result = real_resample_poly(block, *args, **kwargs)
            seen.append((len(block), block.dtype, len(result)))
            return result

        samples = np.zeros(3 * 1024 + 17, dtype=np.float32)
        with (
            mock.patch.object(am, "_TRUE_PEAK_CHUNK_FRAMES", 1024),
            mock.patch.object(am, "_TRUE_PEAK_OVERLAP_FRAMES", 64),
            mock.patch.object(am, "resample_poly", spy),
        ):
            measure_true_peak(samples)

        self.assertEqual(len(seen), 4)
        self.assertLessEqual(max(item[0] for item in seen), 1024 + 2 * 64)
        self.assertLessEqual(max(item[2] for item in seen), (1024 + 2 * 64) * 4)
        self.assertTrue(all(item[1] == np.float32 for item in seen))

    def test_empty_silence_and_single_sample(self):
        self.assertEqual(measure_true_peak(None), -100.0)
        self.assertEqual(
            measure_true_peak(np.array([], dtype=np.float32)),
            -100.0,
        )
        self.assertAlmostEqual(
            measure_true_peak(np.zeros(32, dtype=np.float32)),
            -200.0,
        )
        self.assertAlmostEqual(
            measure_true_peak(np.array([0.5], dtype=np.float32)),
            -6.020599913,
            places=6,
        )


class EditRegionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(am.AudioBalancerApp)
        self.audio = AudioSegment.silent(duration=1000, frame_rate=48_000)
        self.path = "/tmp/audio-master-test.wav"

    def entry(self, saved_marker=...):
        entry = {
            "path": self.path,
            "audio": self.audio,
            "duration": "00:01",
            "_table": None,
        }
        if saved_marker is not ...:
            entry["edit_regions"] = saved_marker
        return entry

    def test_edit_region_decoder_preserves_three_states(self):
        self.assertIsNone(self.app._entry_edit_regions(self.entry()))
        self.assertIsNone(self.app._entry_edit_regions(self.entry(None)))
        self.assertEqual(self.app._entry_edit_regions(self.entry([])), [])
        self.assertIsNone(self.app._entry_edit_regions(self.entry({"bad": True})))

        full = am.EditRegion(self.path, 0.0, 1.0, 0.0).to_dict()
        self.assertIsNone(self.app._entry_edit_regions(self.entry([full])))

        edited = am.EditRegion(self.path, 0.2, 0.8, 0.0).to_dict()
        decoded = self.app._entry_edit_regions(self.entry([edited]))
        self.assertEqual(len(decoded), 1)
        self.assertAlmostEqual(decoded[0].src_start, 0.2)

    def test_empty_edit_renders_silence_instead_of_original(self):
        unedited = self.entry(None)
        self.assertIs(self.app._render_edited_audio(unedited), self.audio)

        emptied = self.entry([])
        rendered = self.app._render_edited_audio(emptied)
        self.assertIsNot(rendered, self.audio)
        self.assertLessEqual(
            len(rendered.raw_data),
            self.audio.sample_width * self.audio.channels,
        )
        self.assertFalse(any(rendered.raw_data))

    def test_sync_and_export_freeze_keep_none_distinct_from_empty(self):
        editor = object.__new__(am.EditWindow)
        empty_entry = self.entry()
        editor.app = self.app
        editor.tracks = [{"entry": empty_entry, "regions": []}]
        editor.sync_entries()
        self.assertEqual(empty_entry["edit_regions"], [])
        self.assertEqual(empty_entry["duration"], "00:00")

        unedited_entry = {
            **self.entry(None),
            "name": "unedited.wav",
            "status": "🟢 就緒",
            "export": True,
            "lufs": -16.0,
        }
        empty_entry.update({
            "name": "empty.wav",
            "status": "🟢 就緒",
            "export": True,
            "lufs": -16.0,
        })
        workspace = types.SimpleNamespace(
            name="Test",
            audio_files=[unedited_entry, empty_entry],
        )
        self.app._sync_open_edit_window_entries = lambda: None
        self.app._export_subpath_for = lambda _workspace, _path: ""

        jobs = self.app._build_export_jobs([workspace], "")
        frozen = {entry["name"]: entry for entry in jobs[0]["entries"]}
        self.assertIsNone(frozen["unedited.wav"]["edit_regions"])
        self.assertEqual(frozen["empty.wav"]["edit_regions"], [])

    def test_duration_follows_edits_and_restores_source_length(self):
        five_seconds = AudioSegment.silent(duration=5000, frame_rate=48_000)
        entry = {
            **self.entry([]),
            "audio": five_seconds,
            "duration": "00:05",
        }
        self.assertEqual(self.app._entry_duration_label(entry), "00:00")

        entry["edit_regions"] = [
            am.EditRegion(self.path, 0.0, 3.2, 0.0).to_dict(),
        ]
        self.assertEqual(self.app._entry_duration_label(entry), "00:03")

        entry["edit_regions"] = [
            am.EditRegion(self.path, 0.0, 5.0, 0.0).to_dict(),
        ]
        entry["duration"] = "00:00"
        self.assertEqual(self.app._entry_duration_label(entry), "00:05")


class ActiveRegionCommandTests(unittest.TestCase):
    def make_editor(self, regions, active):
        editor = object.__new__(am.EditWindow)
        editor.tracks = [{"regions": list(regions)}]
        editor.active_region = active
        editor.selection = None
        editor.clipboard = []
        editor.playhead_track = 0
        editor.undo_stack = []
        editor.redo_stack = []
        editor.redraw = lambda: None
        return editor

    def test_copy_preserves_fades_and_normalizes_offset(self):
        region = am.EditRegion(
            "/tmp/source.wav",
            1.0,
            3.0,
            7.0,
            fade_in=0.3,
            fade_out=0.4,
            fade_in_curve=0.7,
            fade_out_curve=-0.6,
        )
        editor = self.make_editor([region], region)
        editor.cmd_copy()

        copied = editor.clipboard[0]
        self.assertIsNot(copied, region)
        self.assertEqual(copied.track_offset, 0.0)
        self.assertEqual(copied.fade_in, region.fade_in)
        self.assertEqual(copied.fade_out, region.fade_out)
        self.assertEqual(copied.fade_in_curve, region.fade_in_curve)
        self.assertEqual(copied.fade_out_curve, region.fade_out_curve)

    def test_delete_only_removes_active_region_and_undo_restores(self):
        active = am.EditRegion("/tmp/a.wav", 0.0, 1.0, 0.0)
        sibling = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 0.5)
        editor = self.make_editor([active, sibling], active)

        editor.cmd_delete()
        self.assertEqual(editor.tracks[0]["regions"], [sibling])
        self.assertIsNone(editor.active_region)

        editor.cmd_undo()
        self.assertEqual(len(editor.tracks[0]["regions"]), 2)
        editor.cmd_redo()
        self.assertEqual(len(editor.tracks[0]["regions"]), 1)
        self.assertEqual(editor.tracks[0]["regions"][0].source_path, "/tmp/b.wav")

    def test_cut_last_region_leaves_explicit_empty_track(self):
        active = am.EditRegion("/tmp/a.wav", 0.0, 1.0, 4.0)
        editor = self.make_editor([active], active)

        editor.cmd_cut()
        self.assertEqual(editor.tracks[0]["regions"], [])
        self.assertEqual(len(editor.clipboard), 1)
        self.assertEqual(editor.clipboard[0].track_offset, 0.0)
        self.assertEqual(len(editor.undo_stack), 1)

    def test_stale_active_region_is_a_noop(self):
        stale = am.EditRegion("/tmp/stale.wav", 0.0, 1.0, 0.0)
        editor = self.make_editor([], stale)
        editor.cmd_delete()
        self.assertEqual(editor.undo_stack, [])


if __name__ == "__main__":
    unittest.main()
