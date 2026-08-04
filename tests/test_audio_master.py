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
        self.app.workspaces = [types.SimpleNamespace(current_file_path=None)]
        self.app.active_ws_idx = 0
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

    def test_crossfade_fields_round_trip_and_clone(self):
        region = am.EditRegion(
            self.path, 0.0, 1.0, 0.0,
            fade_in=0.1, fade_out=0.2,
            crossfade_in=0.3, crossfade_out=0.4,
        )

        restored = am.EditRegion.from_dict(region.to_dict())
        cloned = restored.clone()

        self.assertAlmostEqual(restored.crossfade_in, 0.3)
        self.assertAlmostEqual(restored.crossfade_out, 0.4)
        self.assertAlmostEqual(cloned.crossfade_in, 0.3)
        self.assertAlmostEqual(cloned.crossfade_out, 0.4)
        self.assertAlmostEqual(cloned.effective_fade_in, 0.3)
        self.assertAlmostEqual(cloned.effective_fade_out, 0.4)


class ActiveRegionCommandTests(unittest.TestCase):
    def make_editor(self, regions, active):
        editor = object.__new__(am.EditWindow)
        editor.tracks = [{"regions": list(regions)}]
        editor.active_region = active
        editor.selected_regions = [active] if active in regions else []
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

        track_delta, copied = editor.clipboard[0]
        self.assertEqual(track_delta, 0)
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
        self.assertEqual(editor.clipboard[0][0], 0)
        self.assertEqual(editor.clipboard[0][1].track_offset, 0.0)
        self.assertEqual(len(editor.undo_stack), 1)

    def test_stale_active_region_is_a_noop(self):
        stale = am.EditRegion("/tmp/stale.wav", 0.0, 1.0, 0.0)
        editor = self.make_editor([], stale)
        editor.cmd_delete()
        self.assertEqual(editor.undo_stack, [])


