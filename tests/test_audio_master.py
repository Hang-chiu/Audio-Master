import math
import os
import tempfile
import types
import unittest
from unittest import mock

import numpy as np
from pydub import AudioSegment
from scipy.signal import resample_poly

import audio_master as am


def make_editor_stub():
    """Create a non-Tk EditWindow test double backed by its real session model.

    EditWindow now forwards state such as ``tracks`` and ``playhead`` to an
    EditSession.  Tests that bypass ``__init__`` must still provide that data
    layer; otherwise they only test an impossible half-initialized editor.
    """
    editor = object.__new__(am.EditWindow)
    editor._session = am.EditSession()
    return editor


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


class WhatsNewNotesTests(unittest.TestCase):
    def test_current_release_summarizes_retained_v126_to_v130_features(self):
        """The current dialog is an aggregate, not only the last commit's notes."""
        notes = am.WHATS_NEW_NOTES[am.APP_VERSION]
        joined = "\n".join(notes)

        self.assertEqual(am.APP_VERSION, "1.3.0")
        self.assertEqual(am.WHATS_NEW_REVISION, 2)
        self.assertIn("v1.2.6 → v1.3.0", notes[0])
        for retained_feature in (
            "遺失素材管理",
            "Marker",
            "Track Inspector",
            "L／R Peak",
            "專屬的折角文件 Logo 打包",
        ):
            self.assertIn(retained_feature, joined)
        self.assertNotIn("交付 QC", joined)


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
        editor = make_editor_stub()
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

        # Flex Time 後，中央表格應顯示 Region 在時間軸上的播放長度，不能沿用來源長度。
        entry["edit_regions"] = [
            am.EditRegion(self.path, 0.0, 1.0, 0.0, time_stretch_ratio=2.0).to_dict(),
        ]
        self.assertEqual(self.app._entry_duration_label(entry), "00:02")

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


class MediaAvailabilityTests(unittest.TestCase):
    """Missing files must fail visibly instead of becoming zero-sample audio."""

    def setUp(self):
        self.app = object.__new__(am.AudioBalancerApp)
        self.app.workspaces = [types.SimpleNamespace(audio_files=[])]
        self.app.active_ws_idx = 0
        self.missing_original_path = "/tmp/audio-master-missing-original.wav"
        self.loaded_entry_path = os.path.abspath(__file__)
        self.join_path = "/tmp/audio-master-missing-join.wav"
        self.assertFalse(os.path.exists(self.missing_original_path))
        self.assertTrue(os.path.isfile(self.loaded_entry_path))
        self.assertFalse(os.path.exists(self.join_path))

    def _entry_with_missing_join_region(self):
        return {
            "name": "edited.wav",
            # The entry's original audio is still loaded, as it would be after
            # import.  The Region instead points to a removed Join/external file.
            "path": self.loaded_entry_path,
            "audio": AudioSegment.silent(duration=1000, frame_rate=48_000),
            "edit_regions": [
                am.EditRegion(self.join_path, 0.0, 1.0, 0.0).to_dict(),
            ],
            "lufs": -16.0,
            "target_lufs": -16.0,
        }

    def test_playback_preflight_checks_external_region_not_only_loaded_entry(self):
        entry = self._entry_with_missing_join_region()

        with self.assertRaises(am.MediaUnavailableError) as caught:
            self.app._build_playback_mix([entry], ab_on=False)

        self.assertEqual(caught.exception.paths, (self.join_path,))

    def test_decoder_refuses_missing_source_even_when_a_zero_sample_cache_exists(self):
        stale_cache = {self.join_path: (np.zeros(0, dtype=np.float32), 44_100, 1)}

        with self.assertRaises(am.MediaUnavailableError) as caught:
            self.app._decode_source_samples(self.join_path, stale_cache)

        self.assertEqual(caught.exception.paths, (self.join_path,))

    def test_unedited_missing_original_source_is_rejected(self):
        with self.assertRaises(am.MediaUnavailableError) as caught:
            self.app._require_entry_media_available({
                "path": self.missing_original_path,
                "edit_regions": None,
            })

        self.assertEqual(caught.exception.paths, (self.missing_original_path,))

    def test_region_renderer_cannot_turn_missing_join_into_silence(self):
        region = am.EditRegion(self.join_path, 0.0, 1.0, 0.0)

        with self.assertRaises(am.MediaUnavailableError):
            self.app._render_region_list([region], 48_000, 1)

    def test_main_display_keeps_original_waveform_and_notifies_once(self):
        entry = self._entry_with_missing_join_region()
        notices = []
        self.app._show_media_unavailable_error = (
            lambda error, action: notices.append((error.paths, action))
        )

        displayed = self.app._render_entry_for_main_display(entry, notify=True)
        displayed_again = self.app._render_entry_for_main_display(entry, notify=True)

        self.assertIs(displayed, entry["audio"])
        self.assertIs(displayed_again, entry["audio"])
        self.assertTrue(entry["_display_original_due_to_missing_media"])
        self.assertEqual(notices, [((self.join_path,), "顯示剪輯波形")])

    def test_explicitly_empty_edit_remains_a_valid_intentional_silence(self):
        # [] has always meant the user deliberately removed every Region.  It
        # must remain exportable even if the original imported file is gone.
        self.app._require_entry_media_available({
            "path": self.missing_original_path,
            "edit_regions": [],
        })

    def test_export_reports_missing_join_as_a_failure_without_writing_audio(self):
        entry = self._entry_with_missing_join_region()
        callbacks = []
        self.app._export_cancel = False
        self.app._update_export_progress = lambda *_args: None
        self.app._finish_export = lambda *_args: None
        self.app._enqueue_ui = lambda callback, *args: callbacks.append((callback, args))

        with tempfile.TemporaryDirectory() as export_folder:
            self.app.export_process(
                "WAV",
                [{"folder_base": "Workspace", "entries": [entry]}],
                export_folder,
            )

            finish_calls = [
                args for callback, args in callbacks
                if callback is self.app._finish_export
            ]
            self.assertEqual(len(finish_calls), 1)
            successes, failures, cancelled = finish_calls[0]
            self.assertEqual(successes, 0)
            self.assertFalse(cancelled)
            self.assertEqual(failures[0][0], "edited.wav")
            self.assertIn(self.join_path, failures[0][1])
            self.assertFalse(os.path.exists(os.path.join(
                export_folder, "Workspace", "edited.wav",
            )))


class PlaybackTargetFreshnessTests(unittest.TestCase):
    """Target/Gain changes must never reuse an older preview mix."""

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def _make_app(self):
        app = object.__new__(am.AudioBalancerApp)
        first = {
            "name": "Lead.wav", "path": "/tmp/lead.wav",
            "lufs": -20.0, "target_lufs": -20.0,
        }
        second = {
            "name": "Music.wav", "path": "/tmp/music.wav",
            "lufs": -20.0, "target_lufs": -20.0,
        }
        app.workspaces = [am.Workspace(
            "Playback", audio_files=[first, second], current_file_path=first["path"],
        )]
        app.active_ws_idx = 0
        app.target_lufs_var = self.Var(-20.0)
        app.ab_listen_var = self.Var(True)
        app.loop_var = self.Var(False)
        app.pause_position = 0.0
        app.is_playing = False
        app._playback_entries = mock.Mock(return_value=[first, second])
        app._require_entries_media_available = mock.Mock()
        app._end_main_meter = mock.Mock()
        app._pause_playing_edit_views = mock.Mock()
        app.reset_peaks = mock.Mock()
        app._monitor_signature = mock.Mock(return_value=())
        app._build_playback_mix = mock.Mock(
            return_value=(np.array([0.25], dtype=np.float32), 1),
        )
        app.scrub_slider = mock.Mock()
        app.scrub_var = self.Var(0.0)
        app.get_selected_device = mock.Mock(return_value=None)
        app._begin_main_meter = mock.Mock()
        app.play_btn = mock.Mock()
        app.update_meters = mock.Mock()
        app.playback_data = np.array([0.1], dtype=np.float32)
        app.playback_sr = 1
        app._lufs_apply_job = None
        app._gain_apply_job = None
        app._update_meter_id = 0
        return app, first, second

    def test_next_main_play_rebuilds_when_noncurrent_selected_target_changes(self):
        """Edit can change any selected track, not only main current_file_path."""
        app, first, second = self._make_app()
        old_key = (
            tuple((id(entry), entry["path"]) for entry in (first, second)),
            True,
            app._playback_target_gain_signature([first, second]),
            (),
        )
        app.cached_audio_path = old_key

        # This matches a Target/Gain adjustment on the second Edit track.  The
        # main Target widget still represents the first track, so the old cache
        # key design would have incorrectly reused its old PCM here.
        second["target_lufs"] = -8.0

        with mock.patch.object(am.sd, "stop"), mock.patch.object(am.sd, "play"):
            app.play_original()

        app._build_playback_mix.assert_called_once_with([first, second], True)
        self.assertNotEqual(app.cached_audio_path, old_key)
        self.assertEqual(
            app.cached_audio_path[2],
            app._playback_target_gain_signature([first, second]),
        )

    def test_pending_target_and_gain_writes_flush_before_playback(self):
        app, _first, _second = self._make_app()
        app._lufs_apply_job = "pending-target"
        app._gain_apply_job = "pending-gain"
        app.after_cancel = mock.Mock()
        app._flush_lufs_apply = mock.Mock()
        app._flush_gain_apply = mock.Mock()

        app._flush_pending_volume_changes_for_playback()

        app.after_cancel.assert_has_calls([
            mock.call("pending-target"), mock.call("pending-gain"),
        ])
        app._flush_lufs_apply.assert_called_once_with()
        app._flush_gain_apply.assert_called_once_with()
        self.assertIsNone(app._lufs_apply_job)
        self.assertIsNone(app._gain_apply_job)


