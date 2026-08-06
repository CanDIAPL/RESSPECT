"""Run the daily RESSPECT analysis against FASTDB."""

import datetime
import json
import logging
from collections.abc import Mapping

from astropy.time import Time

LOGGER = logging.getLogger("resspect.daily")


def get_hot_transients(fastdb, config):
    """Get recently detected transient light curves from FASTDB."""

    payload = {"detected_in_last_days": config.detected_in_last_days}
    if config.mjd_now is not None:
        payload["mjd_now"] = config.mjd_now
    return fastdb.post(f"/ltcv/gethottransients/{config.processing_version}", json=payload)


def get_known_spectra(fastdb):
    """Get all spectrum information currently known to FASTDB."""

    return fastdb.post("/spectrum/getknownspectruminfo", json={})


def get_previous_requests(fastdb, config):
    """Get spectrum requests previously made by this RESSPECT instance."""

    # FASTDB currently requires mjd_now even though it is documented as optional.
    mjd_now = config.mjd_now
    if mjd_now is None:
        mjd_now = float(Time.now().tai.mjd + 1 / 1440)

    return fastdb.post(
        "/spectrum/spectrawanted",
        json={
            "processing_version": config.processing_version,
            "requester": config.requester,
            "mjd_now": mjd_now,
            "detected_since_mjd": None,
            "not_claimed_in_last_days": None,
            "no_spectra_in_last_days": None,
        },
    )


def validate_processing_version(fastdb, processing_version):
    """Check that the configured FASTDB processing version exists."""

    result = fastdb.post("/getprocvers")
    available = result.get("procvers", []) if isinstance(result, Mapping) else []
    if processing_version not in available:
        raise ValueError(
            f"Unknown FASTDB processing version '{processing_version}'. Available versions: {available}"
        )


def _root_ids(lightcurves):
    return {
        str(lightcurve["rootid"])
        for lightcurve in lightcurves
        if isinstance(lightcurve, Mapping) and lightcurve.get("rootid") is not None
    }


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def run_daily(config, fastdb, dry_run=False, run_id=None):
    """Run one daily RESSPECT analysis."""

    started = datetime.datetime.now(tz=datetime.timezone.utc)
    run_id = run_id or started.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = config.state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    validate_processing_version(fastdb, config.processing_version)
    hot_transients = get_hot_transients(fastdb, config)
    known_spectra = get_known_spectra(fastdb)
    previous_requests = get_previous_requests(fastdb, config)

    hot_root_ids = _root_ids(hot_transients.get("ltcvs", []))
    requested_root_ids = {
        str(request["root_diaobject_id"])
        for request in previous_requests.get("wantedspectra", [])
        if request.get("root_diaobject_id") is not None
    }
    eligible_root_ids = sorted(hot_root_ids - requested_root_ids)

    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "mode": "dry-run" if dry_run else "live",
        "processing_version": config.processing_version,
        "requester": config.requester,
        "detected_in_last_days": config.detected_in_last_days,
        "hot_transients": len(hot_root_ids),
        "known_spectra": len(known_spectra),
        "previously_requested_by_requester": len(requested_root_ids),
        "eligible_after_request_filter": len(eligible_root_ids),
        "scientific_analysis": "not-yet-run",
        "requests_submitted": 0,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "eligible-rootids.json", {"rootids": eligible_root_ids})

    LOGGER.info(
        "FASTDB read complete: %d hot, %d previously requested, %d eligible",
        len(hot_root_ids),
        len(requested_root_ids),
        len(eligible_root_ids),
    )
    return summary