class CrossfadeTests(unittest.TestCase):
    def make_editor(self, regions):
        editor = object.__new__(am.EditWindow)
        editor.tracks = [{"regions": list(regions)}]
        editor.active_region = None
        editor.selected_regions = []
        return editor

    def test_overlap_keeps_both_regions_and_sets_complementary_crossfade(self):
        left = am.EditRegion("/tmp/a.wav", 0.0, 2.0, 0.0, fade_out=0.2)
        right = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 1.25, fade_in=0.1)
        editor = self.make_editor([right, left])  # list order must not affect time order

        changed = editor._resolve_track_overlaps(0, right)

        self.assertTrue(changed)
        self.assertEqual(editor.tracks[0]["regions"], [right, left])
        self.assertAlmostEqual(left.crossfade_out, 0.75)
        self.assertAlmostEqual(right.crossfade_in, 0.75)
        self.assertAlmostEqual(left.fade_out, 0.2)  # manual Fade remains independent
        self.assertAlmostEqual(right.fade_in, 0.1)
        self.assertEqual((left.src_start, left.src_end), (0.0, 2.0))
        self.assertEqual((right.src_start, right.src_end), (0.0, 2.0))

    def test_moving_apart_clears_only_automatic_crossfade(self):
        left = am.EditRegion("/tmp/a.wav", 0.0, 2.0, 0.0, fade_out=0.25)
        right = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 1.5, fade_in=0.15)
        editor = self.make_editor([left, right])
        editor._resolve_track_overlaps(0, right)

        right.track_offset = 2.5
        editor._resolve_track_overlaps(0, right)

        self.assertEqual(left.crossfade_out, 0.0)
        self.assertEqual(right.crossfade_in, 0.0)
        self.assertAlmostEqual(left.fade_out, 0.25)
        self.assertAlmostEqual(right.fade_in, 0.15)

    def test_touching_and_contained_regions_are_kept_without_auto_crossfade(self):
        outer = am.EditRegion("/tmp/outer.wav", 0.0, 4.0, 0.0)
        contained = am.EditRegion("/tmp/inside.wav", 0.0, 1.0, 1.0)
        touching = am.EditRegion("/tmp/touch.wav", 0.0, 1.0, 4.0)
        editor = self.make_editor([outer, contained, touching])

        editor._resolve_track_overlaps(0, contained)

        self.assertEqual(len(editor.tracks[0]["regions"]), 3)
        for region in (outer, contained, touching):
            self.assertEqual(region.crossfade_in, 0.0)
            self.assertEqual(region.crossfade_out, 0.0)

    def test_flex_overlap_uses_playback_length(self):
        left = am.EditRegion(
            "/tmp/a.wav", 0.0, 1.0, 0.0, time_stretch_ratio=2.0,
        )
        right = am.EditRegion("/tmp/b.wav", 0.0, 1.0, 1.25)
        editor = self.make_editor([left, right])

        editor._resolve_track_overlaps(0, right)

        self.assertAlmostEqual(left.crossfade_out, 0.75)
        self.assertAlmostEqual(right.crossfade_in, 0.75)

    def test_linear_crossfade_renders_constant_gain_through_overlap(self):
        left = am.EditRegion("/tmp/a.wav", 0.0, 1.0, 0.0)
        right = am.EditRegion("/tmp/b.wav", 0.0, 1.0, 0.5)
        editor = self.make_editor([left, right])
        editor._resolve_track_overlaps(0, right)

        app = object.__new__(am.AudioBalancerApp)
        app._decode_source_samples = lambda _path, _cache: (
            np.ones(10, dtype=np.float32), 10, 1,
        )
        rendered = app._render_region_list([left, right], 10, 1)

        np.testing.assert_allclose(rendered, np.ones(15), atol=1e-6)

    def test_crossfade_overrides_longer_manual_fades_only_while_overlapping(self):
        left = am.EditRegion("/tmp/a.wav", 0.0, 2.0, 0.0, fade_out=1.0)
        right = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 1.5, fade_in=1.0)
        editor = self.make_editor([left, right])
        editor._resolve_track_overlaps(0, right)

        self.assertAlmostEqual(left.effective_fade_out, 0.5)
        self.assertAlmostEqual(right.effective_fade_in, 0.5)
        app = object.__new__(am.AudioBalancerApp)
        app._decode_source_samples = lambda _path, _cache: (
            np.ones(20, dtype=np.float32), 10, 1,
        )
        rendered = app._render_region_list([left, right], 10, 1)
        np.testing.assert_allclose(rendered, np.ones(35), atol=1e-6)

        right.track_offset = 2.5
        editor._resolve_track_overlaps(0, right)
        self.assertAlmostEqual(left.effective_fade_out, 1.0)
        self.assertAlmostEqual(right.effective_fade_in, 1.0)

    def test_three_way_overlap_does_not_create_misleading_pairwise_crossfades(self):
        first = am.EditRegion("/tmp/a.wav", 0.0, 2.0, 0.0)
        second = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 1.0)
        third = am.EditRegion("/tmp/c.wav", 0.0, 2.0, 1.5)
        editor = self.make_editor([first, second, third])

        editor._resolve_track_overlaps(0, second)

        self.assertEqual(editor.tracks[0]["_crossfade_pairs"], [])
        for region in (first, second, third):
            self.assertEqual(region.crossfade_in, 0.0)
            self.assertEqual(region.crossfade_out, 0.0)

    def test_clean_tracks_reuse_cached_crossfade_pairs(self):
        first = am.EditRegion("/tmp/a.wav", 0.0, 1.0, 0.0)
        second = am.EditRegion("/tmp/b.wav", 0.0, 1.0, 0.5)
        editor = object.__new__(am.EditWindow)
        editor.tracks = [
            {"regions": [first, second], "_crossfade_pairs": [], "_crossfade_dirty": False},
            {"regions": [], "_crossfade_pairs": [], "_crossfade_dirty": False},
        ]

        with mock.patch.object(
            editor, "_crossfade_pairs_for_regions", wraps=editor._crossfade_pairs_for_regions,
        ) as pair_finder:
            editor._refresh_all_crossfades()
            pair_finder.assert_not_called()

            editor._mark_track_crossfade_dirty(0)
            editor._refresh_all_crossfades()
            pair_finder.assert_called_once_with([first, second])

    def test_crossfade_overlay_draws_box_and_two_curves(self):
        class Canvas:
            def __init__(self):
                self.rectangles = []
                self.lines = []

            def create_rectangle(self, *args, **kwargs):
                self.rectangles.append((args, kwargs))

            def create_line(self, *args, **kwargs):
                self.lines.append((args, kwargs))

        editor = object.__new__(am.EditWindow)
        editor.canvas = Canvas()
        editor.px_per_sec = 100.0
        left = am.EditRegion("/tmp/a.wav", 0.0, 2.0, 0.0)
        right = am.EditRegion("/tmp/b.wav", 0.0, 2.0, 1.25)

        editor._draw_crossfade_overlay(left, right, 1.25, 2.0, 10.0, 80.0)

        self.assertEqual(len(editor.canvas.rectangles), 1)
        self.assertEqual(len(editor.canvas.lines), 2)
        fade_out = editor.canvas.lines[0][0]
        fade_in = editor.canvas.lines[1][0]
        self.assertAlmostEqual(fade_out[1], 10.0)
        self.assertAlmostEqual(fade_out[-1], 80.0)
        self.assertAlmostEqual(fade_in[1], 80.0)
        self.assertAlmostEqual(fade_in[-1], 10.0)


class EditWindowShortcutTests(unittest.TestCase):
    def test_cmd_one_fallback_toggles_and_old_cmd_four_does_not(self):
        app = object.__new__(am.AudioBalancerApp)
        app._handle_edit_window_shortcut = mock.Mock(return_value="break")

        result_char = app._handle_edit_window_digit_fallback(
            types.SimpleNamespace(char="1", keysym="??"),
        )
        result_keypad = app._handle_edit_window_digit_fallback(
            types.SimpleNamespace(char="", keysym="KP_1"),
        )
        result_old = app._handle_edit_window_digit_fallback(
            types.SimpleNamespace(char="4", keysym="4"),
        )

        self.assertEqual(result_char, "break")
        self.assertEqual(result_keypad, "break")
        self.assertIsNone(result_old)
        self.assertEqual(app._handle_edit_window_shortcut.call_count, 2)


