"""Run the daily RESSPECT analysis against FASTDB."""

import datetime
import json
import logging
from collections.abc import Mapping

import numpy as np
import pandas as pd
from astropy.time import Time

from resspect.classifiers import random_forest
from resspect.feature_extractors.malanchev import Malanchev
from resspect.query_strategies import uncertainty_sampling

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


def _known_spectrum_root_ids(known_spectra):
    return {
        str(spectrum["root_diaobject_id"])
        for spectrum in known_spectra
        if spectrum.get("root_diaobject_id") is not None
    }


def _object_coordinates(objinfo):
    """Return FASTDB coordinates keyed by root diaobject ID."""

    keys = ("rootid", "ra", "dec")
    if not all(key in objinfo for key in keys):
        return {}
    return {
        str(rootid): (float(ra), float(dec))
        for rootid, ra, dec in zip(objinfo["rootid"], objinfo["ra"], objinfo["dec"])
        if rootid is not None and ra is not None and dec is not None
    }


def _malanchev_feature_columns(bootstrap):
    """Find the bands represented by the bootstrap Malanchev columns."""

    feature_names = Malanchev.feature_names
    feature_names_by_length = sorted(feature_names, key=len, reverse=True)
    bands = []
    for column in bootstrap.columns:
        for feature_name in feature_names_by_length:
            if column.endswith(feature_name):
                band = column[: -len(feature_name)]
                if band and band not in bands:
                    bands.append(band)
                break

    columns = [f"{band}{feature_name}" for band in bands for feature_name in feature_names]
    missing = sorted(set(columns) - set(bootstrap.columns))
    if not columns or missing:
        raise ValueError("Bootstrap file does not contain a complete set of Malanchev features")
    return bands, columns


def extract_malanchev_features(lightcurves, eligible_root_ids, bands, feature_columns):
    """Extract the same Malanchev features used by Amanda's notebook."""

    rows = []
    for lightcurve in lightcurves:
        rootid = str(lightcurve.get("rootid"))
        if rootid not in eligible_root_ids:
            continue

        photometry = pd.DataFrame(
            {
                key: lightcurve.get(key, [])
                for key in ("mjd", "band", "flux", "fluxerr")
            }
        ).dropna()
        if photometry.empty:
            LOGGER.warning("Skipping %s: FASTDB returned no usable photometry", rootid)
            continue

        extractor = Malanchev()
        extractor.photometry = photometry
        extractor.filters = bands
        extractor.fit_all()
        if len(extractor.features) != len(feature_columns) or "None" in extractor.features:
            LOGGER.warning("Skipping %s: Malanchev feature extraction failed", rootid)
            continue

        row = {"rootid": rootid}
        row.update(dict(zip(feature_columns, extractor.features)))
        rows.append(row)
    return pd.DataFrame(rows, columns=["rootid", *feature_columns])


def select_spectrum_targets(hot_transients, eligible_root_ids, config, run_dir):
    """Classify the hot-transient pool and apply uncertainty sampling."""

    if config.bootstrap_features is None:
        raise ValueError("bootstrap_features must be set before scientific analysis can run")
    if not config.bootstrap_features.is_file():
        raise ValueError(f"Bootstrap feature file does not exist: {config.bootstrap_features}")

    bootstrap = pd.read_csv(config.bootstrap_features, index_col=False)
    bands, feature_columns = _malanchev_feature_columns(bootstrap)
    training = bootstrap[bootstrap["orig_sample"] == "train"]
    training_labels = (training["type"] == "Ia").astype(int).to_numpy()
    if len(np.unique(training_labels)) != 2:
        raise ValueError("Bootstrap training sample must contain both Ia and non-Ia objects")

    pool = extract_malanchev_features(
        hot_transients["ltcvs"], set(eligible_root_ids), bands, feature_columns
    )
    pool.to_csv(run_dir / "pool-features.csv", index=False)
    if pool.empty:
        return [], 0

    train_features = training[feature_columns].to_numpy(dtype=float, copy=True)
    pool_features = pool[feature_columns].to_numpy(dtype=float, copy=True)
    train_features[~np.isfinite(train_features)] = -999
    pool_features[~np.isfinite(pool_features)] = -999

    _predicted_class, class_probability, _classifier = random_forest(
        train_features,
        training_labels,
        pool_features,
        n_estimators=1000,
        random_state=42,
    )
    rootids = pool["rootid"].to_numpy()
    selected_indices = uncertainty_sampling(
        class_prob=class_probability,
        test_ids=rootids,
        queryable_ids=rootids,
        batch=min(config.batch_size, len(rootids)),
    )

    coordinates = _object_coordinates(hot_transients.get("objinfo", {}))
    proposals = []
    for index in selected_indices:
        rootid = str(rootids[index])
        if rootid not in coordinates:
            LOGGER.warning("Skipping selected object %s: FASTDB returned no coordinates", rootid)
            continue
        ra, dec = coordinates[rootid]
        proposals.append(
            {
                "rootid": rootid,
                "ra": ra,
                "dec": dec,
                "priority": 3,  # TODO: replace after the targeting priority policy is agreed.
                "probability_ia": float(class_probability[index, 1]),
            }
        )
    return proposals, len(pool)


def submit_spectrum_requests(fastdb, requester, proposals):
    """Submit RESSPECT's selected targets to FASTDB."""

    return fastdb.post(
        "/spectrum/askforspectrum",
        json={
            "requester": requester,
            "rootids": [proposal["rootid"] for proposal in proposals],
            "ras": [proposal["ra"] for proposal in proposals],
            "decs": [proposal["dec"] for proposal in proposals],
            "priorities": [proposal["priority"] for proposal in proposals],
        },
    )


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
    known_root_ids = _known_spectrum_root_ids(known_spectra)
    requested_root_ids = {
        str(request["root_diaobject_id"])
        for request in previous_requests.get("wantedspectra", [])
        if request.get("root_diaobject_id") is not None
    }
    eligible_root_ids = sorted(hot_root_ids - requested_root_ids - known_root_ids)

    proposals = []
    features_extracted = 0
    if eligible_root_ids:
        proposals, features_extracted = select_spectrum_targets(
            hot_transients, eligible_root_ids, config, run_dir
        )
    _write_json(run_dir / "proposed-spectrum-requests.json", {"requests": proposals})

    submitted = 0
    if proposals and not dry_run:
        response = submit_spectrum_requests(fastdb, config.requester, proposals)
        submitted = int(response.get("num", len(proposals)))

    summary = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "mode": "dry-run" if dry_run else "live",
        "processing_version": config.processing_version,
        "requester": config.requester,
        "detected_in_last_days": config.detected_in_last_days,
        "hot_transients": len(hot_root_ids),
        "known_spectra": len(known_spectra),
        "hot_transients_with_known_spectra": len(hot_root_ids & known_root_ids),
        "previously_requested_by_requester": len(requested_root_ids),
        "eligible_after_request_filter": len(eligible_root_ids),
        "features_extracted": features_extracted,
        "targets_selected": len(proposals),
        "requests_submitted": submitted,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "eligible-rootids.json", {"rootids": eligible_root_ids})

    LOGGER.info(
        "Daily analysis complete: %d hot, %d eligible, %d selected, %d submitted",
        len(hot_root_ids),
        len(eligible_root_ids),
        len(proposals),
        submitted,
    )
    return summary
