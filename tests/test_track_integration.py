import types
import unittest
from unittest import mock

import numpy as np

import audio_master as am


def make_editor():
    editor = object.__new__(am.EditWindow)
    editor._session = am.EditSession()
    editor.redraw = lambda: None
    return editor


class TrackPersistenceTests(unittest.TestCase):
    def test_sync_metadata_writes_entry_and_serializes(self):
        editor = make_editor()
        entry = {
            "name": "Voice.wav", "path": "/tmp/voice.wav", "lufs": -16.0,
            "target_lufs": -16.0, "true_peak": -1.0, "duration": "00:01",
        }
        editor.tracks = [{
            "entry": entry, "regions": [], "name": "Lead VO", "color": "#2E6E4D",
            "gain_db": -3.5, "pan": 0.25,
        }]
        editor._write_track_metadata_to_entries()
        self.assertEqual(entry["edit_track"], {
            "name": "Lead VO", "color": "#2E6E4D", "gain_db": -3.5, "pan": 0.25, "order": 0,
        })

        app = object.__new__(am.AudioBalancerApp)
        app._serialize_dir_tree = lambda _ws: []
        ws = am.Workspace("Test", audio_files=[entry], project_file_path="/tmp/test.abproj")
        serialized = app._serialize_workspace(ws)
        self.assertEqual(serialized["audio_files"][0]["edit_track"], entry["edit_track"])

    def test_snapshot_restores_track_layout_and_regions(self):
        editor = make_editor()
        first = {"name": "First.wav", "path": "/tmp/first.wav"}
        second = {"name": "Second.wav", "path": "/tmp/second.wav"}
        region_a = am.EditRegion("/tmp/first.wav", 0.0, 1.0, 0.0)
        region_b = am.EditRegion("/tmp/second.wav", 0.0, 1.0, 0.0)
        editor.tracks = [
            {"entry": first, "regions": [region_a], "name": "A", "color": "#2A4D6E", "gain_db": 0, "pan": 0},
            {"entry": second, "regions": [region_b], "name": "B", "color": "#2E6E4D", "gain_db": -6, "pan": -0.5},
        ]
        snapshot = editor._snapshot()
        editor.tracks.reverse()
        editor.tracks[0]["name"] = "Changed"
        editor._restore(snapshot)
        self.assertEqual([track["name"] for track in editor.tracks], ["A", "B"])
        self.assertEqual(editor.tracks[1]["gain_db"], -6.0)
        self.assertEqual(editor.tracks[1]["pan"], -0.5)
        self.assertIsNot(editor.tracks[0]["regions"][0], region_a)

    def test_snapshot_restores_active_track_by_entry_after_reorder(self):
        editor = make_editor()
        first = {"name": "First.wav", "path": "/tmp/first.wav"}
        second = {"name": "Second.wav", "path": "/tmp/second.wav"}
        editor.tracks = [
            {"entry": first, "regions": [], "name": "First", "color": "#2A4D6E"},
            {"entry": second, "regions": [], "name": "Second", "color": "#2E6E4D"},
        ]
        editor.playhead_track = 0
        snapshot = editor._snapshot()
        editor.tracks.reverse()
        editor.playhead_track = 1
        editor._restore(snapshot)
        self.assertEqual(editor.playhead_track, 0)
        self.assertIs(editor.tracks[editor.playhead_track]["entry"], first)


