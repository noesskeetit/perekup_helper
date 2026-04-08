"""MLflow tracking wrapper for PerekupHelper pricing models.

Provides lightweight experiment tracking with local file-based storage.
No MLflow server required -- all data lives in the ``mlruns/`` directory.

Typical usage inside ``pricing_trainer.py``::

    from app.services.mlflow_tracking import log_training_run, promote_if_better

    stats = model.train(df)
    model.save(tmp_dir / "price_model.pkl")
    run_id = log_training_run(stats, tmp_dir)
    promoted = promote_if_better(stats, tmp_dir)
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import mlflow

logger = logging.getLogger(__name__)

EXPERIMENT_NAME = "perekup-pricing"
TRACKING_URI = "file:./mlruns"

# Model artifact filenames expected in a model directory
_MODEL_FILES = [
    "price_model.pkl",
    "price_model_meta.pkl",
    "lgb_model.txt",
    "cb_te_model.pkl",
]


def _ensure_experiment() -> str:
    """Set tracking URI and create/get the experiment. Returns experiment id."""
    mlflow.set_tracking_uri(TRACKING_URI)
    experiment = mlflow.get_experiment_by_name(EXPERIMENT_NAME)
    exp_id = mlflow.create_experiment(EXPERIMENT_NAME) if experiment is None else experiment.experiment_id
    return exp_id


def log_training_run(stats: dict, model_dir: Path) -> str | None:
    """Log a completed training run to MLflow.

    Parameters
    ----------
    stats : dict
        Training statistics returned by ``PriceModel.train()``.
        Expected keys: ``samples``, ``quantile_metrics``, ``cv_mape_p50``,
        ``lgb_p50_mape``, ``cb_te_p50_mape``, ``feature_importance``.
    model_dir : Path
        Directory containing saved model files to log as artifacts.

    Returns
    -------
    str | None
        The MLflow run ID, or ``None`` if logging failed.
    """
    if stats.get("status") != "trained":
        return None

    try:
        exp_id = _ensure_experiment()

        with mlflow.start_run(experiment_id=exp_id) as run:
            # -- Params --
            mlflow.log_param("training_samples", stats.get("samples", 0))

            # Feature list (truncated to MLflow's 500-char limit)
            feat_imp = stats.get("feature_importance", {})
            top_features = list(feat_imp.keys())[:20]
            mlflow.log_param("top_features", ", ".join(top_features))

            # -- Metrics --
            qm = stats.get("quantile_metrics", {})
            for quantile_name, metrics in qm.items():
                mlflow.log_metric(f"{quantile_name}_mape", metrics.get("mape", 0))
                mlflow.log_metric(f"{quantile_name}_mae", metrics.get("mae", 0))

            if "cv_mape_p50" in stats:
                mlflow.log_metric("cv_mape_p50", stats["cv_mape_p50"])
            if "lgb_p50_mape" in stats:
                mlflow.log_metric("lgb_p50_mape", stats["lgb_p50_mape"])
            if "cb_te_p50_mape" in stats:
                mlflow.log_metric("cb_te_p50_mape", stats["cb_te_p50_mape"])

            # -- Artifacts --
            for filename in _MODEL_FILES:
                filepath = model_dir / filename
                if filepath.exists():
                    mlflow.log_artifact(str(filepath))

            run_id = run.info.run_id
            logger.info("MLflow run logged: %s (P50 MAPE=%.1f%%)", run_id, qm.get("P50", {}).get("mape", -1))
            return run_id

    except Exception:
        logger.exception("Failed to log MLflow run")
        return None


def get_best_model_mape() -> float | None:
    """Return the P50 MAPE of the best production run, or None if no runs exist.

    Searches for runs tagged ``status=production`` in the experiment.
    Falls back to the run with the lowest ``P50_mape`` if no production tag.
    """
    try:
        exp_id = _ensure_experiment()

        # First try: find production-tagged runs
        runs = mlflow.search_runs(
            experiment_ids=[exp_id],
            filter_string="tags.status = 'production'",
            order_by=["metrics.P50_mape ASC"],
            max_results=1,
        )
        if not runs.empty:
            return float(runs.iloc[0]["metrics.P50_mape"])

        # Fallback: best P50_mape across all runs
        runs = mlflow.search_runs(
            experiment_ids=[exp_id],
            filter_string="",
            order_by=["metrics.P50_mape ASC"],
            max_results=1,
        )
        if not runs.empty and "metrics.P50_mape" in runs.columns:
            val = runs.iloc[0]["metrics.P50_mape"]
            if val == val:  # check not NaN
                return float(val)

        return None

    except Exception:
        logger.exception("Failed to query MLflow for best MAPE")
        return None


def promote_if_better(stats: dict, tmp_model_dir: Path, production_dir: Path | None = None) -> bool:
    """Compare new model's MAPE with the production best; promote if better.

    Parameters
    ----------
    stats : dict
        Training stats from the current run (must contain ``quantile_metrics.P50.mape``).
    tmp_model_dir : Path
        Temporary directory where the new model files are saved.
    production_dir : Path | None
        Production model directory (defaults to ``models/``).

    Returns
    -------
    bool
        True if the new model was promoted to production.
    """
    from app.services.pricing import MODEL_DIR

    prod_dir = production_dir or MODEL_DIR
    current_mape = stats.get("quantile_metrics", {}).get("P50", {}).get("mape")
    if current_mape is None:
        logger.warning("No P50 MAPE in stats, cannot compare")
        return False

    best_mape = get_best_model_mape()

    if best_mape is not None and current_mape > best_mape:
        # New model is worse -- reject
        logger.warning(
            "New model MAPE %.1f%% > production best %.1f%% -- rejecting",
            current_mape,
            best_mape,
        )
        _tag_latest_run("rejected")
        return False

    # New model is better or no prior model exists -- promote
    logger.info(
        "Promoting new model (MAPE %.1f%%) to production (previous best: %s)",
        current_mape,
        f"{best_mape:.1f}%" if best_mape is not None else "none",
    )

    # Copy model files from temp to production
    prod_dir.mkdir(parents=True, exist_ok=True)
    for filename in _MODEL_FILES:
        src = tmp_model_dir / filename
        dst = prod_dir / filename
        if src.exists():
            shutil.copy2(str(src), str(dst))

    _tag_latest_run("production")
    # Remove production tag from previous best
    _demote_previous_production_runs()

    return True


def _tag_latest_run(status: str) -> None:
    """Tag the most recent run in the experiment with the given status."""
    try:
        exp_id = _ensure_experiment()
        runs = mlflow.search_runs(
            experiment_ids=[exp_id],
            order_by=["start_time DESC"],
            max_results=1,
        )
        if not runs.empty:
            run_id = runs.iloc[0]["run_id"]
            client = mlflow.tracking.MlflowClient()
            client.set_tag(run_id, "status", status)
    except Exception:
        logger.exception("Failed to tag MLflow run as %s", status)


def _demote_previous_production_runs() -> None:
    """Remove 'production' tag from all runs except the latest one."""
    try:
        exp_id = _ensure_experiment()
        runs = mlflow.search_runs(
            experiment_ids=[exp_id],
            filter_string="tags.status = 'production'",
            order_by=["start_time DESC"],
        )
        if len(runs) <= 1:
            return

        client = mlflow.tracking.MlflowClient()
        # Keep the first (newest) as production, demote the rest
        for _, row in runs.iloc[1:].iterrows():
            client.set_tag(row["run_id"], "status", "superseded")

    except Exception:
        logger.exception("Failed to demote previous production runs")