class RightPlayerEmbeddedEditTransportTests(unittest.TestCase):
    """The main right-player seek surface must follow an inline Edit owner."""

    class Var:
        def __init__(self, value=0.0):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    def make_app(self, *, inline_view=None):
        app = object.__new__(am.AudioBalancerApp)
        workspace = am.Workspace("Inline", edit_pane_view=inline_view)
        app.workspaces = [workspace]
        app.active_ws_idx = 0
        app.current_audio = object()
        app.playback_duration = 10.0
        app.pause_position = 0.0
        app._just_paused = True
        app.is_playing = False
        app.scrub_var = self.Var()
        app.lbl_time = mock.Mock()
        app.format_time = lambda value: f"{float(value):.1f}"
        app._draw_main_playhead = mock.Mock()
        app.update_playhead_idle = mock.Mock()
        app.jump_to = mock.Mock()
        app._right_player_edit_scrub = None
        app._active_track_width = 100
        return app, workspace

    @staticmethod
    def make_inline_owner(workspace, *, playing=True):
        session = am.EditSession(workspace=workspace)
        owner = types.SimpleNamespace(
            _is_embedded=True,
            _session=session,
            playhead=1.0,
            _play_direction=1,
            total_duration=lambda: 8.0,
            _draw_playhead_only=mock.Mock(),
            play=mock.Mock(),
        )

        def pause(*, by_space):
            session.is_playing = False
            session.play_owner = None

        owner.pause = mock.Mock(side_effect=pause)
        owner._move_playhead_to = mock.Mock(return_value=True)
        session.is_playing = playing
        session.play_owner = owner if playing else None
        workspace.edit_pane_view = owner
        return owner, session

    def test_waveform_drag_pauses_inline_owner_once_and_resumes_once(self):
        app, workspace = self.make_app()
        owner, session = self.make_inline_owner(workspace)

        self.assertEqual(
            app.on_waveform_click(types.SimpleNamespace(x=50)),
            "break",
        )
        self.assertAlmostEqual(owner.playhead, 5.0)
        owner.pause.assert_called_once_with(by_space=False)
        owner.play.assert_not_called()

        # Moving across the waveform only updates the cue; it must not rebuild
        # the multitrack mix once per pointer pixel.
        self.assertEqual(
            app.on_waveform_drag(types.SimpleNamespace(x=70)),
            "break",
        )
        self.assertAlmostEqual(owner.playhead, 7.0)
        owner.pause.assert_called_once_with(by_space=False)
        owner.play.assert_not_called()
        self.assertFalse(session.is_playing)

        self.assertEqual(
            app.on_waveform_release(types.SimpleNamespace()),
            "break",
        )
        owner.play.assert_called_once_with(direction=1)
        self.assertAlmostEqual(owner._cycle_seek_origin, 7.0)
        self.assertAlmostEqual(app.pause_position, 7.0)
        self.assertFalse(app._just_paused)

    def test_slider_drag_uses_the_same_deferred_inline_transport_path(self):
        app, workspace = self.make_app()
        owner, _session = self.make_inline_owner(workspace)

        # This is the pre-CTkSlider ButtonPress bindtag.  Its following
        # on_scrub callbacks correspond to CTkSlider click + B1-Motion.
        app._on_right_player_scrub_press()
        app.on_scrub(3.0)
        app.on_scrub(6.0)
        app._end_right_player_edit_scrub()

        owner.pause.assert_called_once_with(by_space=False)
        owner.play.assert_called_once_with(direction=1)
        self.assertAlmostEqual(owner.playhead, 6.0)
        self.assertAlmostEqual(owner._cycle_seek_origin, 6.0)
        app.jump_to.assert_not_called()

    def test_idle_inline_edit_is_cued_without_starting_playback(self):
        app, workspace = self.make_app()
        view, session = self.make_inline_owner(workspace, playing=False)

        app.on_scrub(7.0)

        self.assertFalse(session.is_playing)
        view.play.assert_not_called()
        view._move_playhead_to.assert_called_once_with(7.0, resume_if_playing=False)
        self.assertAlmostEqual(app.pause_position, 7.0)
        app.update_playhead_idle.assert_called_once_with()

    def test_standalone_edit_never_hijacks_the_main_right_player(self):
        app, workspace = self.make_app()
        session = am.EditSession(workspace=workspace)
        standalone = types.SimpleNamespace(
            _is_embedded=False,
            _session=session,
            _move_playhead_to=mock.Mock(return_value=True),
        )
        session.is_playing = True
        session.play_owner = standalone
        app._edit_window = standalone

        app.on_scrub(4.0)

        standalone._move_playhead_to.assert_not_called()
        self.assertAlmostEqual(app.pause_position, 4.0)
        app.update_playhead_idle.assert_called_once_with()

    def test_hidden_workspace_inline_owner_never_hijacks_active_right_player(self):
        app, active_workspace = self.make_app()
        hidden_workspace = am.Workspace("Hidden")
        hidden_owner, _session = self.make_inline_owner(hidden_workspace)
        app.workspaces.append(hidden_workspace)

        app.on_scrub(4.0)

        # Right-player controls are scoped to the active workspace.  A hidden
        # inline pane may still exist (or be shutting down), but cannot steal
        # this workspace's main transport.
        self.assertIsNone(active_workspace.edit_pane_view)
        hidden_owner._move_playhead_to.assert_not_called()
        self.assertAlmostEqual(app.pause_position, 4.0)
        app.update_playhead_idle.assert_called_once_with()

    def test_cycle_play_resumes_from_external_seek_instead_of_cycle_start(self):
        editor = make_editor_stub()
        region = am.EditRegion("/tmp/cycle.wav", 0.0, 10.0, 0.0)
        editor.tracks = [{
            "entry": {"audio": types.SimpleNamespace(frame_rate=10, channels=1)},
            "regions": [region], "muted": False, "soloed": False,
        }]
        editor.cycle_enabled = True
        editor.cycle_range = (2.0, 6.0)
        editor.playhead = 4.5
        editor._cycle_seek_origin = 4.5
        editor._refresh_all_crossfades = mock.Mock()
        editor._render_audible_track_mix = mock.Mock(
            return_value=np.arange(100, dtype=np.float32),
        )
        editor._tick = mock.Mock()
        editor.app = types.SimpleNamespace(
            _stop_main_playback_for_editor=mock.Mock(),
            _require_regions_media_available=mock.Mock(),
            get_selected_device=mock.Mock(return_value=None),
        )

        with mock.patch.object(am.sd, "stop"), mock.patch.object(am.sd, "play") as play:
            editor.play(direction=1)

        # Cycle spans source samples [20, 60).  A seek to 4.5s starts at 45,
        # not at the historical fixed cycle start sample 20.
        self.assertEqual(play.call_args.args[0][0], 45.0)
        self.assertAlmostEqual(editor.playhead, 4.5)
        self.assertAlmostEqual(editor._playhead_after_elapsed(0.2), 4.7)
        # 4.5 + 1.6 wraps through the end of [2.0, 6.0) to 2.1.  This
        # proves the elapsed phase is anchored to the external seek, not ct0.
        self.assertAlmostEqual(editor._playhead_after_elapsed(1.6), 2.1)

    def test_reverse_cycle_play_resumes_from_external_seek(self):
        editor = make_editor_stub()
        region = am.EditRegion("/tmp/cycle-reverse.wav", 0.0, 10.0, 0.0)
        editor.tracks = [{
            "entry": {"audio": types.SimpleNamespace(frame_rate=10, channels=1)},
            "regions": [region], "muted": False, "soloed": False,
        }]
        editor.cycle_enabled = True
        editor.cycle_range = (2.0, 6.0)
        editor.playhead = 4.5
        editor._cycle_seek_origin = 4.5
        editor._refresh_all_crossfades = mock.Mock()
        editor._render_audible_track_mix = mock.Mock(
            return_value=np.arange(100, dtype=np.float32),
        )
        editor._tick = mock.Mock()
        editor.app = types.SimpleNamespace(
            _stop_main_playback_for_editor=mock.Mock(),
            _require_regions_media_available=mock.Mock(),
            get_selected_device=mock.Mock(return_value=None),
        )

        with mock.patch.object(am.sd, "stop"), mock.patch.object(am.sd, "play") as play:
            editor.play(direction=-1)

        # Reverse playback must begin at the selected source sample as well,
        # then move down the timeline instead of silently resetting to ct1.
        self.assertEqual(play.call_args.args[0][0], 45.0)
        self.assertAlmostEqual(editor.playhead, 4.5)
        self.assertAlmostEqual(editor._playhead_after_elapsed(0.2), 4.3)
        # 4.5 - 2.7 wraps through the lower edge to 5.8, still relative to
        # the external seek rather than the historical ct1 origin.
        self.assertAlmostEqual(editor._playhead_after_elapsed(2.7), 5.8)