class MainWindowUndoTests(unittest.TestCase):
    class Tree:
        def __init__(self):
            self.nodes = {
                "folder": {
                    "parent": "", "children": ["file-a", "file-b"],
                    "text": "Folder", "values": (), "open": True,
                    "tags": ("dirfolder",), "image": "",
                },
                "file-a": {
                    "parent": "folder", "children": [],
                    "text": "a.wav", "values": (), "open": False,
                    "tags": ("dimfile",), "image": "",
                },
                "file-b": {
                    "parent": "folder", "children": [],
                    "text": "b.wav", "values": (), "open": False,
                    "tags": ("dimfile",), "image": "",
                },
            }
            self.roots = ["folder"]
            self.selected = ("file-a",)
            self.focused = None
            self.seen = None
            self.next_iid = 1

        def selection(self):
            return self.selected

        def selection_set(self, iids):
            self.selected = tuple(iids) if not isinstance(iids, str) else (iids,)

        def exists(self, iid):
            return iid in self.nodes

        def get_children(self, parent=""):
            return tuple(self.roots if parent == "" else self.nodes[parent]["children"])

        def parent(self, iid):
            return self.nodes[iid]["parent"]

        def index(self, iid):
            parent = self.parent(iid)
            return list(self.get_children(parent)).index(iid)

        def item(self, iid):
            node = self.nodes[iid]
            return {key: node[key] for key in ("text", "values", "open", "tags", "image")}

        def tag_has(self, tag, iid):
            return tag in self.nodes[iid]["tags"]

        def delete(self, iid):
            for child in list(self.get_children(iid)):
                self.delete(child)
            parent = self.nodes[iid]["parent"]
            siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            siblings.remove(iid)
            del self.nodes[iid]

        def insert(self, parent, index, iid=None, **options):
            if iid is None:
                iid = f"new-{self.next_iid}"
                self.next_iid += 1
            if iid in self.nodes:
                raise am.tk.TclError("duplicate iid")
            siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            if index == "end":
                index = len(siblings)
            siblings.insert(int(index), iid)
            self.nodes[iid] = {
                "parent": parent, "children": [],
                "text": options.get("text", ""),
                "values": tuple(options.get("values", ())),
                "open": bool(options.get("open", False)),
                "tags": tuple(options.get("tags", ())),
                "image": options.get("image", ""),
            }
            return iid

        def move(self, iid, parent, index):
            old_parent = self.nodes[iid]["parent"]
            old_siblings = self.roots if old_parent == "" else self.nodes[old_parent]["children"]
            old_siblings.remove(iid)
            new_siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            new_siblings.insert(min(int(index), len(new_siblings)), iid)
            self.nodes[iid]["parent"] = parent

        def focus(self, iid):
            self.focused = iid

        def see(self, iid):
            self.seen = iid

    def make_app(self, tree):
        paths = {
            "folder": "/tmp/folder",
            "file-a": "/tmp/folder/a.wav",
            "file-b": "/tmp/folder/b.wav",
        }
        workspace = am.Workspace("Test", dir_tree=tree, tree_item_paths=paths)
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [workspace]
        app.active_ws_idx = 0
        app._undo_stack = []
        app._refresh_dir_tree_counts = mock.Mock()
        app._schedule_autosave = mock.Mock()
        return app, workspace

    def test_left_tree_file_delete_can_be_undone(self):
        tree = self.Tree()
        app, workspace = self.make_app(tree)

        self.assertEqual(app._remove_tree_selection(workspace), "break")
        self.assertFalse(tree.exists("file-a"))
        self.assertNotIn("file-a", workspace.tree_item_paths)
        self.assertEqual(app._undo_stack[-1][0], "remove_tree_items")

        app._undo()

        self.assertEqual(tree.get_children("folder"), ("file-a", "file-b"))
        self.assertEqual(workspace.tree_item_paths["file-a"], "/tmp/folder/a.wav")
        self.assertEqual(tree.selection(), ("file-a",))

    def test_parent_and_selected_child_are_snapshotted_only_once(self):
        tree = self.Tree()
        tree.selected = ("folder", "file-a")
        app, workspace = self.make_app(tree)

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app._remove_tree_selection(workspace)
        _, (_, snapshots) = app._undo_stack[-1]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["iid"], "folder")

        app._undo()
        self.assertEqual(tree.get_children(""), ("folder",))
        self.assertEqual(tree.get_children("folder"), ("file-a", "file-b"))

    def test_left_tree_reverse_multi_delete_restores_original_root_order(self):
        tree = self.Tree()
        roots = ["A", "B", "C", "D", "E"]
        tree.roots = list(roots)
        tree.nodes = {
            iid: {
                "parent": "", "children": [], "text": iid, "values": (),
                "open": True, "tags": ("dirfolder",), "image": "",
            }
            for iid in roots
        }
        tree.selected = ("D", "B")  # 故意與畫面順位相反
        app, workspace = self.make_app(tree)
        workspace.tree_item_paths = {iid: f"/tmp/{iid}" for iid in roots}

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app._remove_tree_selection(workspace)
        app._undo()

        self.assertEqual(tree.get_children(""), tuple(roots))


