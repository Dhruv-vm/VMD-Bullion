from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.db.session import get_db
from app.main import app
from app.models.research import Base, NewsArticleRecord, NewsCluster, NewsLabelRecord
import app.services.sentiment.news_service as news_service
from app.services.sentiment.news_service import (
    get_latest_news_db,
    get_live_news_signal_db,
    get_news_archive_db,
    get_news_summary_db,
)


@pytest.fixture
def test_db(tmp_path: Path):
    """Provides an isolated SQLite database session created with Base.metadata."""
    db_file = tmp_path / "test_news_service.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def test_client(test_db):
    """TestClient with get_db dependency overridden to the test_db."""
    def _override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_latest_news_db_empty(test_db):
    """When DB is empty and no CSV is populated, returns empty list without raising."""
    res = get_latest_news_db(db=test_db, limit=10)
    assert isinstance(res, list)


def test_latest_news_db_populated(test_db):
    """Verifies that get_latest_news_db correctly joins articles, clusters, and latest labels."""
    now = datetime.now(timezone.utc)

    # 1. Create a cluster
    cluster = NewsCluster(
        story_cluster_id="clu_test_001",
        cluster_key="fed_rate_decision_2026",
        cluster_version="v2",
        representative_title="Federal Reserve Signals Rate Cuts as Inflation Cools",
        article_count=2,
        first_seen_at=now - timedelta(hours=4),
        latest_published_at=now - timedelta(hours=1),
    )
    test_db.add(cluster)

    # 2. Create two articles linked to the cluster
    art1 = NewsArticleRecord(
        canonical_url="https://reuters.com/markets/gold-rates-fed-1",
        url="https://reuters.com/markets/gold-rates-fed-1?ref=rss",
        title="Federal Reserve Signals Rate Cuts as Inflation Cools",
        source="Reuters",
        language="en",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(hours=1),
        content_fingerprint="fp_art_1",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_test_001",
    )
    art2 = NewsArticleRecord(
        canonical_url="https://bloomberg.com/news/gold-rally-fed-cut",
        url="https://bloomberg.com/news/gold-rally-fed-cut",
        title="Gold Rallies on Fed Dovish Shift",
        source="Bloomberg",
        language="en",
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(hours=2),
        content_fingerprint="fp_art_2",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_test_001",
    )
    test_db.add_all([art1, art2])
    test_db.flush()

    # 3. Create two versioned labels for the cluster; newest (30m ago) must win over older (2h ago)
    lbl_old = NewsLabelRecord(
        story_cluster_id="clu_test_001",
        article_id=art1.id,
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(hours=2),
        available_at=now - timedelta(hours=2),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="NEUTRAL",
        gold_impact="NEUTRAL",
        driver="FED_POLICY",
        surprise="NO_SURPRISE",
        is_price_report=False,
        confidence="LOW",
        evidence_rationale="Old classification",
    )
    lbl_new = NewsLabelRecord(
        story_cluster_id="clu_test_001",
        article_id=art1.id,
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(minutes=30),
        available_at=now - timedelta(minutes=30),
        sentiment_version="v2",
        model_name="deterministic_rules_v2",
        general_tone="POSITIVE",
        gold_impact="BULLISH",
        driver="FED_POLICY",
        surprise="DOVISH_PIVOT",
        is_price_report=False,
        confidence="HIGH",
        evidence_rationale="Fed rate cut dovish shift directly supports bullion upside.",
    )
    test_db.add_all([lbl_old, lbl_new])
    test_db.commit()

    # Query latest news
    res = get_latest_news_db(db=test_db, limit=10)
    assert len(res) == 2

    # Verify art1 is first (published 1h ago vs 2h ago)
    top = res[0]
    assert top["title"] == "Federal Reserve Signals Rate Cuts as Inflation Cools"
    assert top["source"] == "Reuters"
    assert top["gold_impact"] == "BULLISH"
    assert top["sentiment_label"] == "bullish"
    assert top["sentiment_score"] == 1.0
    assert top["driver"] == "FED_POLICY"
    assert top["finbert_driver"] == "FED_POLICY"
    assert top["confidence"] == "HIGH"
    assert top["evidence_rationale"] == "Fed rate cut dovish shift directly supports bullion upside."
    assert top["is_gold_relevant"] is True
    assert top["is_price_report"] is False
    assert top["syndicated_copies_count"] == 2
    assert "ago" in top["age_display"]


