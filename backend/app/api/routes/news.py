from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.sentiment.news_service import (
    get_latest_news_db,
    get_live_news_signal_db,
    get_news_archive_db,
    get_news_summary_db,
)

router = APIRouter(prefix="/api/news", tags=["News"])


@router.get("/latest")
def get_latest_news(
    limit: int = 30,
    window_hours: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """
    Returns latest financial and bullion news with gold relevance, sentiment indicators,
    and bullion directional impact.
    Primary source: PostgreSQL via SQLAlchemy (news_articles + news_clusters + news_labels).
    Fails closed with HTTP 503 if database access fails in production/PostgreSQL mode.
    """
    try:
        return get_latest_news_db(
            db=db,
            limit=limit,
            window_hours=window_hours,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/live-signal")
def get_live_news_signal(
    window_hours: int = 24,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns real-time bullion news signal intelligence prioritized by freshness.
    Includes syndicated cluster deduplication, macro driver attribution, directional consensus,
    and market vs. news alignment.
    Primary source: PostgreSQL via SQLAlchemy.
    Fails closed with HTTP 503 if database access fails in production/PostgreSQL mode.
    """
    try:
        return get_live_news_signal_db(
            db=db,
            window_hours=window_hours,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/archive")
def get_news_archive(
    limit: int = 50,
    offset: int = 0,
    is_gold_relevant: bool | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns historical news articles from the research archive (isolated from live signal).
    Useful for ML researchers and point-in-time backtesting verification.
    Primary source: PostgreSQL via SQLAlchemy (news_articles + news_labels).
    Fails closed with HTTP 503 if database access fails in production/PostgreSQL mode.
    """
    try:
        return get_news_archive_db(
            db=db,
            limit=limit,
            offset=offset,
            is_gold_relevant=is_gold_relevant,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/summary")
def get_news_summary(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Aggregated news intelligence metrics separating gold-specific impact from generic tone.
    Primary source: Aggregated from latest PostgreSQL records via get_latest_news_db.
    Fails closed with HTTP 503 if database access fails in production/PostgreSQL mode.
    """
    try:
        return get_news_summary_db(
            db=db,
            limit=50,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