class MainWorkspaceFileUndoOrderTests(unittest.TestCase):
    class FileTable:
        COLUMN_INDEX = {
            "檔案": 0,
            "Duration": 1,
            "Status": 2,
            "原始 LUFS": 3,
            "原始 True Peak": 4,
            "目標 LUFS": 5,
            "目標 True Peak": 6,
        }

        def __init__(self, groups, selected=()):
            self.roots = []
            self.nodes = {}
            self.selected = tuple(selected)
            self.focused = None
            self.seen = None
            self.next_iid = 1
            for folder_path, file_paths in groups:
                folder_iid = f"__folder__::{folder_path}"
                self.roots.append(folder_iid)
                self.nodes[folder_iid] = {
                    "parent": "", "children": list(file_paths), "text": "",
                    "values": (f"📁 {folder_path.rsplit('/', 1)[-1]}", "", "", "", "", "", ""),
                    "open": True, "tags": ("folder", am._CHECK_TAG_ON), "image": "",
                }
                for path in file_paths:
                    self.nodes[path] = {
                        "parent": folder_iid, "children": [], "text": "",
                        "values": (path.rsplit('/', 1)[-1], "00:01", "🟢 就緒", "-16.0 LUFS", "--", "-16.0 LUFS", "--"),
                        "open": False, "tags": ("file", am._CHECK_TAG_ON), "image": "",
                    }

        def selection(self):
            return self.selected

        def selection_set(self, iids):
            self.selected = tuple(iids) if not isinstance(iids, str) else (iids,)

        def exists(self, iid):
            return iid in self.nodes

        def tag_has(self, tag, iid):
            return tag in self.nodes[iid]["tags"]

        def get_children(self, parent=""):
            return tuple(self.roots if parent == "" else self.nodes[parent]["children"])

        def parent(self, iid):
            return self.nodes[iid]["parent"]

        def index(self, iid):
            parent = self.parent(iid)
            siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            return siblings.index(iid)

        def item(self, iid, option=None, **kwargs):
            node = self.nodes[iid]
            if kwargs:
                for key, value in kwargs.items():
                    node[key] = tuple(value) if key in ("tags", "values") else value
                return None
            if option is not None:
                return node[option]
            return {key: node[key] for key in ("text", "values", "open", "tags", "image")}

        def set(self, iid, column, value):
            values = list(self.nodes[iid]["values"])
            values[self.COLUMN_INDEX[column]] = value
            self.nodes[iid]["values"] = tuple(values)

        def delete(self, iid):
            for child in list(self.get_children(iid)):
                self.delete(child)
            parent = self.nodes[iid]["parent"]
            siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            siblings.remove(iid)
            del self.nodes[iid]

        def insert(self, parent, index, iid=None, **options):
            if iid is None:
                iid = f"new-{self.next_iid}"
                self.next_iid += 1
            if iid in self.nodes:
                raise am.tk.TclError("duplicate iid")
            siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            if index == "end":
                index = len(siblings)
            siblings.insert(int(index), iid)
            self.nodes[iid] = {
                "parent": parent, "children": [], "text": options.get("text", ""),
                "values": tuple(options.get("values", ())), "open": bool(options.get("open", False)),
                "tags": tuple(options.get("tags", ())), "image": options.get("image", ""),
            }
            return iid

        def move(self, iid, parent, index):
            old_parent = self.nodes[iid]["parent"]
            old_siblings = self.roots if old_parent == "" else self.nodes[old_parent]["children"]
            old_siblings.remove(iid)
            new_siblings = self.roots if parent == "" else self.nodes[parent]["children"]
            new_siblings.insert(min(int(index), len(new_siblings)), iid)
            self.nodes[iid]["parent"] = parent

        def focus(self, iid):
            self.focused = iid

        def see(self, iid):
            self.seen = iid

    @staticmethod
    def paths(folder, names):
        return [f"/tmp/{folder}/{name}.wav" for name in names]

    def make_app(self, groups, selected):
        table = self.FileTable(groups, selected)
        entries = []
        for _folder, paths in groups:
            for path in paths:
                entries.append({
                    "path": path, "name": path.rsplit("/", 1)[-1], "duration": "00:01",
                    "status": "🟢 就緒", "lufs": -16.0, "target_lufs": -16.0,
                    "export": True, "true_peak": None,
                })
        workspace = am.Workspace(
            "Test", audio_files=entries,
            audio_by_path={entry["path"]: entry for entry in entries},
            file_table=table,
        )
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [workspace]
        app.active_ws_idx = 0
        app._undo_stack = []
        app._check_icon_on = object()
        app._check_icon_off = object()
        app._true_peak_displays = mock.Mock(return_value=("--", "--"))
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        app._update_empty_hint = mock.Mock()
        app.check_export_ready = mock.Mock()
        app._schedule_autosave = mock.Mock()
        return app, workspace, table, [entry["path"] for entry in entries]

    def test_single_file_undo_restores_original_child_and_audio_order(self):
        a = self.paths("A", ["a0", "a1", "a2"])
        b = self.paths("B", ["b0"])
        app, workspace, table, original = self.make_app(
            [("/tmp/A", a), ("/tmp/B", b)], (a[1],),
        )

        app.remove_selected_files()
        self.assertEqual(table.get_children("__folder__::/tmp/A"), (a[0], a[2]))
        app._undo()

        self.assertEqual(table.get_children(""), ("__folder__::/tmp/A", "__folder__::/tmp/B"))
        self.assertEqual(table.get_children("__folder__::/tmp/A"), tuple(a))
        self.assertEqual([entry["path"] for entry in workspace.audio_files], original)

    def test_deleted_folder_group_undo_restores_original_root_position(self):
        a = self.paths("A", ["a0"])
        b = self.paths("B", ["b0", "b1"])
        c = self.paths("C", ["c0"])
        folder_b = "__folder__::/tmp/B"
        app, workspace, table, original = self.make_app(
            [("/tmp/A", a), ("/tmp/B", b), ("/tmp/C", c)], (folder_b,),
        )

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app.remove_selected_files()
        self.assertEqual(table.get_children(""), ("__folder__::/tmp/A", "__folder__::/tmp/C"))
        app._undo()

        self.assertEqual(
            table.get_children(""),
            ("__folder__::/tmp/A", "__folder__::/tmp/B", "__folder__::/tmp/C"),
        )
        self.assertEqual(table.get_children(folder_b), tuple(b))
        self.assertEqual([entry["path"] for entry in workspace.audio_files], original)

    def test_batch_undo_uses_original_order_not_selection_order(self):
        a = self.paths("A", ["a0", "a1", "a2", "a3"])
        b = self.paths("B", ["b0", "b1"])
        app, workspace, table, original = self.make_app(
            [("/tmp/A", a), ("/tmp/B", b)], (b[1], a[2], a[1]),
        )

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app.remove_selected_files()
        app._undo()

        self.assertEqual(table.get_children("__folder__::/tmp/A"), tuple(a))
        self.assertEqual(table.get_children("__folder__::/tmp/B"), tuple(b))
        self.assertEqual([entry["path"] for entry in workspace.audio_files], original)

    def test_two_deleted_groups_restore_root_order_even_when_selected_backwards(self):
        a = self.paths("A", ["a0"])
        b = self.paths("B", ["b0"])
        c = self.paths("C", ["c0"])
        folders = ("__folder__::/tmp/C", "__folder__::/tmp/A")
        app, workspace, table, original = self.make_app(
            [("/tmp/A", a), ("/tmp/B", b), ("/tmp/C", c)], folders,
        )

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app.remove_selected_files()
        app._undo()

        self.assertEqual(
            table.get_children(""),
            ("__folder__::/tmp/A", "__folder__::/tmp/B", "__folder__::/tmp/C"),
        )
        self.assertEqual([entry["path"] for entry in workspace.audio_files], original)

    def test_undo_repositions_a_folder_reimported_before_undo(self):
        a = self.paths("A", ["a0"])
        b = self.paths("B", ["b0", "b1"])
        c = self.paths("C", ["c0"])
        folder_b = "__folder__::/tmp/B"
        app, workspace, table, original = self.make_app(
            [("/tmp/A", a), ("/tmp/B", b), ("/tmp/C", c)], (folder_b,),
        )

        with mock.patch.object(am.messagebox, "askyesno", return_value=True):
            app.remove_selected_files()
        replacement = {
            "path": b[0], "name": "b0.wav", "duration": "00:01", "status": "🟢 就緒",
            "lufs": -16.0, "target_lufs": -16.0, "export": True, "true_peak": None,
        }
        workspace.audio_files.append(replacement)
        workspace.audio_by_path[b[0]] = replacement
        app._insert_file_row_into(
            table, b[0], True, "00:01", "🟢 就緒", "-16.0 LUFS", "-16.0 LUFS",
        )
        self.assertEqual(table.get_children(""), ("__folder__::/tmp/A", "__folder__::/tmp/C", folder_b))

        app._undo()

        self.assertEqual(
            table.get_children(""),
            ("__folder__::/tmp/A", folder_b, "__folder__::/tmp/C"),
        )
        self.assertEqual(table.get_children(folder_b), tuple(b))
        self.assertEqual([entry["path"] for entry in workspace.audio_files], original)