class TrackMixIntegrationTests(unittest.TestCase):
    def test_editor_mix_applies_controls_before_the_final_sum(self):
        editor = make_editor()
        left_regions, right_regions = [object()], [object()]
        editor.tracks = [
            {
                "entry": {"name": "Left.wav", "path": "/tmp/left.wav"},
                "regions": left_regions, "name": "Left", "color": "#2A4D6E",
                "gain_db": 0.0, "pan": -1.0, "muted": False, "soloed": False,
            },
            {
                "entry": {"name": "Right.wav", "path": "/tmp/right.wav"},
                "regions": right_regions, "name": "Right", "color": "#2E6E4D",
                "gain_db": 0.0, "pan": 1.0, "muted": False, "soloed": False,
            },
        ]
        buffers = {
            id(left_regions): np.array([[0.75, 0.75]], dtype=np.float32),
            id(right_regions): np.array([[0.5, 0.5]], dtype=np.float32),
        }
        editor.app = types.SimpleNamespace(
            _render_region_list=lambda regions, *_args, **_kwargs: buffers[id(regions)].copy(),
            apply_soft_clipper=np.tanh,
        )
        mixed = editor._render_audible_track_mix(48_000, 2, 1)
        np.testing.assert_allclose(mixed, [[0.75, 0.5]], atol=1e-6)

    def test_editor_target_preview_uses_adjusted_entry_gain_when_ab_is_target(self):
        """The embedded pane and standalone window share this renderer.

        Target/Gain in Edit writes ``target_lufs`` on the entry.  Its next
        playback must apply the same target-minus-measured gain as the main
        A/B target preview, before any Track Gain/Pan mixer control.
        """
        editor = make_editor()
        regions = [object()]
        editor.tracks = [{
            "entry": {
                "name": "Voice.wav", "path": "/tmp/voice.wav",
                "lufs": -20.0, "target_lufs": -14.0,
            },
            "regions": regions, "name": "Voice", "color": "#2A4D6E",
            "gain_db": 0.0, "pan": 0.0, "muted": False, "soloed": False,
        }]
        editor.app = types.SimpleNamespace(
            _render_region_list=lambda *_args, **_kwargs: np.array([0.25], dtype=np.float32),
            apply_soft_clipper=np.tanh,
            ab_listen_var=types.SimpleNamespace(get=lambda: True),
        )

        mixed = editor._render_audible_track_mix(48_000, 1, 1)

        np.testing.assert_allclose(
            mixed,
            np.array([0.25 * (10 ** (6.0 / 20.0))], dtype=np.float32),
            atol=1e-6,
        )

    def test_editor_target_adjustment_switches_main_ab_to_target(self):
        """Explicit Edit Target/Gain adjustments retain the app's A/B model."""
        editor = make_editor()
        entry = {
            "name": "Voice.wav", "path": "/tmp/voice.wav",
            "lufs": -20.0, "target_lufs": -20.0,
        }
        editor.tracks = [{
            "entry": entry, "regions": [], "name": "Voice", "color": "#2A4D6E",
            "gain_db": 0.0, "pan": 0.0,
        }]
        editor.playhead_track = 0
        editor.selected_regions = []
        editor._sync_ew_entry_change = mock.Mock()
        editor._refresh_gain_target_display = mock.Mock()
        editor.redraw = mock.Mock()
        app = types.SimpleNamespace(
            _undo_stack=[], _ensure_ab_target=mock.Mock(), _schedule_autosave=mock.Mock(),
            cached_audio_path="old",
        )
        editor.app = app

        editor._apply_target_absolute_to_selection(-14.0)
        editor._apply_gain_delta_to_selection(-2.0)

        self.assertEqual(entry["target_lufs"], -16.0)
        self.assertEqual(app._ensure_ab_target.call_count, 2)
        app._schedule_autosave.assert_called()

    def test_active_workspace_controls_are_keyed_by_entry_identity(self):
        app = object.__new__(am.AudioBalancerApp)
        first_ws, second_ws = am.Workspace("A"), am.Workspace("B")
        first_entry = {"name": "same.wav", "path": "/tmp/same.wav"}
        second_entry = {"name": "same.wav", "path": "/tmp/same.wav"}
        first_view = types.SimpleNamespace(
            _session=am.EditSession(workspace=first_ws),
            tracks=[{"entry": first_entry, "gain_db": -4.0, "pan": -0.25}],
        )
        second_view = types.SimpleNamespace(
            _session=am.EditSession(workspace=second_ws),
            tracks=[{"entry": second_entry, "gain_db": 6.0, "pan": 0.5}],
        )
        app.workspaces = [first_ws, second_ws]
        app.active_ws_idx = 0
        app._unique_session_views = lambda: [first_view, second_view]
        controls = app._editor_track_mix_controls()
        self.assertEqual(controls, {id(first_entry): (-4.0, -0.25)})

    def test_saved_track_controls_continue_in_main_preview_after_editor_closes(self):
        """Track Inspector writes entry metadata, so closing Edit must not reset it."""
        app = object.__new__(am.AudioBalancerApp)
        entry = {
            "name": "Music.wav", "path": "/tmp/music.wav",
            "edit_track": {
                "name": "Music", "color": "#2A4D6E",
                "gain_db": -7.5, "pan": 0.4, "order": 0,
            },
        }
        ws = am.Workspace("A", audio_files=[entry])
        app.workspaces = [ws]
        app.active_ws_idx = 0
        # No live view represents the normal post-close state.
        app._unique_session_views = lambda: []

        self.assertEqual(app._editor_track_mix_controls(), {id(entry): (-7.5, 0.4)})
        self.assertEqual(
            app._monitor_signature(),
            ((id(entry), "/tmp/music.wav", False, False, -7.5, 0.4),),
        )