class EditWorkspaceIsolationTests(unittest.TestCase):
    """同一路徑可出現在不同 Workspace；EditSession 必須以 workspace 身分隔離。"""

    class LiveWindow:
        def winfo_exists(self):
            return True

    class Table:
        def __init__(self, *paths):
            self.paths = set(paths)
            self.set_calls = []

        def exists(self, path):
            return path in self.paths

        def set(self, *args):
            self.set_calls.append(args)

    @staticmethod
    def _entry(path, table, target=-16.0):
        return {
            "path": path,
            "audio": object(),
            "target_lufs": target,
            "_table": table,
        }

    def make_app(self):
        path = "/tmp/shared-between-workspaces.wav"
        first_table = self.Table(path)
        second_table = self.Table(path)
        first_entry = self._entry(path, first_table)
        second_entry = self._entry(path, second_table)
        first = am.Workspace(
            "First", audio_files=[first_entry], audio_by_path={path: first_entry},
            file_table=first_table, current_file_path=path,
        )
        second = am.Workspace(
            "Second", audio_files=[second_entry], audio_by_path={path: second_entry},
            file_table=second_table, current_file_path=path,
        )
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [first, second]
        app.active_ws_idx = 1
        app._edit_window = None
        return app, first, second, first_entry, second_entry

    def make_view(self, workspace, entry, **track_state):
        track = {"entry": entry, "soloed": False, "muted": False}
        track.update(track_state)
        return types.SimpleNamespace(
            win=self.LiveWindow(),
            _session=am.EditSession(workspace=workspace),
            tracks=[track],
            sync_entries=mock.Mock(),
            load_entries=mock.Mock(),
            _track_is_audible=mock.Mock(return_value=not track["muted"]),
        )

    def test_matching_session_requires_same_workspace_for_identical_path(self):
        app, first, second, first_entry, second_entry = self.make_app()
        app._edit_window = self.make_view(first, first_entry)

        self.assertIsNone(app._matching_edit_session([second_entry]))

        second_view = self.make_view(second, second_entry)
        second.edit_pane_view = second_view
        self.assertIs(app._matching_edit_session([second_entry]), second_view._session)

    def test_selection_follow_does_not_reload_other_workspace_window(self):
        app, first, _second, first_entry, second_entry = self.make_app()
        editor = self.make_view(first, first_entry)
        app._edit_window = editor
        app._ensure_entry_audio_decoded = mock.Mock(return_value=True)

        app._sync_edit_window_selection([second_entry["path"]])

        editor.sync_entries.assert_not_called()
        editor.load_entries.assert_not_called()

    def test_opening_window_after_workspace_switch_recreates_it_for_active_workspace(self):
        app, first, second, first_entry, second_entry = self.make_app()
        old_editor = self.make_view(first, first_entry)
        old_editor.on_close = mock.Mock(side_effect=lambda: setattr(app, "_edit_window", None))
        app._edit_window = old_editor
        app._resolve_edit_entries = mock.Mock(return_value=[second_entry])
        replacement = types.SimpleNamespace(load_entries=mock.Mock())

        with mock.patch.object(am, "EditWindow", return_value=replacement) as edit_window:
            app._open_edit_window()

        old_editor.sync_entries.assert_called_once_with()
        old_editor.on_close.assert_called_once_with()
        edit_window.assert_called_once_with(app, session=None, workspace=second)
        replacement.load_entries.assert_called_once_with([second_entry])

    def test_target_sync_routes_to_entry_table_not_active_workspace_table(self):
        app, first, _second, first_entry, _second_entry = self.make_app()
        first_entry["target_lufs"] = -10.0
        app._sync_true_peak_cells = mock.Mock()
        app.update_target_lufs = mock.Mock()
        app._refresh_gain_display = mock.Mock()
        app._schedule_wave_draw = mock.Mock()
        editor = make_editor_stub()
        editor.app = app
        editor._session.workspace = first

        editor._sync_ew_entry_change(first_entry, first_entry["path"])

        self.assertEqual(
            first.file_table.set_calls,
            [(first_entry["path"], "目標 LUFS", "-10.0 LUFS")],
        )
        self.assertEqual(app.workspaces[1].file_table.set_calls, [])
        app._sync_true_peak_cells.assert_called_once_with(
            first.file_table, first_entry["path"], first_entry,
        )
        app.update_target_lufs.assert_not_called()

    def test_monitor_state_from_other_workspace_cannot_filter_or_key_active_playback(self):
        app, first, _second, first_entry, second_entry = self.make_app()
        # 另一頁的同路徑軌被 MUTE；若錯誤拿來套用，作用中 workspace 的唯一檔案會變成無聲。
        app._edit_window = self.make_view(first, first_entry, muted=True)

        self.assertEqual(app._filter_by_editor_monitor([second_entry]), [second_entry])
        self.assertEqual(app._monitor_signature(), ())


