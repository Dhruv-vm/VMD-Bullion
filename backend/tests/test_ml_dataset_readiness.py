from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock
from pathlib import Path
import pytest

from app.services.prediction.forecast_engine import get_ml_dataset_readiness_status, TARGET_DAILY_OBSERVATIONS


def test_target_constant_is_50():
    assert TARGET_DAILY_OBSERVATIONS == 50


def test_readiness_status_empty_db():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = []
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    status = get_ml_dataset_readiness_status(db=mock_db, include_csv=False)

    assert status["valid_daily_snapshots_collected"] == 0
    assert status["deduplicated_daily_observations"] == 0
    assert status["target"] == 50
    assert status["target_completed_pct"] == 0.0
    assert status["readiness_status"] == "INSUFFICIENT_HISTORY"
    assert status["first_valid_market_date"] is None
    assert status["latest_valid_market_date"] is None


def test_readiness_status_collecting():
    mock_db = MagicMock()
    base_time = datetime(2026, 11, 1, 4, 30, tzinfo=timezone.utc)
    
    # 5 distinct daily observations
    mock_rows = []
    for day in range(5):
        obs = MagicMock()
        obs.source_published_at = base_time + timedelta(days=day)
        mock_rows.append(obs)

    mock_db.execute.return_value.scalars.return_value.all.return_value = mock_rows
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    status = get_ml_dataset_readiness_status(db=mock_db, include_csv=False)

    assert status["valid_daily_snapshots_collected"] == 5
    assert status["deduplicated_daily_observations"] == 5
    assert status["target"] == 50
    assert status["target_completed_pct"] == 10.0
    assert status["remaining_dates_needed"] == 45
    assert status["readiness_status"] == "COLLECTING"
    assert status["first_valid_market_date"] == "2026-11-01"
    assert status["latest_valid_market_date"] == "2026-11-05"


def test_readiness_status_ready_at_50():
    mock_db = MagicMock()
    base_time = datetime(2026, 9, 1, 4, 30, tzinfo=timezone.utc)
    
    # 50 distinct daily observations
    mock_rows = []
    for day in range(50):
        obs = MagicMock()
        obs.source_published_at = base_time + timedelta(days=day)
        mock_rows.append(obs)

    mock_db.execute.return_value.scalars.return_value.all.return_value = mock_rows
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    status = get_ml_dataset_readiness_status(db=mock_db, include_csv=False)

    assert status["valid_daily_snapshots_collected"] == 50
    assert status["deduplicated_daily_observations"] == 50
    assert status["target"] == 50
    assert status["target_completed_pct"] == 100.0
    assert status["remaining_dates_needed"] == 0
    assert status["readiness_status"] == "READY_FOR_EXPERIMENTAL_TRAINING"


def test_readiness_status_reconciled_with_csv(tmp_path: Path):
    # Test reconciliation logic: CSV with 3 business days (Mon-Wed), DB with 2 dates (1 overlapping) -> 4 distinct dates
    test_csv = tmp_path / "test_rates.csv"
    test_csv.write_text(
        "observed_at,published_time,gold_mjdta,silver_mjdta,source\n"
        "2026-11-02T10:00:00+05:30,Time : 09:30 am,13000,200,KJPL\n"
        "2026-11-03T10:00:00+05:30,Time : 09:30 am,13100,200,KJPL\n"
        "2026-11-04T10:00:00+05:30,Time : 09:30 am,13200,200,KJPL\n"
    )

    mock_db = MagicMock()
    # DB has 2026-11-04 (overlap) and 2026-11-05 (new)
    obs1 = MagicMock()
    obs1.source_published_at = datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)
    obs2 = MagicMock()
    obs2.source_published_at = datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc)
    mock_db.execute.return_value.scalars.return_value.all.return_value = [obs1, obs2]
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    status = get_ml_dataset_readiness_status(db=mock_db, include_csv=True, csv_path=test_csv)

    assert status["csv_rows_discovered"] == 3
    assert status["valid_csv_observations"] == 3
    assert status["distinct_csv_market_dates"] == 3
    assert status["neon_observations"] == 2
    assert status["overlapping_dates"] == 1
    assert status["deduplicated_daily_observations"] == 4
    assert status["target"] == 50
    assert status["target_completed_pct"] == 8.0
    assert status["remaining_dates_needed"] == 46
    assert status["first_valid_market_date"] == "2026-11-02"
    assert status["latest_valid_market_date"] == "2026-11-05"
    assert status["readiness_status"] == "COLLECTING"
