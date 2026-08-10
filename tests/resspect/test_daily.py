import json
from pathlib import Path

import pytest

import resspect.daily as daily_module
from resspect.daily import run_daily
from resspect.daily_configuration import load_configuration


class FakeFastDB:
    """Small endpoint-aware FASTDB fake for daily-run tests."""

    def __init__(self):
        self.calls = []

    def post(self, endpoint, json=None):
        """Return a canned response for a FASTDB endpoint."""
        self.calls.append((endpoint, json))
        responses = {
            "/getprocvers": {"status": "ok", "procvers": ["lass"]},
            "/ltcv/gethottransients/lass": {
                "ltcvs": [
                    {"rootid": "new-object"},
                    {"rootid": "already-requested"},
                ],
                "objinfo": {},
            },
            "/spectrum/getknownspectruminfo": [
                {"specinfo_id": "spectrum-1", "root_diaobject_id": "known-object"}
            ],
            "/spectrum/spectrawanted": {
                "status": "ok",
                "wantedspectra": [
                    {
                        "root_diaobject_id": "already-requested",
                        "requester": "resspect-local",
                    }
                ],
            },
            "/spectrum/askforspectrum": {"status": "ok", "num": 1},
        }
        return responses[endpoint]


def write_configuration(path: Path, state_dir: str = "state"):
    """Write the smallest valid daily-run configuration."""
    path.write_text(
        f"""
fastdb_profile: local-kind
processing_version: lass
detected_in_last_days: 14
batch_size: 5
requester: resspect-local
state_dir: {state_dir}
""",
        encoding="utf-8",
    )


def test_load_daily_configuration_resolves_relative_paths(tmp_path):
    """Configuration paths are relative to the YAML file."""
    config_path = tmp_path / "daily.yaml"
    write_configuration(config_path)

    configuration = load_configuration(config_path)

    assert configuration.fastdb_profile == "local-kind"
    assert configuration.processing_version == "lass"
    assert configuration.detected_in_last_days == 14
    assert configuration.batch_size == 5
    assert configuration.state_dir == tmp_path / "state"


def test_load_daily_configuration_requires_requester(tmp_path):
    """A stable requester identity is required for request filtering."""
    config_path = tmp_path / "daily.yaml"
    config_path.write_text(
        """
fastdb_profile: local-kind
processing_version: lass
state_dir: state
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requester"):
        load_configuration(config_path)


def test_daily_run_filters_previous_requests_and_selects_targets(tmp_path, monkeypatch):
    """Objects already requested by this requester leave the candidate pool."""
    config_path = tmp_path / "daily.yaml"
    write_configuration(config_path)
    configuration = load_configuration(config_path)
    fastdb = FakeFastDB()
    proposal = {
        "rootid": "new-object",
        "ra": 1.2,
        "dec": 3.4,
        "priority": 3,
        "probability_ia": 0.5,
    }
    monkeypatch.setattr(
        daily_module,
        "select_spectrum_targets",
        lambda hot_transients, eligible_root_ids, config, run_dir: ([proposal], 1),
    )

    summary = run_daily(
        configuration,
        fastdb,
        dry_run=True,
        run_id="test-run",
    )

    assert summary["hot_transients"] == 2
    assert summary["known_spectra"] == 1
    assert summary["previously_requested_by_requester"] == 1
    assert summary["eligible_after_request_filter"] == 1
    assert summary["features_extracted"] == 1
    assert summary["targets_selected"] == 1
    assert summary["requests_submitted"] == 0

    run_dir = tmp_path / "state" / "runs" / "test-run"
    assert json.loads((run_dir / "eligible-rootids.json").read_text())["rootids"] == ["new-object"]
    assert json.loads((run_dir / "summary.json").read_text())["mode"] == "dry-run"
    requests = json.loads((run_dir / "proposed-spectrum-requests.json").read_text())
    assert requests["requests"] == [proposal]
    assert not any(call[0] == "/spectrum/askforspectrum" for call in fastdb.calls)

    wanted_call = next(call for call in fastdb.calls if call[0] == "/spectrum/spectrawanted")
    assert wanted_call[1]["requester"] == "resspect-local"
    assert wanted_call[1]["processing_version"] == "lass"
    assert wanted_call[1]["detected_since_mjd"] is None


def test_live_run_submits_selected_targets(tmp_path, monkeypatch):
    """A live run sends the proposed requests to FASTDB."""
    config_path = tmp_path / "daily.yaml"
    write_configuration(config_path)
    configuration = load_configuration(config_path)
    fastdb = FakeFastDB()
    proposal = {
        "rootid": "new-object",
        "ra": 1.2,
        "dec": 3.4,
        "priority": 3,
        "probability_ia": 0.5,
    }
    monkeypatch.setattr(
        daily_module,
        "select_spectrum_targets",
        lambda hot_transients, eligible_root_ids, config, run_dir: ([proposal], 1),
    )

    summary = run_daily(configuration, fastdb, dry_run=False, run_id="live-run")

    assert summary["requests_submitted"] == 1
    endpoint, payload = next(
        call for call in fastdb.calls if call[0] == "/spectrum/askforspectrum"
    )
    assert endpoint == "/spectrum/askforspectrum"
    assert payload == {
        "requester": "resspect-local",
        "rootids": ["new-object"],
        "ras": [1.2],
        "decs": [3.4],
        "priorities": [3],
    }


def test_read_stage_rejects_unknown_processing_version(tmp_path):
    """The configured processing version must exist in FASTDB."""
    config_path = tmp_path / "daily.yaml"
    write_configuration(config_path)
    configuration = load_configuration(config_path)
    fastdb = FakeFastDB()
    fastdb.post = lambda endpoint, json=None: {"status": "ok", "procvers": ["different"]}

    with pytest.raises(ValueError, match="Unknown FASTDB processing version 'lass'"):
        run_daily(configuration, fastdb, dry_run=True, run_id="test-run")