class TrackMutationTests(unittest.TestCase):
    def test_playing_reverse_track_change_restarts_in_reverse_and_persists(self):
        editor = make_editor()
        entry = {"name": "FX.wav", "path": "/tmp/fx.wav"}
        editor.tracks = [{
            "entry": entry, "regions": [], "name": "FX", "color": "#2A4D6E",
            "gain_db": 0.0, "pan": 0.0, "muted": False, "soloed": False,
        }]
        editor.transport_state = am.EditWindow.TRANSPORT_PLAYING
        editor._session.play_owner = types.SimpleNamespace(_play_direction=-1)
        editor._capture_playhead_now = mock.Mock()
        editor._push_undo = mock.Mock()
        editor.play = mock.Mock()
        editor.app = types.SimpleNamespace(
            cached_audio_path="old", _rebuild_main_playback_for_monitor_change=mock.Mock(),
            _schedule_autosave=mock.Mock(),
        )
        with mock.patch.object(am.sd, "stop"):
            self.assertTrue(editor._apply_track_settings(0, gain_db=-4.0, pan=0.5))
        self.assertEqual(entry["edit_track"]["gain_db"], -4.0)
        self.assertEqual(entry["edit_track"]["pan"], 0.5)
        editor.play.assert_called_once_with(direction=-1)

    def test_move_track_remaps_range_selection_and_persists_contiguous_order(self):
        editor = make_editor()
        entries = [
            {"name": f"T{index}.wav", "path": f"/tmp/t{index}.wav"}
            for index in range(3)
        ]
        editor.tracks = [
            {"entry": entry, "regions": [], "name": f"T{index}", "color": "#2A4D6E", "gain_db": 0, "pan": 0}
            for index, entry in enumerate(entries)
        ]
        editor.selection = (2, 0.0, 1.0)
        editor.playhead_track = 0
        editor.transport_state = am.EditWindow.TRANSPORT_READY
        editor._push_undo = mock.Mock()
        editor.app = types.SimpleNamespace(
            cached_audio_path=None, _rebuild_main_playback_for_monitor_change=mock.Mock(),
            _schedule_autosave=mock.Mock(),
        )
        editor._move_track(0, 2)
        self.assertEqual([track["name"] for track in editor.tracks], ["T1", "T2", "T0"])
        self.assertEqual(editor.playhead_track, 2)
        self.assertEqual(editor.selection, (1, 0.0, 1.0))
        self.assertEqual([entry["edit_track"]["order"] for entry in entries], [2, 0, 1])


if __name__ == "__main__":
    unittest.main()