def test_latest_news_db_window_hours_filtering(test_db):
    """Verifies that articles older than window_hours are excluded."""
    now = datetime.now(timezone.utc)

    art_recent = NewsArticleRecord(
        canonical_url="https://source.com/recent-1",
        url="https://source.com/recent-1",
        title="Recent Gold News",
        source="TestWire",
        published_at=now - timedelta(hours=3),
        first_seen_at=now - timedelta(hours=3),
        content_fingerprint="fp_rec",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
    )
    art_old = NewsArticleRecord(
        canonical_url="https://source.com/old-1",
        url="https://source.com/old-1",
        title="Old Gold News from Last Week",
        source="TestWire",
        published_at=now - timedelta(hours=72),
        first_seen_at=now - timedelta(hours=72),
        content_fingerprint="fp_old",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
    )
    test_db.add_all([art_recent, art_old])
    test_db.commit()

    # Query with 24h window
    res_24 = get_latest_news_db(db=test_db, limit=10, window_hours=24)
    assert len(res_24) == 1
    assert res_24[0]["title"] == "Recent Gold News"

    # Query with 96h window
    res_96 = get_latest_news_db(db=test_db, limit=10, window_hours=96)
    assert len(res_96) == 2


def test_future_dated_articles_excluded(test_db):
    """Verifies that future-dated articles are strictly excluded from latest and live signal."""
    now = datetime.now(timezone.utc)

    art_future = NewsArticleRecord(
        canonical_url="https://source.com/future-1",
        url="https://source.com/future-1",
        title="Future Leak Article",
        source="FutureWire",
        published_at=now + timedelta(hours=2),
        first_seen_at=now,
        content_fingerprint="fp_fut",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
    )
    art_valid = NewsArticleRecord(
        canonical_url="https://source.com/valid-1",
        url="https://source.com/valid-1",
        title="Valid Contemporary Article",
        source="ValidWire",
        published_at=now - timedelta(minutes=10),
        first_seen_at=now - timedelta(minutes=10),
        content_fingerprint="fp_val",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
    )
    test_db.add_all([art_future, art_valid])
    test_db.commit()

    latest = get_latest_news_db(db=test_db, limit=10)
    assert len(latest) == 1
    assert latest[0]["title"] == "Valid Contemporary Article"


def test_unknown_classifications_handled_strictly(test_db):
    """Verifies that UNKNOWN classifications are never treated as bullish or neutral."""
    now = datetime.now(timezone.utc)

    cluster = NewsCluster(
        story_cluster_id="clu_unknown",
        cluster_key="key_unknown",
        cluster_version="v2",
        representative_title="Vague Economic Commentary",
        article_count=1,
        latest_published_at=now - timedelta(hours=1),
    )
    art = NewsArticleRecord(
        canonical_url="https://source.com/unknown-1",
        url="https://source.com/unknown-1",
        title="Vague Economic Commentary",
        source="TestWire",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(hours=1),
        content_fingerprint="fp_unk",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_unknown",
    )
    lbl = NewsLabelRecord(
        story_cluster_id="clu_unknown",
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(hours=1),
        available_at=now - timedelta(hours=1),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="UNKNOWN",
        gold_impact="UNKNOWN",
        driver="UNKNOWN",
        is_price_report=False,
        confidence="LOW",
    )
    test_db.add_all([cluster, art, lbl])
    test_db.commit()

    latest = get_latest_news_db(db=test_db, limit=10)
    assert len(latest) == 1
    item = latest[0]
    assert item["gold_impact"] == "UNKNOWN"
    assert item["sentiment_label"] == "unknown"  # Strictly 'unknown', never 'neutral'
    assert item["sentiment_score"] == 0.0
    assert item["is_gold_relevant"] is False
    assert item["directional_implication"] == "UNCERTAIN"


def test_live_news_signal_db_insufficient(test_db):
    """Returns INSUFFICIENT_FRESH_RELEVANT_NEWS when fewer than 2 distinct relevant clusters exist."""
    now = datetime.now(timezone.utc)

    cluster = NewsCluster(
        story_cluster_id="clu_single",
        cluster_key="single_key",
        cluster_version="v2",
        representative_title="Single Story",
        article_count=1,
        first_seen_at=now - timedelta(hours=2),
        latest_published_at=now - timedelta(hours=1),
    )
    art = NewsArticleRecord(
        canonical_url="https://single.com/1",
        url="https://single.com/1",
        title="Single Story",
        source="SingleWire",
        published_at=now - timedelta(hours=1),
        first_seen_at=now - timedelta(hours=1),
        content_fingerprint="fp_single",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_single",
    )
    lbl = NewsLabelRecord(
        story_cluster_id="clu_single",
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(hours=1),
        available_at=now - timedelta(hours=1),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="POSITIVE",
        gold_impact="BULLISH",
        driver="FED_POLICY",
        confidence="HIGH",
    )
    test_db.add_all([cluster, art, lbl])
    test_db.commit()

    sig = get_live_news_signal_db(db=test_db, window_hours=24)
    assert sig["status"] == "INSUFFICIENT_FRESH_RELEVANT_NEWS"
    assert sig["is_sufficient"] is False
    assert sig["distinct_clusters_count"] == 1
    assert sig["net_signal"] == "UNCERTAIN"