class EditWorkspaceSyncTests(unittest.TestCase):
    """Edit 資料在切換 workspace 時必須先落到正確 entry，而非等關窗。"""

    class Panel:
        def __init__(self):
            self.grid = mock.Mock()
            self.grid_remove = mock.Mock()
            self.destroy = mock.Mock()

    class Table:
        def __init__(self, selected=()):
            self.selected = tuple(selected)

        def selection(self):
            return self.selected

        def tag_has(self, _tag, _iid):
            return False

    def test_switch_flushes_only_departing_workspace_and_restores_new_selection(self):
        path = "/tmp/shared.wav"
        first = am.Workspace(
            "First", file_table=self.Table((path,)),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        second = am.Workspace(
            "Second", file_table=self.Table((path,)),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        first_view = types.SimpleNamespace(
            _session=am.EditSession(workspace=first), sync_entries=mock.Mock(),
        )
        second_view = types.SimpleNamespace(
            _session=am.EditSession(workspace=second), sync_entries=mock.Mock(),
        )
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [first, second]
        app.active_ws_idx = 0
        app.stop_playback = mock.Mock()
        app._unique_session_views = lambda all_workspaces=False: [first_view, second_view]
        app._schedule_autosave = mock.Mock()
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        app.lbl_active_file = mock.Mock()
        app.lbl_info_current = mock.Mock()
        app.lbl_info_target = mock.Mock()
        app.lbl_info_gain = mock.Mock()
        app.waveform_canvas = mock.Mock()
        app._apply_right_layout = mock.Mock()
        app.check_export_ready = mock.Mock()
        app.on_table_select = mock.Mock()

        app._switch_workspace(1)

        first_view.sync_entries.assert_called_once_with()
        second_view.sync_entries.assert_not_called()
        app._schedule_autosave.assert_called_once_with()
        self.assertEqual(app.active_ws_idx, 1)
        first.left_panel_inner.grid_remove.assert_called_once_with()
        second.center_panel_inner.grid.assert_called_once_with()
        app.on_table_select.assert_called_once_with(None)

    def test_switch_stops_departing_workspace_edit_owner(self):
        class Owner:
            TRANSPORT_READY = "ready"

            def __init__(self, session):
                self.session = session
                self.pause_calls = []

            def pause(self, *, by_space):
                self.pause_calls.append(by_space)
                self.session.play_owner = None
                self.session.is_playing = False

        first = am.Workspace(
            "First", file_table=self.Table(),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        second = am.Workspace(
            "Second", file_table=self.Table(),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        session = am.EditSession(workspace=first)
        owner = Owner(session)
        session.play_owner = owner
        session.is_playing = True
        first_view = types.SimpleNamespace(_session=session, sync_entries=mock.Mock())

        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [first, second]
        app.active_ws_idx = 0
        app._edit_window = None
        app.stop_playback = mock.Mock()
        app._unique_session_views = lambda all_workspaces=False: [first_view]
        app._schedule_autosave = mock.Mock()
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        app.lbl_active_file = mock.Mock()
        app.lbl_info_current = mock.Mock()
        app.lbl_info_target = mock.Mock()
        app.lbl_info_gain = mock.Mock()
        app.waveform_canvas = mock.Mock()
        app._apply_right_layout = mock.Mock()
        app.check_export_ready = mock.Mock()

        app._switch_workspace(1)

        self.assertEqual(owner.pause_calls, [False])
        self.assertIsNone(session.play_owner)
        self.assertFalse(session.is_playing)

    def test_close_inactive_workspace_preserves_active_page_and_closes_its_views(self):
        first = am.Workspace(
            "First", file_table=self.Table(),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        closing = am.Workspace(
            "Closing", file_table=self.Table(),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        third = am.Workspace(
            "Third", file_table=self.Table(),
            left_panel_inner=self.Panel(), center_panel_inner=self.Panel(),
        )
        closing_view = types.SimpleNamespace(
            _session=am.EditSession(workspace=closing),
            sync_entries=mock.Mock(), on_close=mock.Mock(),
        )
        closing.edit_pane_view = closing_view

        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = [first, closing, third]
        app.active_ws_idx = 0
        app._edit_window = None
        app._refresh_tab_buttons = mock.Mock()
        app.check_export_ready = mock.Mock()
        app._schedule_autosave = mock.Mock()
        app._switch_workspace = mock.Mock()

        app._close_workspace(1)

        self.assertEqual(app.workspaces, [first, third])
        self.assertEqual(app.active_ws_idx, 0)
        first.left_panel_inner.destroy.assert_not_called()
        closing.left_panel_inner.destroy.assert_called_once_with()
        closing_view.sync_entries.assert_called_once_with()
        closing_view.on_close.assert_called_once_with()
        app._switch_workspace.assert_not_called()

    def test_debounced_edit_sync_flushes_without_waiting_for_close(self):
        class Window:
            def __init__(self):
                self.calls = []

            def after(self, delay, callback):
                self.calls.append((delay, callback))
                return f"job-{len(self.calls)}"

            def after_cancel(self, _job):
                pass

        editor = make_editor_stub()
        editor.win = Window()
        editor.app = types.SimpleNamespace(_schedule_autosave=mock.Mock())
        editor._closing = False
        editor.sync_entries = mock.Mock()

        editor._schedule_entry_sync()
        delay, callback = editor.win.calls[-1]
        self.assertEqual(delay, 180)
        callback()

        editor.sync_entries.assert_called_once_with()
        editor.app._schedule_autosave.assert_called_once_with()


class ActiveRegionCommandTests(unittest.TestCase):
    def make_editor(self, regions, active):
        editor = make_editor_stub()
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
        editor = make_editor_stub()
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
        editor = make_editor_stub()
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

        editor = make_editor_stub()
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


class TimelineEfficiencyTests(unittest.TestCase):
    """EditWindow v1.2.9 navigation tools stay deterministic without Tk."""

    class Canvas:
        def __init__(self, width=500, scrollregion="0 0 3000 300"):
            self.width = width
            self.scrollregion = scrollregion
            self.x_targets = []

        def winfo_width(self):
            return self.width

        def cget(self, option):
            self.assert_option = option
            return self.scrollregion

        def xview_moveto(self, value):
            self.x_targets.append(value)

    def make_editor(self, regions=(), workspace=None):
        editor = make_editor_stub()
        editor._session.workspace = workspace
        editor._session.markers = am._normalize_timeline_markers(
            getattr(workspace, "timeline_markers", []) if workspace else [],
        )
        editor.tracks = [{"regions": list(regions)}]
        editor.px_per_sec = 100.0
        editor.active_region = None
        editor.selected_regions = []
        editor.selection = None
        editor.canvas = self.Canvas()
        editor.redraw = mock.Mock()
        editor.app = types.SimpleNamespace(
            _schedule_autosave=mock.Mock(),
            _sync_main_player_playhead=mock.Mock(),
        )
        return editor

    def test_markers_add_name_jump_delete_and_workspace_persist(self):
        workspace = am.Workspace("Timeline")
        first_region = am.EditRegion("/tmp/a.wav", 0.0, 5.0, 0.0)
        editor = self.make_editor([first_region], workspace)
        editor.playhead = 1.25

        first = editor.cmd_add_marker()
        self.assertEqual(first, {"time": 1.25, "name": "Marker 1"})
        self.assertEqual(workspace.timeline_markers, [first])
        editor.app._schedule_autosave.assert_called_once_with()
        self.assertIs(editor._marker_hit_at_timeline_x(126), first)

        with mock.patch.object(am.simpledialog, "askstring", return_value="Verse"):
            editor.cmd_rename_nearest_marker()
        self.assertEqual(first["name"], "Verse")
        self.assertEqual(workspace.timeline_markers[0]["name"], "Verse")

        editor.playhead = 3.0
        second = editor.cmd_add_marker()
        self.assertEqual(second["time"], 3.0)
        self.assertIs(editor.jump_to_previous_marker(), first)
        self.assertAlmostEqual(editor.playhead, 1.25)
        self.assertIs(editor.jump_to_next_marker(), second)
        self.assertAlmostEqual(editor.playhead, 3.0)
        self.assertTrue(editor.cmd_delete_nearest_marker())
        self.assertEqual(workspace.timeline_markers, [first])

    def test_marker_serialization_restore_and_project_data_round_trip(self):
        app = object.__new__(am.AudioBalancerApp)
        app._serialize_dir_tree = lambda _ws: []
        app._sync_open_edit_window_entries = lambda: None
        app.export_folder = ""
        app.active_ws_idx = 0
        workspace = am.Workspace(
            "Timeline",
            timeline_markers=[
                {"time": "4.5", "name": "Outro"},
                {"time": -2, "name": "invalid"},
                {"time": 1.0, "name": "Intro"},
            ],
        )
        app.workspaces = [workspace]

        serialized = app._serialize_workspace(workspace)
        project_data = app._project_data()
        self.assertEqual(
            serialized["timeline_markers"],
            [{"time": 1.0, "name": "Intro"}, {"time": 4.5, "name": "Outro"}],
        )
        self.assertEqual(project_data["workspaces"][0]["timeline_markers"], serialized["timeline_markers"])

        restored = am.Workspace("Restored")
        app._update_empty_hint = mock.Mock()
        app._restore_workspace_into(restored, {
            "timeline_markers": serialized["timeline_markers"],
            "audio_files": [],
        })
        self.assertEqual(restored.timeline_markers, serialized["timeline_markers"])
        self.assertEqual(am.EditSession(workspace=restored).markers, restored.timeline_markers)

    def test_zoom_to_fit_and_selection_compute_bounded_scale(self):
        region = am.EditRegion("/tmp/a.wav", 0.0, 10.0, 0.0)
        editor = self.make_editor([region])

        self.assertAlmostEqual(editor.zoom_to_fit(), 44.8)
        self.assertEqual(editor.canvas.x_targets[-1], 0.0)

        editor.selection = (0, 2.0, 4.0)
        selection_scale = editor.zoom_to_selection()
        self.assertAlmostEqual(selection_scale, 205.0)
        self.assertGreaterEqual(editor.canvas.x_targets[-1], 0.0)
        self.assertLessEqual(editor.canvas.x_targets[-1], 1.0)

    def test_reverse_preview_uses_reversed_buffer_and_playhead_math(self):
        region = am.EditRegion("/tmp/a.wav", 0.0, 1.0, 0.0)
        editor = self.make_editor([region])
        editor.tracks[0]["entry"] = {
            "audio": types.SimpleNamespace(frame_rate=10, channels=1),
        }
        editor._refresh_all_crossfades = mock.Mock()
        editor._tick = mock.Mock()
        editor.playhead = 0.5
        editor.app = types.SimpleNamespace(
            _stop_main_playback_for_editor=mock.Mock(),
            _require_regions_media_available=mock.Mock(),
            _render_region_list=lambda *_args, **_kwargs: np.arange(10, dtype=np.float32) / 10.0,
            apply_soft_clipper=lambda samples: samples,
            get_selected_device=lambda: None,
        )

        with mock.patch.object(am.sd, "stop"), mock.patch.object(am.sd, "play") as play:
            editor.play(direction=-1)

        reversed_buffer = play.call_args.args[0]
        np.testing.assert_array_equal(reversed_buffer, np.array([0.5, 0.4, 0.3, 0.2, 0.1, 0.0], dtype=np.float32))
        self.assertEqual(editor._play_direction, -1)
        self.assertAlmostEqual(editor._play_origin, 0.5)
        self.assertAlmostEqual(editor._playhead_after_elapsed(0.3), 0.2)

    def test_j_k_l_dispatch_without_changing_existing_transport_bindings(self):
        editor = self.make_editor()
        editor.play = mock.Mock()
        editor._set_transport_state = mock.Mock()
        editor.cmd_play_backward()
        editor.cmd_play_forward()
        self.assertEqual(editor.play.call_args_list, [mock.call(direction=-1), mock.call(direction=1)])

        editor.is_playing = True
        editor.pause = mock.Mock()
        editor.cmd_stop_shuttle()
        editor.pause.assert_called_once_with(by_space=False)


class EditWindowPlayheadFollowTests(unittest.TestCase):
    """Long Edit timelines keep their own visible playhead without Tk."""

    class Canvas:
        def __init__(self, width=500, total_width=3000, first=0.0):
            self.width = width
            self.total_width = total_width
            self.first = first
            self.auto_moves = []
            self.manual_commands = []

        def cget(self, option):
            self.assert_option = option
            return f"0 0 {self.total_width} 300"

        def winfo_width(self):
            return self.width

        def xview(self, *args):
            if args:
                self.manual_commands.append(args)
                if args[0] == "moveto":
                    self.first = max(0.0, min(1.0, float(args[1])))
            visible = min(1.0, self.first + self.width / self.total_width)
            return self.first, visible

        def xview_moveto(self, value):
            self.first = max(0.0, min(1.0, float(value)))
            self.auto_moves.append(self.first)

        def delete(self, *_args):
            pass

        def create_line(self, *_args, **_kwargs):
            return 1

        def create_polygon(self, *_args, **_kwargs):
            return 2

    def make_editor(self, *, session=None, first=0.0, embedded=False, main_playing=False):
        editor = make_editor_stub()
        if session is not None:
            editor._session = session
        editor._closing = False
        editor._is_embedded = embedded
        editor._play_direction = 1
        editor._playhead_follow_paused_until = 0.0
        editor.tracks = [{}]
        editor.px_per_sec = 100.0
        editor.canvas = self.Canvas(first=first)
        editor.app = types.SimpleNamespace(is_playing=main_playing)
        editor._schedule_redraw = mock.Mock()
        return editor

    def test_forward_follow_starts_after_playhead_leaves_viewport_and_does_not_jitter(self):
        editor = self.make_editor()
        editor.is_playing = True

        editor.playhead = 4.99  # still in the first 500px viewport
        editor._draw_playhead_only()
        self.assertEqual(editor.canvas.auto_moves, [])

        editor.playhead = 5.2
        editor._draw_playhead_only()
        self.assertEqual(len(editor.canvas.auto_moves), 1)
        self.assertAlmostEqual(editor.canvas.auto_moves[0], (520.0 - 350.0) / 3000.0)

        # The 70% anchor leaves enough room that the next tick does not issue a second move.
        editor._draw_playhead_only()
        self.assertEqual(len(editor.canvas.auto_moves), 1)

    def test_reverse_follow_keeps_past_timeline_visible_and_does_not_repeat(self):
        editor = self.make_editor(first=0.5)
        editor.is_playing = True
        editor._play_direction = -1
        editor.playhead = 13.0  # 1300px: just left of the 1500px viewport edge

        editor._draw_playhead_only()
        self.assertEqual(len(editor.canvas.auto_moves), 1)
        self.assertAlmostEqual(editor.canvas.auto_moves[0], (1300.0 - 150.0) / 3000.0)

        editor._draw_playhead_only()
        self.assertEqual(len(editor.canvas.auto_moves), 1)

    def test_session_notification_follows_in_embedded_and_standalone_views(self):
        session = am.EditSession()
        embedded = self.make_editor(session=session, first=0.0, embedded=True)
        standalone = self.make_editor(session=session, first=0.4, embedded=False)
        session.views = [embedded, standalone]
        session.play_owner = embedded
        session.is_playing = True
        session.playhead = 10.0

        session.notify_playhead()

        self.assertEqual(len(embedded.canvas.auto_moves), 1)
        self.assertEqual(len(standalone.canvas.auto_moves), 1)
        self.assertTrue(embedded._is_embedded)
        self.assertFalse(standalone._is_embedded)

    def test_main_transport_and_manual_horizontal_scroll_grace_period(self):
        editor = self.make_editor(main_playing=True)
        editor._play_direction = -1  # A previous Edit shuttle must not affect main-preview follow.
        editor.playhead = 8.0

        # The bottom embedded Edit pane receives main-player playhead broadcasts even though its
        # own EditSession transport is idle, so it must still follow.
        editor._draw_playhead_only()
        self.assertEqual(len(editor.canvas.auto_moves), 1)
        self.assertAlmostEqual(editor.canvas.auto_moves[0], (800.0 - 350.0) / 3000.0)

        editor.canvas.auto_moves.clear()
        editor.playhead = 2.0
        with mock.patch.object(am.time, "monotonic", return_value=10.0):
            editor._editor_xview("moveto", "0.5")
            self.assertFalse(editor._follow_playhead_if_needed())
        self.assertEqual(editor.canvas.auto_moves, [])
        self.assertEqual(editor.canvas.manual_commands, [("moveto", "0.5")])

        with mock.patch.object(am.time, "monotonic", return_value=12.0):
            self.assertTrue(editor._follow_playhead_if_needed())
        self.assertEqual(len(editor.canvas.auto_moves), 1)


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


class ProjectMediaManagementTests(unittest.TestCase):
    """素材管理 helper 的純資料層回歸測試（不需要建立 Tk 視窗）。"""

    @staticmethod
    def _entry(path, name=None, edit_regions=None):
        return {
            "path": path,
            "name": name or os.path.basename(path),
            "duration": "00:01",
            "status": "🟢 就緒",
            "lufs": -16.0,
            "target_lufs": -16.0,
            "export": True,
            "edit_regions": edit_regions,
        }

    @staticmethod
    def _make_app(workspaces, live_views=()):
        app = object.__new__(am.AudioBalancerApp)
        app.workspaces = list(workspaces)
        app.active_ws_idx = 0
        app._unique_session_views = lambda all_workspaces=False: list(live_views)
        app._submit_analysis = mock.Mock()
        app._schedule_wave_draw = mock.Mock()
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        app._true_peak_displays = mock.Mock(return_value=("--", "--"))
        app._check_icon_on = object()
        app._check_icon_off = object()
        return app

    def test_missing_media_index_lists_entry_and_external_region_impacts(self):
        with tempfile.TemporaryDirectory() as root:
            missing_original = os.path.join(root, "missing-original.wav")
            missing_join = os.path.join(root, "missing-join.wav")
            existing = os.path.abspath(__file__)
            entries = [
                self._entry(missing_original, "Original.wav", None),
                self._entry(
                    existing, "Edited.wav",
                    [am.EditRegion(missing_join, 0.0, 1.0, 0.0).to_dict()],
                ),
                # Explicit empty edit is intentional silence, so its absent original must not be reported.
                self._entry(os.path.join(root, "intentional-silence.wav"), "Silence.wav", []),
            ]
            ws = am.Workspace(
                "Media", audio_files=entries,
                audio_by_path={entry["path"]: entry for entry in entries},
            )
            app = self._make_app([ws])

            missing = {item["path"]: item for item in app._missing_workspace_media(ws)}

            self.assertEqual(set(missing), {missing_original, missing_join})
            self.assertEqual(
                [app._media_reference_impact_label(ref) for ref in missing[missing_original]["references"]],
                ["Original.wav · 原始檔"],
            )
            self.assertEqual(
                [app._media_reference_impact_label(ref) for ref in missing[missing_join]["references"]],
                ["Edited.wav · Region 1"],
            )

    def test_folder_auto_relink_only_uses_unique_matching_basename(self):
        with tempfile.TemporaryDirectory() as root:
            missing_dir = os.path.join(root, "missing")
            candidate_dir = os.path.join(root, "recovered")
            os.makedirs(missing_dir)
            os.makedirs(os.path.join(candidate_dir, "first"))
            os.makedirs(os.path.join(candidate_dir, "second"))
            old_unique = os.path.join(missing_dir, "unique.wav")
            old_ambiguous = os.path.join(missing_dir, "duplicate.wav")
            unique_candidate = os.path.join(candidate_dir, "unique.wav")
            with open(unique_candidate, "wb") as output:
                output.write(b"unique")
            for folder in ("first", "second"):
                with open(os.path.join(candidate_dir, folder, "duplicate.wav"), "wb") as output:
                    output.write(folder.encode("utf-8"))

            entries = [self._entry(old_unique), self._entry(old_ambiguous)]
            ws = am.Workspace(
                "Media", audio_files=entries,
                audio_by_path={entry["path"]: entry for entry in entries},
            )
            app = self._make_app([ws])

            mapping, ambiguous, unmatched = app._auto_relink_mapping_from_folder(ws, candidate_dir)

            self.assertEqual(mapping, {old_unique: unique_candidate})
            self.assertEqual([item["path"] for item in ambiguous], [old_ambiguous])
            self.assertEqual(unmatched, [])

    def test_relink_live_session_clears_stale_history_and_isolates_other_workspace(self):
        with tempfile.TemporaryDirectory() as root:
            replacement = os.path.join(root, "replacement.wav")
            with open(replacement, "wb") as output:
                output.write(b"replacement")
            missing = os.path.join(root, "missing-join.wav")

            entry_a = self._entry(
                replacement, "A.wav",
                [am.EditRegion(missing, 0.0, 1.0, 0.0).to_dict()],
            )
            entry_b = self._entry(
                replacement, "B.wav",
                [am.EditRegion(missing, 0.0, 1.0, 0.0).to_dict()],
            )
            ws_a = am.Workspace("A", audio_files=[entry_a], audio_by_path={replacement: entry_a})
            ws_b = am.Workspace("B", audio_files=[entry_b], audio_by_path={replacement: entry_b})

            session_a = am.EditSession(ws_a)
            live_region_a = am.EditRegion(missing, 0.0, 1.0, 0.0)
            session_a.tracks = [{"entry": entry_a, "regions": [live_region_a]}]
            session_a.undo_stack = [[[am.EditRegion(missing, 0.0, 1.0, 0.0)]]]
            session_a.redo_stack = [[[am.EditRegion(missing, 0.0, 1.0, 0.0)]]]
            session_a.clipboard = [(0, am.EditRegion(missing, 0.0, 1.0, 0.0))]
            editor_a = object.__new__(am.EditWindow)
            editor_a._session = session_a
            editor_a._schedule_redraw = lambda _delay=0: None
            # Call the real write-back boundary after Relink; it must not restore the old path.
            editor_a._refresh_all_crossfades = lambda force=False: None
            editor_a._persist_timeline_markers = lambda schedule_autosave=False: None
            editor_a._session_is_active_workspace = lambda: False

            # This session has changed visible tracks already, but its history still contains the old source.
            history_only = am.EditSession(ws_a)
            history_only.tracks = [{"entry": entry_a, "regions": [am.EditRegion(replacement, 0.0, 1.0, 0.0)]}]
            history_only.undo_stack = [[[am.EditRegion(missing, 0.0, 1.0, 0.0)]]]
            history_only.redo_stack = []
            history_only.clipboard = [(0, am.EditRegion(missing, 0.0, 1.0, 0.0))]
            history_view = types.SimpleNamespace(
                _session=history_only,
                tracks=history_only.tracks,
                _zero_cross_cache={},
                _cross_source_peak_cache={},
                _schedule_redraw=lambda _delay=0: None,
            )

            session_b = am.EditSession(ws_b)
            live_region_b = am.EditRegion(missing, 0.0, 1.0, 0.0)
            session_b.tracks = [{"entry": entry_b, "regions": [live_region_b]}]
            session_b.undo_stack = [[[am.EditRegion(missing, 0.0, 1.0, 0.0)]]]
            session_b.clipboard = [(0, am.EditRegion(missing, 0.0, 1.0, 0.0))]
            other_workspace_view = types.SimpleNamespace(
                _session=session_b,
                tracks=session_b.tracks,
                _zero_cross_cache={},
                _cross_source_peak_cache={},
                _schedule_redraw=lambda _delay=0: None,
            )

            app = self._make_app([ws_a, ws_b], [editor_a, history_view, other_workspace_view])
            editor_a.app = app

            result = app._relink_workspace_media(ws_a, missing, replacement, reload_primary=False)
            editor_a.sync_entries()

            self.assertTrue(result["changed"])
            self.assertEqual(live_region_a.source_path, replacement)
            self.assertEqual(entry_a["edit_regions"][0]["source_path"], replacement)
            self.assertEqual(session_a.undo_stack, [])
            self.assertEqual(session_a.redo_stack, [])
            self.assertIsNone(session_a.clipboard)
            self.assertEqual(history_only.undo_stack, [])
            self.assertEqual(history_only.redo_stack, [])
            self.assertIsNone(history_only.clipboard)
            # Same missing path in another workspace must remain completely untouched.
            self.assertEqual(live_region_b.source_path, missing)
            self.assertEqual(entry_b["edit_regions"][0]["source_path"], missing)
            self.assertEqual(len(session_b.undo_stack), 1)
            self.assertIsNotNone(session_b.clipboard)

    def test_collect_copies_actual_original_and_join_sources_then_relinks(self):
        with tempfile.TemporaryDirectory() as root:
            source_dir = os.path.join(root, "sources")
            project_dir = os.path.join(root, "project")
            os.makedirs(source_dir)
            os.makedirs(project_dir)
            original = os.path.join(source_dir, "original.wav")
            join = os.path.join(source_dir, "joined.wav")
            unused_base = os.path.join(source_dir, "unused-base.wav")
            for path, payload in ((original, b"original"), (join, b"joined"), (unused_base, b"base")):
                with open(path, "wb") as output:
                    output.write(payload)

            entry_original = self._entry(original, "Original.wav", None)
            entry_with_join = self._entry(
                unused_base, "Edited.wav",
                [am.EditRegion(join, 0.0, 1.0, 0.0).to_dict()],
            )
            project_path = os.path.join(project_dir, "mix.abproj")
            ws = am.Workspace(
                "Collect", audio_files=[entry_original, entry_with_join],
                audio_by_path={
                    entry_original["path"]: entry_original,
                    entry_with_join["path"]: entry_with_join,
                },
                project_file_path=project_path,
            )
            app = self._make_app([ws])

            report = app._collect_workspace_media(ws)

            media_dir = os.path.join(project_dir, "Media")
            collected_original = os.path.join(media_dir, "original.wav")
            collected_join = os.path.join(media_dir, "joined.wav")
            self.assertTrue(report["ok"])
            self.assertEqual(set(report["copied"]), {collected_original, collected_join})
            self.assertEqual(entry_original["path"], collected_original)
            self.assertEqual(entry_with_join["path"], unused_base)
            self.assertEqual(entry_with_join["edit_regions"][0]["source_path"], collected_join)
            with open(collected_original, "rb") as copied_original:
                self.assertEqual(copied_original.read(), b"original")
            with open(collected_join, "rb") as copied_join:
                self.assertEqual(copied_join.read(), b"joined")
            self.assertNotIn(os.path.join(media_dir, "unused-base.wav"), report["copied"])

            # Repeating Collect neither overwrites nor makes a second copy of already-collected media.
            again = app._collect_workspace_media(ws)
            self.assertEqual(again["copied"], [])
            self.assertEqual(set(again["already_collected"]), {collected_original, collected_join})

    def test_direct_relink_preserves_original_folder_and_child_position(self):
        paths = MainWorkspaceFileUndoOrderTests.paths("A", ["a0", "a1", "a2"])
        other = MainWorkspaceFileUndoOrderTests.paths("B", ["b0"])
        helper = MainWorkspaceFileUndoOrderTests()
        app, ws, table, _original = helper.make_app([("/tmp/A", paths), ("/tmp/B", other)], ())
        app._unique_session_views = lambda all_workspaces=False: []
        app._submit_analysis = mock.Mock()
        ws.current_file_path = None
        entry_to_relink = ws.audio_by_path[paths[1]]

        with tempfile.TemporaryDirectory() as root:
            replacement = os.path.join(root, "moved", "replacement.wav")
            os.makedirs(os.path.dirname(replacement))
            with open(replacement, "wb") as output:
                output.write(b"replacement")

            result = app._relink_workspace_media(ws, paths[1], replacement, reload_primary=True)

        folder_a = "__folder__::/tmp/A"
        self.assertTrue(result["changed"])
        self.assertEqual(table.get_children(""), (folder_a, "__folder__::/tmp/B"))
        self.assertEqual(table.get_children(folder_a), (paths[0], replacement, paths[2]))
        self.assertEqual(table.parent(replacement), folder_a)
        self.assertEqual(table.index(replacement), 1)
        self.assertFalse(table.exists(paths[1]))
        self.assertFalse(table.exists(f"__folder__::{os.path.dirname(replacement)}"))
        self.assertEqual(entry_to_relink["status"], "🟡 載入中")
        app._submit_analysis.assert_called_once_with(
            entry_to_relink, preserve_saved_lufs=True, workspace=ws,
        )


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
                self.x_target = None
                self.y_calls = []

            def xview(self):
                return 0.2, 0.7

            def winfo_width(self):
                return 500

            def xview_moveto(self, value):
                self.x_target = value

            def yview_scroll(self, amount, unit):
                self.y_calls.append((amount, unit))

        app = object.__new__(am.AudioBalancerApp)
        app._current_ui_scale = 1.0
        app._wheel_dbg = mock.Mock()
        app._schedule_true_peak_overlay_refresh = mock.Mock()
        table = Table()
        # Tk 9 packed delta：高 16-bit 是 X=+2，低 16-bit 是 Y=-40px。
        # X 用比例式 xview_moveto；Y 會依 row height 換算成一列，兩軸都必須保留。
        packed = (2 << 16) | ((-40) & 0xFFFF)

        result = app._scroll_table_by_touchpad(
            table, types.SimpleNamespace(serial=10, delta=packed),
        )

        self.assertEqual(result, "break")
        self.assertAlmostEqual(table.x_target, 0.198)
        self.assertEqual(table.y_calls, [(1, "units")])
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

    def test_meter_fill_and_negative_thirty_tick_share_visible_bottom_baseline(self):
        class Canvas:
            def __init__(self):
                self.width = 28
                self.height = 150
                self.next_id = 1
                self.coords_by_item = {}

            def _id(self, coords):
                value = self.next_id
                self.next_id += 1
                self.coords_by_item[value] = tuple(coords)
                return value

            def winfo_width(self):
                return self.width

            def winfo_height(self):
                return self.height

            def create_line(self, *coords, **kwargs):
                return self._id(coords)

            def create_rectangle(self, *coords, **kwargs):
                return self._id(coords)

            def coords(self, item, *coords):
                self.coords_by_item[item] = tuple(coords)

            def itemconfigure(self, *args, **kwargs):
                pass

        app = object.__new__(am.AudioBalancerApp)
        canvas = Canvas()

        app.draw_meter_canvas(canvas, 1.0)

        items = canvas._am_meter_items
        cyan_bottom = canvas.coords_by_item[items["cyan"]][3]
        minus_thirty_tick = canvas.coords_by_item[items["ticks"][-30]][1]
        # ``height - 1`` is the last visible Canvas pixel.  There is no former
        # 8px black band below the -30 baseline for the meter to appear to start from.
        self.assertEqual(cyan_bottom, canvas.height - 1)
        self.assertEqual(minus_thirty_tick, canvas.height - 1)

    def test_meter_reuses_and_realigns_items_after_small_viewport_resize(self):
        class Canvas:
            def __init__(self):
                self.width = 28
                self.height = 150
                self.lines = 0
                self.rectangles = 0
                self.next_id = 1
                self.coords_by_item = {}

            def _id(self, coords):
                value = self.next_id
                self.next_id += 1
                self.coords_by_item[value] = tuple(coords)
                return value

            def winfo_width(self):
                return self.width

            def winfo_height(self):
                return self.height

            def create_line(self, *coords, **kwargs):
                self.lines += 1
                return self._id(coords)

            def create_rectangle(self, *coords, **kwargs):
                self.rectangles += 1
                return self._id(coords)

            def coords(self, item, *coords):
                self.coords_by_item[item] = tuple(coords)

            def itemconfigure(self, *args, **kwargs):
                pass

        app = object.__new__(am.AudioBalancerApp)
        canvas = Canvas()
        app.draw_meter_canvas(canvas, 0.5)
        canvas.width = 34
        canvas.height = 96
        app.draw_meter_canvas(canvas, 0.5)

        self.assertEqual(canvas.lines, 6)
        self.assertEqual(canvas.rectangles, 3)
        self.assertEqual(canvas.coords_by_item[canvas._am_meter_items["ticks"][-30]][1], 95)
        self.assertEqual(canvas.coords_by_item[canvas._am_meter_items["cyan"]][3], 95)

    def test_lufs_scrollregion_refresh_coalesces_configure_burst_and_repairs_meter_once(self):
        class Canvas:
            def __init__(self):
                self.itemconfigure_calls = []
                self.configure_calls = []

            def winfo_width(self):
                return 240

            def winfo_height(self):
                return 180

            def itemconfigure(self, item, **kwargs):
                self.itemconfigure_calls.append((item, kwargs))

            def configure(self, **kwargs):
                self.configure_calls.append(kwargs)

        class Wrapper:
            def winfo_reqheight(self):
                return 520

            def winfo_height(self):
                return 520

        app = object.__new__(am.AudioBalancerApp)
        app.lufs_scroll_canvas = Canvas()
        app.lufs_wrapper = Wrapper()
        app._lufs_scroll_window = "contents"
        app._lufs_scroll_content_width = None
        app._lufs_scroll_refresh_job = None
        app._lufs_scroll_repaint_pending = False
        scheduled = []
        app.after_idle = lambda callback: scheduled.append(callback) or "lufs-idle"
        app._repair_lufs_meter_paint = mock.Mock()

        app._on_lufs_scroll_canvas_configure(types.SimpleNamespace(width=240))
        app._on_lufs_scroll_content_configure()
        app._on_lufs_scroll_content_configure()

        self.assertEqual(len(scheduled), 1)
        scheduled[0]()
        self.assertEqual(
            app.lufs_scroll_canvas.configure_calls[-1]["scrollregion"],
            (0, 0, 240, 520),
        )
        app._repair_lufs_meter_paint.assert_called_once_with()

    def test_meter_uses_true_sample_peak_not_scaled_rms(self):
        # 滿幅正弦波的 RMS 約 -3 dBFS，但 peak 必須仍是 0 dBFS；舊邏輯把 RMS
        # 乘 4 後會錯誤顯示為正 dB。瞬態也不應因 50ms RMS 平均而被抹掉。
        sine = np.sin(np.linspace(0, 2 * np.pi, 4_800, endpoint=False)).astype(np.float32)
        self.assertEqual(am.AudioBalancerApp._meter_channel_peaks(sine), (1.0, 1.0))

        impulse = np.zeros((2_400, 2), dtype=np.float32)
        impulse[0] = (1.0, 0.5)
        left, right = am.AudioBalancerApp._meter_channel_peaks(impulse)
        self.assertEqual(left, 1.0)
        self.assertEqual(right, 0.5)
        self.assertAlmostEqual(
            am.AudioBalancerApp._meter_fill_fraction(0.5),
            (20.0 * math.log10(0.5) + 30.0) / 30.0,
            places=6,
        )

    def test_editor_redraw_throttle_coalesces_motion_burst(self):
        class Window:
            def __init__(self):
                self.calls = []

            def after(self, delay, fn):
                self.calls.append((delay, fn))
                return "redraw-job"

            def after_cancel(self, job):
                pass

        editor = make_editor_stub()
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
        editor = make_editor_stub()
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
                return 400

            def winfo_height(self):
                return 300

            def cget(self, option):
                return "0 0 500 400"

            def xview_moveto(self, value):
                self.x_target = value

            def yview_moveto(self, value):
                self.y_target = value

        editor = make_editor_stub()
        editor.canvas = Canvas()
        editor.app = types.SimpleNamespace(_wheel_dbg=lambda _message: None)
        editor._schedule_redraw = mock.Mock()
        packed = (2 << 16) | ((-3) & 0xFFFF)

        result = editor._on_editor_touchpad(
            types.SimpleNamespace(delta=packed, widget=editor.canvas),
        )

        self.assertEqual(result, "break")
        self.assertAlmostEqual(editor.canvas.x_target, 0.196)
        self.assertAlmostEqual(editor.canvas.y_target, 0.3075)
        editor._schedule_redraw.assert_called_once_with(16)

    def test_editor_yview_callback_redraws_only_when_fraction_changes(self):
        editor = make_editor_stub()
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

        editor = make_editor_stub()
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
        editor = make_editor_stub()
        editor.canvas = canvas
        editor._fade_imgs = []
        editor._make_fade_image = mock.Mock(return_value=None)

        editor._draw_fade_overlay(0.0, 3000.0, 0.0, 80.0, 0.4, True)

        canvas.create_image.assert_not_called()
        canvas.create_polygon.assert_called_once()
        canvas.create_line.assert_called_once()

    def test_direct_editor_destroy_stops_audio_and_invalidates_tick(self):
        editor = make_editor_stub()
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

    def test_direct_destroy_of_play_owner_stops_shared_session_too(self):
        owner = make_editor_stub()
        peer = make_editor_stub()
        peer._session = owner._session
        owner._session.views = [owner, peer]
        owner._session.play_owner = owner
        owner._session.is_playing = True
        owner.win = object()
        owner.app = types.SimpleNamespace(_edit_window=owner)
        owner._closing = False
        owner._play_generation = 4
        owner._cancel_scheduled_redraw = mock.Mock()
        owner._unbind_global_shortcuts = mock.Mock()

        with mock.patch.object(am.sd, "stop") as stop:
            owner._on_window_destroy(types.SimpleNamespace(widget=owner.win))

        stop.assert_called_once_with()
        self.assertIsNone(owner._session.play_owner)
        self.assertFalse(owner._session.is_playing)
        self.assertEqual(owner._session.transport_state, am.EditWindow.TRANSPORT_READY)
        self.assertEqual(owner._session.views, [peer])

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
        app.playback_duration = 0.0
        audio = types.SimpleNamespace(duration_seconds=2 * 60 * 60)

        app.draw_waveform(audio)

        self.assertLess(app.waveform_canvas.lines, 20)
        step = am._nice_time_grid_step(audio.duration_seconds, 80.0)
        self.assertLessEqual(
            math.floor(audio.duration_seconds / step) + 1,
            am._MAX_TIMELINE_GRID_LINES + 1,
        )


class ToolbarHorizontalScrollTests(unittest.TestCase):
    """Narrow Edit panes must keep both toolbars reachable without touching timeline scroll."""

    class Canvas:
        def __init__(self, first=0.0, last=0.5, width=200, height=40):
            self.first = first
            self.last = last
            self.width = width
            self.height = height
            self.xview_commands = []
            self.item_options = None
            self.scrollregion = None

        def xview(self, *args):
            if not args:
                return self.first, self.last
            self.xview_commands.append(args)
            if args[0] == "moveto":
                visible = self.last - self.first
                self.first = float(args[1])
                self.last = min(1.0, self.first + visible)

        def xview_moveto(self, value):
            visible = self.last - self.first
            self.first = float(value)
            self.last = min(1.0, self.first + visible)

        def winfo_width(self):
            return self.width

        def winfo_height(self):
            return self.height

        def itemconfigure(self, _item, **kwargs):
            self.item_options = kwargs

        def configure(self, **kwargs):
            self.scrollregion = kwargs.get("scrollregion", self.scrollregion)

    class Content:
        def __init__(self, width=560, height=40):
            self.width = width
            self.height = height

        def winfo_reqwidth(self):
            return self.width

        def winfo_reqheight(self):
            return self.height

    @staticmethod
    def row(canvas, content=None):
        return {
            "canvas": canvas,
            "content": content or ToolbarHorizontalScrollTests.Content(),
            "window_id": 1,
            "refresh_job": None,
        }

    def test_shift_wheel_moves_only_its_own_toolbar_row(self):
        editor = object.__new__(am.EditWindow)
        first = self.Canvas()
        second = self.Canvas()
        first_row = self.row(first)
        second_row = self.row(second)

        result = editor._on_toolbar_shift_wheel(
            types.SimpleNamespace(delta=-120, num=None), first_row,
        )

        self.assertEqual(result, "break")
        self.assertAlmostEqual(first.first, 0.12)
        self.assertAlmostEqual(second.first, 0.0)

        editor._toolbar_xview(second_row, "moveto", 0.3)
        self.assertAlmostEqual(first.first, 0.12)
        self.assertAlmostEqual(second.first, 0.3)

    def test_narrow_viewport_uses_full_content_width_for_scrollregion(self):
        editor = object.__new__(am.EditWindow)
        canvas = self.Canvas(width=180)
        row = self.row(canvas, self.Content(width=560, height=40))

        editor._refresh_toolbar_scrollregion(row)

        self.assertEqual(canvas.item_options, {"width": 560, "height": 40})
        self.assertEqual(canvas.scrollregion, (0, 0, 560, 40))

    def test_shift_wheel_over_fully_visible_row_does_not_scroll_timeline(self):
        editor = object.__new__(am.EditWindow)
        canvas = self.Canvas(first=0.0, last=1.0)
        row = self.row(canvas)

        result = editor._on_toolbar_shift_wheel(
            types.SimpleNamespace(delta=-120, num=None), row,
        )

        self.assertEqual(result, "break")
        self.assertEqual(canvas.xview_commands, [])


class EditorMeterRoutingTests(unittest.TestCase):
    """Edit transport 必須把實際送出的 PCM buffer 路由到主畫面 L/R meter。"""

    def test_editor_meter_uses_exact_buffer_and_wraps_short_cycle_ranges(self):
        app = object.__new__(am.AudioBalancerApp)
        app._meter_source = None
        app._meter_generation = 0
        app._apply_meter_chunk = mock.Mock(return_value=True)
        owner = types.SimpleNamespace()
        buffer = np.array([0.1, 0.2, 0.3, 0.9], dtype=np.float32)

        self.assertTrue(app._begin_editor_meter(owner, buffer, 100, loop=True))
        self.assertIs(app._meter_source, owner)
        self.assertTrue(app._update_editor_meter(owner, buffer, 100, 0.03, loop=True))

        chunk = app._apply_meter_chunk.call_args.args[0]
        # 50ms at 100Hz = five samples.  A 40ms cycle must wrap more than once,
        # rather than dropping the meter window after the first wrap.
        np.testing.assert_allclose(chunk, [0.9, 0.1, 0.2, 0.3, 0.9])

    def test_main_handoff_blocks_stale_editor_meter_updates(self):
        app = object.__new__(am.AudioBalancerApp)
        app._meter_source = None
        app._meter_generation = 0
        app._apply_meter_chunk = mock.Mock(return_value=True)
        owner = types.SimpleNamespace()
        buffer = np.array([0.8, 0.2], dtype=np.float32)

        app._begin_editor_meter(owner, buffer, 100)
        app._begin_main_meter()

        self.assertFalse(app._update_editor_meter(owner, buffer, 100, 0.0))
        app._apply_meter_chunk.assert_not_called()

    def test_editor_tick_updates_active_workspace_meter_without_main_player_state(self):
        class Window:
            def after(self, _delay, _callback):
                return "tick-job"

        workspace = am.Workspace("Active")
        app = types.SimpleNamespace(
            workspaces=[workspace], active_ws_idx=0,
            _update_editor_meter=mock.Mock(),
            _sync_main_player_playhead=mock.Mock(),
            _broadcast_playhead_to_editors=mock.Mock(),
        )
        editor = make_editor_stub()
        editor.app = app
        editor._session.workspace = workspace
        editor._session.play_owner = editor
        editor.transport_state = am.EditWindow.TRANSPORT_PLAYING
        editor.is_playing = True
        editor._play_generation = 7
        editor._play_start_sys = 10.0
        editor._play_sr = 100
        editor._play_len = 1_000
        editor._active_cycle_loop = True
        editor._meter_playback_buffer = np.array([0.1, 0.8], dtype=np.float32)
        editor._meter_playback_sr = 100
        editor._meter_playback_loop = True
        editor._meter_last_update_elapsed = -float("inf")
        editor._playhead_after_elapsed = lambda elapsed: elapsed
        editor._draw_playhead_only = mock.Mock()
        editor.win = Window()

        with mock.patch.object(am.time, "time", return_value=10.1):
            editor._tick(7)

        app._update_editor_meter.assert_called_once()
        owner, buffer, sample_rate, elapsed = app._update_editor_meter.call_args.args
        self.assertIs(owner, editor)
        self.assertIs(buffer, editor._meter_playback_buffer)
        self.assertEqual(sample_rate, 100)
        self.assertAlmostEqual(elapsed, 0.1)
        self.assertEqual(app._update_editor_meter.call_args.kwargs, {"loop": True})
        # Edit 播放不會把主播放器 is_playing 設成 True；這裡只驗證專用 meter route。
        self.assertFalse(getattr(app, "is_playing", False))


if __name__ == "__main__":
    unittest.main()