class PerformanceRegressionTests(unittest.TestCase):
    def test_stale_analysis_callback_does_not_overwrite_reimported_entry(self):
        class Table:
            def __init__(self):
                self.set_calls = []

            def exists(self, iid):
                return True

            def set(self, *args):
                self.set_calls.append(args)

        path = "/tmp/reimport.wav"
        old_entry = {"path": path}
        new_entry = {"path": path}
        table = Table()
        workspace = types.SimpleNamespace(file_table=table, audio_by_path={path: new_entry})
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [workspace]

        app.update_table_row(
            path, "00:01", "🟢 就緒", "-16.0 LUFS", "-16.0 LUFS",
            table, expected_entry=old_entry,
        )

        self.assertEqual(table.set_calls, [])

    def test_true_peak_overlay_wheel_returns_break_once(self):
        app = object.__new__(am.AudioBalancerApp)
        table = object()
        event = types.SimpleNamespace(widget=types.SimpleNamespace(_am_table=table))
        app._forward_wheel_to_table = mock.Mock(return_value="break")

        result = app._tp_overlay_wheel(event, shift=False)

        self.assertEqual(result, "break")
        app._forward_wheel_to_table.assert_called_once_with(event, table, shift=False)

    def test_right_panel_wheel_scrolls_custom_canvas(self):
        canvas = mock.Mock()

        result = am.AudioBalancerApp._scroll_canvas_by_wheel(
            canvas, types.SimpleNamespace(delta=-3, num=0),
        )

        self.assertEqual(result, "break")
        canvas.yview_scroll.assert_called_once_with(1, "units")

    def test_right_panel_touchpad_scroll_preserves_pixel_delta(self):
        class Canvas:
            def __init__(self):
                self.position = None

            def yview(self):
                return 0.2, 0.6

            def winfo_height(self):
                return 200

            def yview_moveto(self, position):
                self.position = position

        canvas = Canvas()
        packed = 10  # X=0、Y=+10px

        result = am.AudioBalancerApp._scroll_canvas_by_touchpad(
            canvas, types.SimpleNamespace(delta=packed),
        )

        self.assertEqual(result, "break")
        self.assertAlmostEqual(canvas.position, 0.15)

    def test_small_true_peak_shift_delta_is_not_amplified(self):
        class Table:
            def __init__(self):
                self.new_first = None

            def xview(self):
                return 0.2, 0.7

            def winfo_width(self):
                return 500

            def xview_moveto(self, value):
                self.new_first = value

        app = object.__new__(am.AudioBalancerApp)
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        table = Table()

        app._scroll_table_by_wheel(
            table, types.SimpleNamespace(delta=3, num=0), shift=True,
        )

        self.assertAlmostEqual(table.new_first, 0.1988, places=6)
        app._schedule_true_peak_overlay_refresh.assert_called_once_with()

    def test_true_peak_touchpad_scroll_preserves_both_axes(self):
        class Table:
            def __init__(self):
                self.x_calls = []
                self.y_calls = []

            def xview_scroll(self, amount, unit):
                self.x_calls.append((amount, unit))

            def yview_scroll(self, amount, unit):
                self.y_calls.append((amount, unit))

        app = object.__new__(am.AudioBalancerApp)
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        table = Table()
        # Tk 9 packed delta：高 16-bit 是 X=+2，低 16-bit 是 Y=-3。
        packed = (2 << 16) | ((-3) & 0xFFFF)

        result = app._scroll_table_by_touchpad(
            table, types.SimpleNamespace(serial=10, delta=packed),
        )

        self.assertEqual(result, "break")
        self.assertEqual(table.x_calls, [(-2, "units")])
        self.assertEqual(table.y_calls, [(3, "units")])
        app._schedule_true_peak_overlay_refresh.assert_called_once_with()

    def test_main_table_fallback_does_not_consume_editor_scroll(self):
        class Table:
            def winfo_exists(self):
                return True

            def winfo_rootx(self):
                return 0

            def winfo_rooty(self):
                return 0

            def winfo_width(self):
                return 800

            def winfo_height(self):
                return 600

        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [types.SimpleNamespace(file_table=Table())]
        app.active_ws_idx = 0
        app._touchpad_scroll_supported = False
        app._is_frontmost = mock.Mock(return_value=False)
        app._scroll_table_by_wheel = mock.Mock()
        app._wheel_dbg = mock.Mock()
        callbacks = []

        def bind_all(sequence, callback, add=None):
            callbacks.append((sequence, callback))

        app.bind_all = bind_all
        app._enable_center_table_wheel_fallback()

        result = callbacks[0][1](types.SimpleNamespace(
            widget=object(), x_root=100, y_root=100, delta=3, num=0,
        ))

        self.assertIsNone(result)
        app._scroll_table_by_wheel.assert_not_called()

    def test_visible_row_scan_does_not_scale_with_total_file_count(self):
        class Table:
            def __init__(self):
                self.calls = 0

            def winfo_height(self):
                return 240

            def identify_row(self, y):
                self.calls += 1
                row = y // 20
                return "folder" if row == 0 else f"/tmp/{row}.wav"

        app = object.__new__(am.AudioBalancerApp)
        app._current_ui_scale = 1.0
        table = Table()
        valid = {f"/tmp/{i}.wav": object() for i in range(1000)}

        visible = app._visible_file_iids(table, valid)

        self.assertTrue(visible)
        self.assertNotIn("folder", visible)
        self.assertLessEqual(table.calls, 15)

    def test_true_peak_refresh_ignores_hidden_workspaces(self):
        class Table:
            def winfo_exists(self):
                return True

            def winfo_viewable(self):
                return True

        app = object.__new__(am.AudioBalancerApp)
        first = types.SimpleNamespace(file_table=Table())
        second = types.SimpleNamespace(file_table=Table())
        app.workspaces = [first, second]
        app.active_ws_idx = 1
        app._refresh_true_peak_overlays_for_table = mock.Mock()

        app._do_refresh_true_peak_overlays()

        app._refresh_true_peak_overlays_for_table.assert_called_once_with(second.file_table, second)

    def test_ui_queue_is_drained_in_bounded_batches(self):
        app = object.__new__(am.AudioBalancerApp)
        app._closing = False
        app._ui_queue = am.queue.Queue()
        app.after = mock.Mock(return_value="next-job")
        seen = []
        for idx in range(40):
            app._ui_queue.put((seen.append, (idx,)))

        with mock.patch.object(am.time, "perf_counter", return_value=0.0):
            app._poll_ui_queue()

        self.assertEqual(len(seen), am._UI_QUEUE_MAX_CALLBACKS)
        self.assertEqual(app._ui_queue.qsize(), 40 - am._UI_QUEUE_MAX_CALLBACKS)
        self.assertEqual(app.after.call_args.args[0], 1)

    def test_duplicate_analysis_submission_is_coalesced(self):
        class Pool:
            def __init__(self):
                self.tasks = []

            def submit(self, fn):
                self.tasks.append(fn)
                return True

        app = object.__new__(am.AudioBalancerApp)
        app._analysis_pool = Pool()
        app.analyze_single_file = mock.Mock()
        entry = {"path": "/tmp/a.wav"}

        self.assertTrue(app._submit_analysis(entry))
        self.assertFalse(app._submit_analysis(entry))
        self.assertEqual(len(app._analysis_pool.tasks), 1)
        app._analysis_pool.tasks[0]()
        self.assertFalse(entry["_analysis_pending"])
        app.analyze_single_file.assert_called_once_with(entry, preserve_saved_lufs=False)

    def test_restored_analysis_prioritizes_active_workspace(self):
        app = object.__new__(am.AudioBalancerApp)
        app._submit_analysis = mock.Mock()
        workspaces = [am.Workspace(f"ws-{idx}") for idx in range(3)]
        entries = [{"path": f"/tmp/{idx}.wav"} for idx in range(3)]
        groups = [
            (workspaces[idx], [(entries[idx], idx == 2)])
            for idx in range(3)
        ]

        app._submit_restored_analysis_jobs(groups, priority_idx=2)

        submitted_paths = [call.args[0]["path"] for call in app._submit_analysis.call_args_list]
        self.assertEqual(submitted_paths, ["/tmp/2.wav", "/tmp/0.wav", "/tmp/1.wav"])
        self.assertIs(app._submit_analysis.call_args_list[0].kwargs["workspace"], workspaces[2])

    def test_cancelled_workspace_skips_queued_analysis(self):
        class Pool:
            def __init__(self):
                self.tasks = []

            def submit(self, fn):
                self.tasks.append(fn)
                return True

        app = object.__new__(am.AudioBalancerApp)
        app._analysis_pool = Pool()
        app.analyze_single_file = mock.Mock()
        workspace = am.Workspace("closed")
        entry = {"path": "/tmp/cancelled.wav"}

        self.assertTrue(app._submit_analysis(entry, workspace=workspace))
        workspace._analysis_cancelled = True
        app._analysis_pool.tasks[0]()

        app.analyze_single_file.assert_not_called()
        self.assertFalse(entry["_analysis_pending"])

    def test_removing_pending_file_keeps_entry_identity_for_undo(self):
        path = "/tmp/pending.wav"
        entry = {"path": path, "_analysis_pending": True}

        class Table:
            def selection(self):
                return (path,)

            def tag_has(self, tag, iid):
                return False

            def exists(self, iid):
                return True

            def delete(self, iid):
                pass

        table = Table()
        workspace = am.Workspace(
            "Test", audio_files=[entry], audio_by_path={path: entry}, file_table=table,
        )
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [workspace]
        app.active_ws_idx = 0
        app._undo_stack = []
        app._prune_empty_folder_nodes = mock.Mock()
        app._update_empty_hint = mock.Mock()
        app.check_export_ready = mock.Mock()
        app._schedule_autosave = mock.Mock()

        app.remove_selected_files()

        _, (_, removed) = app._undo_stack[-1]
        self.assertIs(removed["files"][0]["entry"], entry)

    def test_peak_decode_ready_refreshes_open_editor(self):
        app = object.__new__(am.AudioBalancerApp)
        app._schedule_wave_draw = mock.Mock()
        editor = types.SimpleNamespace(_closing=False, _schedule_redraw=mock.Mock())
        app._edit_window = editor
        audio = object()
        entry = {"audio": audio}
        pending_key = ("/tmp/a.wav", id(entry), id(audio))
        app._peak_decode_pending = {pending_key}

        app._finish_peak_decode(pending_key, entry, audio, True)

        self.assertNotIn(pending_key, app._peak_decode_pending)
        app._schedule_wave_draw.assert_called_once_with()
        editor._schedule_redraw.assert_called_once_with(50)

    def test_peak_decode_failure_does_not_start_a_redraw_retry_loop(self):
        app = object.__new__(am.AudioBalancerApp)
        path = "/tmp/broken.wav"
        audio = object()
        entry = {"path": path, "audio": audio}
        pending_key = (path, id(entry), id(audio))
        app._peak_decode_pending = {pending_key}
        app._schedule_wave_draw = mock.Mock()
        app._edit_window = types.SimpleNamespace(
            _closing=False, _schedule_redraw=mock.Mock(),
        )

        app._finish_peak_decode(pending_key, entry, audio, False)

        self.assertNotIn(pending_key, app._peak_decode_pending)
        self.assertIs(entry["_peak_decode_failed_audio"], audio)
        app._schedule_wave_draw.assert_not_called()
        app._edit_window._schedule_redraw.assert_not_called()

    def test_peak_decode_same_path_different_entries_do_not_block_each_other(self):
        class Pool:
            def __init__(self):
                self.tasks = []

            def submit(self, fn):
                self.tasks.append(fn)
                return True

        app = object.__new__(am.AudioBalancerApp)
        app._waveform_pool = Pool()
        app._peak_decode_pending = set()
        first = {"path": "/tmp/shared.wav", "audio": object()}
        second = {"path": "/tmp/shared.wav", "audio": object()}

        app._queue_peak_decode(first)
        app._queue_peak_decode(second)

        self.assertEqual(len(app._waveform_pool.tasks), 2)
        self.assertEqual(len(app._peak_decode_pending), 2)

    def test_meter_canvas_reuses_existing_items(self):
        class Canvas:
            def __init__(self):
                self.lines = 0
                self.rectangles = 0
                self.next_id = 1

            def _id(self):
                value = self.next_id
                self.next_id += 1
                return value

            def create_line(self, *args, **kwargs):
                self.lines += 1
                return self._id()

            def create_rectangle(self, *args, **kwargs):
                self.rectangles += 1
                return self._id()

            def coords(self, *args):
                pass

            def itemconfigure(self, *args, **kwargs):
                pass

        app = object.__new__(am.AudioBalancerApp)
        canvas = Canvas()

        app.draw_meter_canvas(canvas, 0.1)
        app.draw_meter_canvas(canvas, 0.2)

        self.assertEqual(canvas.lines, 6)
        self.assertEqual(canvas.rectangles, 3)

    def test_editor_redraw_throttle_coalesces_motion_burst(self):
        class Window:
            def __init__(self):
                self.calls = []

            def after(self, delay, fn):
                self.calls.append((delay, fn))
                return "redraw-job"

            def after_cancel(self, job):
                pass

        editor = object.__new__(am.EditWindow)
        editor.win = Window()
        editor._closing = False
        editor._redraw_job = None
        editor._trim_help = None
        editor.redraw = mock.Mock()

        for _ in range(100):
            editor._schedule_redraw()

        self.assertEqual(len(editor.win.calls), 1)
        editor.win.calls[0][1]()
        editor.redraw.assert_called_once_with()
        self.assertIsNone(editor._redraw_job)

    def test_editor_visible_track_range_is_viewport_bounded(self):
        editor = object.__new__(am.EditWindow)
        editor.tracks = [{} for _ in range(100)]
        editor.canvas = types.SimpleNamespace(
            winfo_height=lambda: 460,
            canvasy=lambda y: 920 + y,
        )

        visible = list(editor._visible_track_indices())

        self.assertLessEqual(len(visible), 8)
        self.assertIn(10, visible)
        self.assertNotIn(0, visible)

    def test_editor_touchpad_scroll_moves_both_axes_and_redraws(self):
        class Canvas:
            def __init__(self):
                self.x_target = None
                self.y_target = None

            def xview(self):
                return 0.2, 0.7

            def yview(self):
                return 0.3, 0.6

            def winfo_width(self):
                return 500

            def winfo_height(self):
                return 400

            def xview_moveto(self, value):
                self.x_target = value

            def yview_moveto(self, value):
                self.y_target = value

        editor = object.__new__(am.EditWindow)
        editor.canvas = Canvas()
        editor._schedule_redraw = mock.Mock()
        packed = (2 << 16) | ((-3) & 0xFFFF)

        result = editor._on_editor_touchpad(types.SimpleNamespace(delta=packed))

        self.assertEqual(result, "break")
        self.assertAlmostEqual(editor.canvas.x_target, 0.196)
        self.assertAlmostEqual(editor.canvas.y_target, 0.3075)
        editor._schedule_redraw.assert_called_once_with(16)

    def test_editor_yview_callback_redraws_only_when_fraction_changes(self):
        editor = object.__new__(am.EditWindow)
        editor._edit_vbar = types.SimpleNamespace(set=mock.Mock())
        editor.track_header_canvas = types.SimpleNamespace(yview_moveto=mock.Mock())
        editor._schedule_redraw = mock.Mock()

        editor._on_timeline_yscroll("0.2", "0.6")
        editor._on_timeline_yscroll("0.2", "0.6")

        editor.track_header_canvas.yview_moveto.assert_called_with(0.2)
        editor._schedule_redraw.assert_called_once_with(16)

    def test_long_region_waveform_uses_one_bounded_polygon(self):
        class Canvas:
            def __init__(self):
                self.polygon_sizes = []

            def create_rectangle(self, *args, **kwargs):
                return 1

            def create_line(self, *args, **kwargs):
                return 2

            def create_polygon(self, *args, **kwargs):
                self.polygon_sizes.append(len(args))
                return 3

            def create_text(self, *args, **kwargs):
                return 4

            def create_oval(self, *args, **kwargs):
                return 5

        editor = object.__new__(am.EditWindow)
        editor.canvas = Canvas()
        editor.app = types.SimpleNamespace(
            _peek_cached_peaks=lambda entry: np.ones(2000, dtype=np.float32),
            _queue_peak_decode=lambda entry: None,
            _wave_gain_factor=lambda entry: 1.0,
        )
        editor.px_per_sec = 10_000.0
        editor.wave_amp_zoom = 1.0
        editor.show_automation = False
        editor.active_region = None
        editor.selected_regions = []
        editor._fade_imgs = []
        path = "/tmp/long.wav"
        track = {
            "color": "#4A90E2",
            "entry": {
                "path": path,
                "audio": types.SimpleNamespace(duration_seconds=10.0),
            },
        }
        region = am.EditRegion(path, 0.0, 10.0, 0.0)

        editor._draw_region(track, 0, region, editor.RULER_H,
                            editor.RULER_H + editor.TRACK_H)

        waveform_sizes = [size for size in editor.canvas.polygon_sizes if size > 6]
        self.assertEqual(len(waveform_sizes), 1)
        self.assertLessEqual(waveform_sizes[0], editor.MAX_WAVEFORM_POINTS * 4)

    def test_wide_fade_uses_canvas_fallback_instead_of_disappearing(self):
        canvas = types.SimpleNamespace(
            create_image=mock.Mock(),
            create_polygon=mock.Mock(),
            create_line=mock.Mock(),
        )
        editor = object.__new__(am.EditWindow)
        editor.canvas = canvas
        editor._fade_imgs = []
        editor._make_fade_image = mock.Mock(return_value=None)

        editor._draw_fade_overlay(0.0, 3000.0, 0.0, 80.0, 0.4, True)

        canvas.create_image.assert_not_called()
        canvas.create_polygon.assert_called_once()
        canvas.create_line.assert_called_once()

    def test_direct_editor_destroy_stops_audio_and_invalidates_tick(self):
        editor = object.__new__(am.EditWindow)
        editor.win = object()
        editor.app = types.SimpleNamespace(_edit_window=editor)
        editor._closing = False
        editor._play_generation = 4
        editor._cancel_scheduled_redraw = mock.Mock()
        editor._unbind_global_shortcuts = mock.Mock()

        with mock.patch.object(am.sd, "stop") as stop:
            editor._on_window_destroy(types.SimpleNamespace(widget=editor.win))

        self.assertTrue(editor._closing)
        self.assertEqual(editor._play_generation, 5)
        stop.assert_called_once_with()
        self.assertIsNone(editor.app._edit_window)

    def test_two_hour_main_waveform_uses_adaptive_grid(self):
        class Canvas:
            def __init__(self):
                self.lines = 0

            def delete(self, *args):
                pass

            def winfo_width(self):
                return 400

            def winfo_height(self):
                return 120

            def configure(self, **kwargs):
                pass

            def yview_moveto(self, value):
                pass

            def create_line(self, *args, **kwargs):
                self.lines += 1

        app = object.__new__(am.AudioBalancerApp)
        app.waveform_canvas = Canvas()
        audio = types.SimpleNamespace(duration_seconds=2 * 60 * 60)

        app.draw_waveform(audio)

        self.assertLess(app.waveform_canvas.lines, 20)
        step = am._nice_time_grid_step(audio.duration_seconds, 80.0)
        self.assertLessEqual(
            math.floor(audio.duration_seconds / step) + 1,
            am._MAX_TIMELINE_GRID_LINES + 1,
        )


if __name__ == "__main__":
    unittest.main()