def test_live_news_signal_db_active(test_db):
    """Returns ACTIVE consensus when at least 2 distinct relevant clusters are present."""
    now = datetime.now(timezone.utc)

    # 1. Cluster A: Bullish Fed policy
    cA = NewsCluster(
        story_cluster_id="clu_A",
        cluster_key="key_fed",
        cluster_version="v2",
        representative_title="Fed Announces Rate Cut",
        article_count=1,
        first_seen_at=now - timedelta(hours=3),
        latest_published_at=now - timedelta(hours=2),
    )
    artA = NewsArticleRecord(
        canonical_url="https://source.com/fed",
        url="https://source.com/fed",
        title="Fed Announces Rate Cut",
        source="Reuters",
        published_at=now - timedelta(hours=2),
        first_seen_at=now - timedelta(hours=2),
        content_fingerprint="fp_A",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_A",
    )
    lblA = NewsLabelRecord(
        story_cluster_id="clu_A",
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(hours=2),
        available_at=now - timedelta(hours=2),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="POSITIVE",
        gold_impact="BULLISH",
        driver="FED_POLICY",
        confidence="HIGH",
    )

    # 2. Cluster B: Bullish Geopolitics
    cB = NewsCluster(
        story_cluster_id="clu_B",
        cluster_key="key_geo",
        cluster_version="v2",
        representative_title="Geopolitical Escalation Stirs Safe Haven Demand",
        article_count=1,
        first_seen_at=now - timedelta(hours=4),
        latest_published_at=now - timedelta(hours=3),
    )
    artB = NewsArticleRecord(
        canonical_url="https://source.com/geo",
        url="https://source.com/geo",
        title="Geopolitical Escalation Stirs Safe Haven Demand",
        source="Bloomberg",
        published_at=now - timedelta(hours=3),
        first_seen_at=now - timedelta(hours=3),
        content_fingerprint="fp_B",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_B",
    )
    lblB = NewsLabelRecord(
        story_cluster_id="clu_B",
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(hours=3),
        available_at=now - timedelta(hours=3),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="POSITIVE",
        gold_impact="BULLISH",
        driver="GEOPOLITICS",
        confidence="HIGH",
    )

    test_db.add_all([cA, cB, artA, artB, lblA, lblB])
    test_db.commit()

    sig = get_live_news_signal_db(db=test_db, window_hours=24)
    assert sig["status"] == "ACTIVE"
    assert sig["is_sufficient"] is True
    assert sig["distinct_clusters_count"] == 2
    assert sig["bullish_weight"] > 0
    assert sig["net_signal"] == "BULLISH"
    assert sig["overall_signal"] == "BULLISH"
    assert "representative_articles" in sig
    assert len(sig["representative_articles"]) == 2


def test_news_summary_and_archive_db(test_db):
    """Verifies get_news_summary_db and get_news_archive_db."""
    now = datetime.now(timezone.utc)

    # Add 3 articles: 1 bullish, 1 bearish, 1 neutral price wrap
    for i, (imp, drv, is_pr) in enumerate([
        ("BULLISH", "FED_POLICY", False),
        ("BEARISH", "USD", False),
        ("NEUTRAL", "GOLD_PRICE_REPORT", True),
    ]):
        cid = f"clu_sum_{i}"
        cluster = NewsCluster(
            story_cluster_id=cid,
            cluster_key=f"key_{i}",
            cluster_version="v2",
            representative_title=f"Article {i}",
            first_seen_at=now - timedelta(hours=i + 2),
            latest_published_at=now - timedelta(hours=i + 1),
        )
        art = NewsArticleRecord(
            canonical_url=f"https://source.com/art-{i}",
            url=f"https://source.com/art-{i}",
            title=f"Article {i}",
            source="TestWire",
            published_at=now - timedelta(hours=i + 1),
            first_seen_at=now - timedelta(hours=i + 1),
            content_fingerprint=f"fp_{i}",
            query_version="v1",
            collector_version="v1",
            provenance_status="VERIFIED_SOURCE",
            story_cluster_id=cid,
        )
        lbl = NewsLabelRecord(
            story_cluster_id=cid,
            classification_scope="CLUSTER",
            label_created_at=now - timedelta(hours=i + 1),
            available_at=now - timedelta(hours=i + 1),
            sentiment_version="v1",
            model_name="deterministic_rules_v2",
            general_tone="NEUTRAL" if is_pr else ("POSITIVE" if imp == "BULLISH" else "NEGATIVE"),
            gold_impact=imp,
            driver=drv,
            is_price_report=is_pr,
            confidence="MEDIUM",
        )
        test_db.add_all([cluster, art, lbl])
    test_db.commit()

    # 1. Summary
    summary = get_news_summary_db(db=test_db, limit=10)
    assert summary["total_analyzed"] == 3
    assert summary["bullish_count"] == 1
    assert summary["bearish_count"] == 1
    assert summary["gold_relevant_count"] == 2  # price wrap excluded
    assert summary["positive_ratio"] == round(1 / 3, 2)

    # 2. Archive
    archive_all = get_news_archive_db(db=test_db, limit=10, offset=0)
    assert archive_all["total_archive_records"] == 3
    assert archive_all["returned"] == 3

    archive_rel = get_news_archive_db(db=test_db, limit=10, offset=0, is_gold_relevant=True)
    assert archive_rel["returned"] == 2


