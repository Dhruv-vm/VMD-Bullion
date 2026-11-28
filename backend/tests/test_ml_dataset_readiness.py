from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock
import pytest

from app.services.prediction.forecast_engine import get_ml_dataset_readiness_status, TARGET_DAILY_OBSERVATIONS


def test_target_constant_is_50():
    assert TARGET_DAILY_OBSERVATIONS == 50


def test_readiness_status_empty_db():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = []
    mock_db.execute.return_value.scalar.return_value = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    status = get_ml_dataset_readiness_status(db=mock_db)

    assert status["valid_daily_snapshots_collected"] == 0
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

    status = get_ml_dataset_readiness_status(db=mock_db)

    assert status["valid_daily_snapshots_collected"] == 5
    assert status["target"] == 50
    assert status["target_completed_pct"] == 10.0
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

    status = get_ml_dataset_readiness_status(db=mock_db)

    assert status["valid_daily_snapshots_collected"] == 50
    assert status["target"] == 50
    assert status["target_completed_pct"] == 100.0
    assert status["readiness_status"] == "READY_FOR_INITIAL_TRAINING"