# ==============================================================================
# CSV FALLBACK ISOLATION & FAILURE TESTS
# ==============================================================================

def test_production_empty_database_returns_empty_response(monkeypatch, test_db, tmp_path):
    """
    Requirement 1: Production database returns zero articles -> empty response,
    NEVER falling back to local CSV even if CSV files exist.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")

    # Create dummy CSV that would be read if fallback occurred
    dummy_csv = tmp_path / "live_news.csv"
    with dummy_csv.open("w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url", "source", "published_at", "directional_implication"])
        writer.writerow(["Fake News from CSV", "https://fake.com", "FakeWire", "2026-11-26T10:00:00Z", "BULLISH"])
    monkeypatch.setattr(news_service, "LIVE_NEWS_CSV", dummy_csv)

    # Database is empty
    latest = get_latest_news_db(db=test_db, limit=10)
    assert latest == [], "Must return empty list in production, not CSV records"

    sig = get_live_news_signal_db(db=test_db, window_hours=24)
    assert sig["status"] == "INSUFFICIENT_FRESH_RELEVANT_NEWS"
    assert sig["total_fresh_scanned"] == 0
    assert sig["distinct_clusters_count"] == 0

    arch = get_news_archive_db(db=test_db, limit=10, offset=0)
    assert arch["total_archive_records"] == 0
    assert arch["returned"] == 0
    assert arch["articles"] == []


def test_production_database_query_fails_raises_controlled_error(monkeypatch, tmp_path):
    """
    Requirement 2: Production database query failure raises controlled RuntimeError,
    NEVER falling back to CSV.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")

    # Create dummy CSV
    dummy_csv = tmp_path / "live_news.csv"
    with dummy_csv.open("w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url", "source", "published_at"])
        writer.writerow(["Fake News", "https://fake.com", "FakeWire", "2026-11-26T10:00:00Z"])
    monkeypatch.setattr(news_service, "LIVE_NEWS_CSV", dummy_csv)

    broken_db = MagicMock()
    broken_db.execute.side_effect = RuntimeError("Neon PostgreSQL connection reset by peer")
    broken_db.scalar.side_effect = RuntimeError("Neon PostgreSQL connection reset by peer")

    with pytest.raises(RuntimeError, match="Database query failed in production/PostgreSQL mode"):
        get_latest_news_db(db=broken_db, limit=10)

    with pytest.raises(RuntimeError, match="Database query failed in production/PostgreSQL mode"):
        get_live_news_signal_db(db=broken_db, window_hours=24)

    with pytest.raises(RuntimeError, match="Database query failed in production/PostgreSQL mode"):
        get_news_archive_db(db=broken_db, limit=10)


def test_local_development_fallback_permitted_when_dev_mode(monkeypatch, test_db, tmp_path):
    """
    Requirement 3: Local development fallback works ONLY when explicitly permitted
    (ENVIRONMENT != production and not is_postgres_configured).
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    # Create dummy CSV with 1 valid row
    dummy_csv = tmp_path / "live_news.csv"
    with dummy_csv.open("w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url", "source", "published_at", "directional_implication"])
        writer.writerow(["Dev Fallback Story", "https://dev.com", "DevWire", datetime.now(timezone.utc).isoformat(), "BULLISH"])
    monkeypatch.setattr(news_service, "LIVE_NEWS_CSV", dummy_csv)

    # Empty DB in dev mode falls back to CSV
    res = get_latest_news_db(db=test_db, limit=10)
    assert len(res) == 1
    assert res[0]["title"] == "Dev Fallback Story"


def test_postgres_configured_disallows_csv_fallback_even_in_dev(monkeypatch, test_db, tmp_path):
    """
    Requirement 4: When PostgreSQL is configured (DATABASE_URL is postgres),
    CSV fallback is strictly prohibited even if ENVIRONMENT is set to 'development'.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@ep-fake.neon.tech/neondb")

    dummy_csv = tmp_path / "live_news.csv"
    with dummy_csv.open("w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "url", "source", "published_at"])
        writer.writerow(["Dev Fallback Story", "https://dev.com", "DevWire", datetime.now(timezone.utc).isoformat()])
    monkeypatch.setattr(news_service, "LIVE_NEWS_CSV", dummy_csv)

    # With Postgres configured, empty database returns [] and does NOT read CSV
    res = get_latest_news_db(db=test_db, limit=10)
    assert res == []


def test_api_news_routes_error_handling(test_client, monkeypatch):
    """Verifies that FastAPI routes catch RuntimeError in production and return HTTP 503."""
    monkeypatch.setenv("ENVIRONMENT", "production")

    broken_db = MagicMock()
    broken_db.execute.side_effect = RuntimeError("Neon down")
    broken_db.scalar.side_effect = RuntimeError("Neon down")

    def _override_get_db():
        yield broken_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        res = test_client.get("/api/news/latest")
        assert res.status_code == 503
        assert "Database query failed" in res.json()["detail"]

        res_sig = test_client.get("/api/news/live-signal")
        assert res_sig.status_code == 503

        res_arch = test_client.get("/api/news/archive")
        assert res_arch.status_code == 503

        res_sum = test_client.get("/api/news/summary")
        assert res_sum.status_code == 503
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_api_news_routes_integration(test_client, test_db):
    """End-to-end FastAPI route tests for /api/news endpoints."""
    now = datetime.now(timezone.utc)

    # Seed one test article with cluster and label
    cluster = NewsCluster(
        story_cluster_id="clu_api_test",
        cluster_key="api_key_1",
        cluster_version="v2",
        representative_title="API Integration Test Gold Headline",
        first_seen_at=now - timedelta(hours=1),
        latest_published_at=now - timedelta(minutes=15),
    )
    art = NewsArticleRecord(
        canonical_url="https://test.com/api-news-1",
        url="https://test.com/api-news-1",
        title="API Integration Test Gold Headline",
        source="TestAPI",
        published_at=now - timedelta(minutes=15),
        first_seen_at=now - timedelta(minutes=15),
        content_fingerprint="fp_api_1",
        query_version="v1",
        collector_version="v1",
        provenance_status="VERIFIED_SOURCE",
        story_cluster_id="clu_api_test",
    )
    lbl = NewsLabelRecord(
        story_cluster_id="clu_api_test",
        classification_scope="CLUSTER",
        label_created_at=now - timedelta(minutes=15),
        available_at=now - timedelta(minutes=15),
        sentiment_version="v1",
        model_name="deterministic_rules_v2",
        general_tone="POSITIVE",
        gold_impact="BULLISH",
        driver="FED_POLICY",
        is_price_report=False,
        confidence="HIGH",
        evidence_rationale="API test evidence.",
    )
    test_db.add_all([cluster, art, lbl])
    test_db.commit()

    # 1. GET /api/news/latest
    res_latest = test_client.get("/api/news/latest?limit=10")
    assert res_latest.status_code == 200
    data = res_latest.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["title"] == "API Integration Test Gold Headline"
    assert data[0]["gold_impact"] == "BULLISH"
    assert data[0]["finbert_driver"] == "FED_POLICY"

    # 2. GET /api/news/live-signal
    res_signal = test_client.get("/api/news/live-signal?window_hours=24")
    assert res_signal.status_code == 200
    sig_data = res_signal.json()
    assert "status" in sig_data
    assert "net_signal" in sig_data

    # 3. GET /api/news/summary
    res_summary = test_client.get("/api/news/summary")
    assert res_summary.status_code == 200
    sum_data = res_summary.json()
    assert "total_analyzed" in sum_data
    assert sum_data["total_analyzed"] >= 1

    # 4. GET /api/news/archive
    res_archive = test_client.get("/api/news/archive?limit=10&offset=0")
    assert res_archive.status_code == 200
    arch_data = res_archive.json()
    assert "total_archive_records" in arch_data
    assert arch_data["total_archive_records"] >= 1
