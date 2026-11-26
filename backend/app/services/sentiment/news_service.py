from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.session import ENVIRONMENT, is_postgres_configured
from app.models.research import NewsArticleRecord, NewsCluster, NewsLabelRecord
from app.services.data_service import (
    ANALYZED_NEWS_CSV,
    LIVE_NEWS_CSV,
    NEWS_ARCHIVE_CSV,
    NEWS_CSV,
    get_market_overview,
)
from app.services.sentiment.gold_impact import GoldDriver, evaluate_gold_impact
from app.services.sentiment.live_signal import (
    CONFIDENCE_WEIGHTS,
    get_live_news_intelligence,
    parse_utc_timestamp,
)
from app.services.sentiment.relevance import GoldRelevanceScorer

_scorer = GoldRelevanceScorer()


def _is_csv_fallback_allowed() -> bool:
    """
    CSV fallback is strictly prohibited in production and whenever PostgreSQL is configured.
    Permitted only in non-production local development or offline test environments without PostgreSQL.
    """
    current_env = (os.environ.get("ENVIRONMENT") or ENVIRONMENT or "development").lower()
    if current_env == "production" or is_postgres_configured():
        return False
    return True


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure a datetime object is timezone-aware in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _latest_label_subquery():
    """
    Builds a deterministic subquery selecting strictly the latest label per story_cluster_id.
    Ties in label_created_at are deterministically broken by id DESC.
    """
    return (
        select(
            NewsLabelRecord.id.label("lbl_id"),
            NewsLabelRecord.story_cluster_id.label("lbl_cluster_id"),
            NewsLabelRecord.general_tone,
            NewsLabelRecord.gold_impact,
            NewsLabelRecord.driver,
            NewsLabelRecord.surprise,
            NewsLabelRecord.is_price_report,
            NewsLabelRecord.confidence,
            NewsLabelRecord.evidence_rationale,
            NewsLabelRecord.label_created_at,
            func.row_number()
            .over(
                partition_by=NewsLabelRecord.story_cluster_id,
                order_by=(NewsLabelRecord.label_created_at.desc(), NewsLabelRecord.id.desc()),
            )
            .label("rn"),
        )
    ).subquery()


def get_latest_news_db(
    db: Session | None = None,
    limit: int = 30,
    window_hours: float | None = None,
) -> list[dict[str, Any]]:
    """
    Returns latest financial and bullion news with gold relevance, sentiment indicators,
    and bullion directional impact.
    Primary source: PostgreSQL via SQLAlchemy (NewsArticleRecord + NewsCluster + NewsLabelRecord).
    Fallback: Local CSV files ONLY in non-production local development without PostgreSQL.
    """
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except Exception:
            limit = 30
    limit = max(1, min(limit, 100))

    if window_hours is not None and not isinstance(window_hours, (int, float)):
        try:
            window_hours = float(window_hours)
        except Exception:
            window_hours = None

    now = datetime.now(timezone.utc)

    # 1. Primary: PostgreSQL / SQLite database via SQLAlchemy session
    if db is not None:
        try:
            subq = _latest_label_subquery()
            stmt = (
                select(
                    NewsArticleRecord,
                    NewsCluster.article_count,
                    subq.c.general_tone,
                    subq.c.gold_impact,
                    subq.c.driver,
                    subq.c.surprise,
                    subq.c.is_price_report,
                    subq.c.confidence,
                    subq.c.evidence_rationale,
                )
                .outerjoin(
                    NewsCluster,
                    NewsArticleRecord.story_cluster_id == NewsCluster.story_cluster_id,
                )
                .outerjoin(
                    subq,
                    and_(
                        NewsArticleRecord.story_cluster_id == subq.c.lbl_cluster_id,
                        subq.c.rn == 1,
                    ),
                )
            )

            # Publication window and future-dating guard
            stmt = stmt.where(NewsArticleRecord.published_at <= now)
            if window_hours is not None:
                cutoff = now - timedelta(hours=window_hours)
                stmt = stmt.where(NewsArticleRecord.published_at >= cutoff)

            stmt = stmt.order_by(
                NewsArticleRecord.published_at.desc(),
                NewsArticleRecord.id.desc(),
            ).limit(limit)

            rows = db.execute(stmt).all()

            if rows:
                articles: list[dict[str, Any]] = []
                for row in rows:
                    (
                        art,
                        article_count,
                        general_tone,
                        gold_impact,
                        driver,
                        surprise,
                        is_price_report,
                        confidence,
                        evidence_rationale,
                    ) = row

                    pub_dt = _ensure_utc(art.published_at)
                    age_h = (now - pub_dt).total_seconds() / 3600.0 if pub_dt else None

                    g_impact = gold_impact or "UNKNOWN"
                    g_driver = driver or "UNKNOWN"
                    is_pr = bool(is_price_report) if is_price_report is not None else False
                    conf = confidence or "LOW"

                    # Sentiment label & score: UNKNOWN is never treated as bullish or neutral
                    if g_impact in ("BULLISH", "BEARISH", "NEUTRAL", "MIXED"):
                        sentiment_label = g_impact.lower()
                    else:
                        sentiment_label = "unknown"

                    if g_impact == "BULLISH":
                        sentiment_score = 1.0
                    elif g_impact == "BEARISH":
                        sentiment_score = -1.0
                    else:
                        sentiment_score = 0.0

                    is_relevant = bool(
                        not is_pr
                        and g_driver != "UNKNOWN"
                        and g_impact not in ("UNKNOWN", "NEUTRAL")
                    )

                    finbert_driver = g_driver if g_driver != "UNKNOWN" else "MARKET_MOMENTUM"
                    directional_implication = g_impact if g_impact != "UNKNOWN" else "UNCERTAIN"

                    articles.append({
                        "id": str(art.id),
                        "title": art.title,
                        "url": art.url,
                        "canonical_url": art.canonical_url,
                        "source": art.source,
                        "published_at": pub_dt.isoformat() if pub_dt else "",
                        "general_tone": general_tone or "NEUTRAL",
                        "gold_impact": g_impact,
                        "driver": g_driver,
                        "finbert_driver": finbert_driver,
                        "surprise": surprise or "UNKNOWN",
                        "is_price_report": is_pr,
                        "confidence": conf,
                        "evidence_rationale": evidence_rationale or "",
                        "summary": evidence_rationale or None,
                        "sentiment_label": sentiment_label,
                        "sentiment_score": sentiment_score,
                        "is_gold_relevant": is_relevant,
                        "primary_category": "GOLD_DIRECT",
                        "driver_category": finbert_driver,
                        "directional_implication": directional_implication,
                        "age_display": f"{round(age_h, 1)}h ago" if age_h is not None else "recent",
                        "syndicated_copies_count": int(article_count) if article_count else 1,
                    })

                return articles

            # Legitimate empty database response in production/PostgreSQL mode
            if not _is_csv_fallback_allowed():
                return []
        except Exception as exc:
            if not _is_csv_fallback_allowed():
                raise RuntimeError(f"Database query failed in production/PostgreSQL mode: {exc}") from exc
    else:
        if not _is_csv_fallback_allowed():
            raise RuntimeError("Database session is required in production/PostgreSQL mode")

    # 2. Fallback: Dedicated fresh LIVE_NEWS_CSV (only if explicitly allowed in local dev)
    if LIVE_NEWS_CSV.exists():
        try:
            articles = []
            with LIVE_NEWS_CSV.open("r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                for r in reader:
                    pub_dt = parse_utc_timestamp(r.get("published_at", ""))
                    if window_hours is not None and pub_dt:
                        age_h = (now - pub_dt).total_seconds() / 3600.0
                        if age_h > window_hours:
                            continue

                    articles.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "source": r.get("source", ""),
                        "published_at": r.get("published_at", ""),
                        "sentiment_label": r.get("directional_implication", "neutral").lower(),
                        "sentiment_score": 0.0,
                        "is_gold_relevant": r.get("is_gold_relevant", "True").lower() in ("true", "1"),
                        "primary_category": r.get("primary_category", "GOLD_DIRECT"),
                        "driver_category": r.get("driver_category", "MARKET_MOMENTUM"),
                        "directional_implication": r.get("directional_implication", "UNCERTAIN"),
                        "confidence": r.get("confidence", "MEDIUM"),
                        "evidence_rationale": r.get("evidence_rationale", ""),
                        "age_display": f"{round((now - pub_dt).total_seconds() / 3600.0, 1)}h ago" if pub_dt else "recent",
                        "syndicated_copies_count": int(r.get("syndicated_copies_count", 1) or 1),
                    })

                    if len(articles) >= limit:
                        break
            if articles:
                return articles
        except Exception:
            pass

    # 3. Fallback: Analyzed news cache
    if ANALYZED_NEWS_CSV.exists():
        try:
            articles = []
            with ANALYZED_NEWS_CSV.open("r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                for r in reversed(reader):
                    pub_dt = parse_utc_timestamp(r.get("published_at", ""))
                    if window_hours is not None and pub_dt:
                        age_h = (now - pub_dt).total_seconds() / 3600.0
                        if age_h > window_hours:
                            continue

                    title = r.get("title", "")
                    sentiment_label = r.get("sentiment_label", "neutral")
                    sentiment_score = float(r.get("sentiment_score", 0.0) or 0.0)
                    is_gold_relevant = r.get("is_gold_relevant", "True") == "True"

                    impact = evaluate_gold_impact(
                        title=title,
                        published_at=pub_dt or now,
                        finbert_label=sentiment_label,
                        finbert_score=sentiment_score,
                        reference_time=now,
                    )

                    articles.append({
                        "title": title,
                        "url": r.get("url", ""),
                        "source": r.get("source", ""),
                        "published_at": r.get("published_at", ""),
                        "sentiment_label": sentiment_label,
                        "sentiment_score": sentiment_score,
                        "is_gold_relevant": is_gold_relevant,
                        "primary_category": r.get("primary_category", "GOLD_DIRECT"),
                        "driver_category": impact["driver_category"],
                        "directional_implication": impact["directional_implication"],
                        "confidence": impact["confidence"],
                        "evidence_rationale": impact["evidence_rationale"],
                        "age_display": impact["age_display"],
                    })

                    if len(articles) >= limit:
                        break
            if articles:
                return articles
        except Exception:
            pass

    # 4. Fallback: Raw news CSV
    if NEWS_CSV.exists():
        try:
            articles = []
            with NEWS_CSV.open("r", encoding="utf-8") as f:
                reader = list(csv.DictReader(f))
                for r in reversed(reader):
                    pub_dt = parse_utc_timestamp(r.get("published_at", ""))
                    if window_hours is not None and pub_dt:
                        age_h = (now - pub_dt).total_seconds() / 3600.0
                        if age_h > window_hours:
                            continue

                    title = r.get("title", "")
                    rel = _scorer.score_text(title)
                    impact = evaluate_gold_impact(
                        title=title,
                        published_at=pub_dt or now,
                        finbert_label="neutral",
                        finbert_score=0.0,
                        reference_time=now,
                    )

                    articles.append({
                        "title": title,
                        "url": r.get("url", ""),
                        "source": r.get("source", ""),
                        "published_at": r.get("published_at", ""),
                        "general_tone": impact["general_tone"],
                        "gold_impact": impact["gold_impact"],
                        "driver": impact["driver"],
                        "surprise": impact["surprise"],
                        "is_price_report": impact["is_price_report"],
                        "sentiment_label": impact["general_tone"].lower(),
                        "sentiment_score": 0.0,
                        "is_gold_relevant": rel.is_gold_relevant and not impact["is_price_report"],
                        "primary_category": rel.primary_category.value if hasattr(rel.primary_category, "value") else str(rel.primary_category),
                        "driver_category": impact["driver_category"],
                        "directional_implication": impact["directional_implication"],
                        "confidence": impact["confidence"],
                        "evidence_rationale": impact["evidence_rationale"],
                        "age_display": impact["age_display"],
                    })

                    if len(articles) >= limit:
                        break
            return articles
        except Exception:
            pass

    return []


def get_live_news_signal_db(
    db: Session | None = None,
    window_hours: int = 24,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Computes real-time, freshness-prioritized bullion news intelligence.
    Reads from PostgreSQL via SQLAlchemy if available; fallback allowed only in local dev.
    """
    now = reference_time or datetime.now(timezone.utc)
    now = _ensure_utc(now)
    window_hours = max(1, int(window_hours))
    cutoff_time = now - timedelta(hours=window_hours)

    # 1. Primary: PostgreSQL / SQLAlchemy
    if db is not None:
        try:
            subq = _latest_label_subquery()
            stmt = (
                select(
                    NewsArticleRecord,
                    NewsCluster.cluster_key,
                    NewsCluster.article_count,
                    subq.c.general_tone,
                    subq.c.gold_impact,
                    subq.c.driver,
                    subq.c.surprise,
                    subq.c.is_price_report,
                    subq.c.confidence,
                    subq.c.evidence_rationale,
                )
                .outerjoin(
                    NewsCluster,
                    NewsArticleRecord.story_cluster_id == NewsCluster.story_cluster_id,
                )
                .outerjoin(
                    subq,
                    and_(
                        NewsArticleRecord.story_cluster_id == subq.c.lbl_cluster_id,
                        subq.c.rn == 1,
                    ),
                )
                .where(
                    NewsArticleRecord.published_at >= cutoff_time,
                    NewsArticleRecord.published_at <= now,  # Future-dated articles excluded
                )
                .order_by(NewsArticleRecord.published_at.desc())
            )

            rows = db.execute(stmt).all()

            # If DB returned rows OR fallback is prohibited (production/PostgreSQL), process DB result
            if rows or not _is_csv_fallback_allowed():
                total_fresh_scanned = len(rows)

                # Group articles by story cluster key to deduplicate syndicated wire reprints
                clusters: dict[str, list[dict[str, Any]]] = {}
                relevant_evaluated: list[dict[str, Any]] = []

                for row in rows:
                    (
                        art,
                        cluster_key,
                        cluster_article_count,
                        general_tone,
                        gold_impact,
                        driver,
                        surprise,
                        is_price_report,
                        confidence,
                        evidence_rationale,
                    ) = row

                    pub_dt = _ensure_utc(art.published_at)
                    age_seconds = max(0.0, (now - pub_dt).total_seconds()) if pub_dt else 0.0
                    age_hours = age_seconds / 3600.0
                    freshness_weight = round(math.exp(-age_hours / 12.0), 3)

                    g_impact = gold_impact or "UNKNOWN"
                    g_driver = driver or "UNKNOWN"
                    is_pr = bool(is_price_report) if is_price_report is not None else False
                    conf = confidence or "LOW"

                    # Domain relevance: non-price-wrap gold driver or directional signal
                    is_rel = not is_pr and (
                        g_driver not in ("UNKNOWN", "OTHER_MACRO")
                        or g_impact in ("BULLISH", "BEARISH")
                    )

                    ckey = cluster_key or (art.story_cluster_id or f"art_{art.id}")

                    art_dict = {
                        "id": str(art.id),
                        "title": art.title,
                        "url": art.url,
                        "source": art.source,
                        "published_at": pub_dt.isoformat() if pub_dt else "",
                        "cluster_key": ckey,
                        "general_tone": general_tone or "NEUTRAL",
                        "gold_impact": g_impact,
                        "driver": g_driver,
                        "driver_category": g_driver if g_driver != "UNKNOWN" else "MARKET_MOMENTUM",
                        "directional_implication": g_impact if g_impact != "UNKNOWN" else "UNCERTAIN",
                        "confidence": conf,
                        "freshness_weight": freshness_weight,
                        "evidence_rationale": evidence_rationale or "",
                        "is_gold_relevant": is_rel,
                        "is_price_report": is_pr,
                    }

                    if is_rel:
                        relevant_evaluated.append(art_dict)
                        if ckey not in clusters:
                            clusters[ckey] = []
                        clusters[ckey].append(art_dict)

                # Pick representative article for each cluster (highest confidence, then freshness)
                cluster_representatives: list[dict[str, Any]] = []
                for ckey, items in clusters.items():
                    sorted_items = sorted(
                        items,
                        key=lambda x: (
                            CONFIDENCE_WEIGHTS.get(x["confidence"], 0.5),
                            x["freshness_weight"],
                        ),
                        reverse=True,
                    )
                    rep = dict(sorted_items[0])
                    rep["syndicated_copies_count"] = len(items)
                    cluster_representatives.append(rep)

                cluster_representatives.sort(
                    key=lambda x: x["freshness_weight"] * CONFIDENCE_WEIGHTS.get(x["confidence"], 0.5),
                    reverse=True,
                )

                distinct_clusters_count = len(cluster_representatives)

                # Fetch market reference
                market_overview = get_market_overview()
                market = market_overview.get("market") or {}
                xauusd_change_pct = market.get("xauusd_change_24h_pct", 0.0) or 0.0

                # Guard: Insufficient fresh news (< 2 distinct relevant clusters)
                if distinct_clusters_count < 2:
                    return {
                        "status": "INSUFFICIENT_FRESH_RELEVANT_NEWS",
                        "is_sufficient": False,
                        "window_hours": window_hours,
                        "evaluated_at": now.isoformat(),
                        "total_fresh_scanned": total_fresh_scanned,
                        "distinct_clusters_count": distinct_clusters_count,
                        "relevant_fresh_count": len(relevant_evaluated),
                        "total_articles": total_fresh_scanned,
                        "message": (
                            f"GoldSense AI detected {distinct_clusters_count} distinct fresh relevant news story cluster(s) "
                            f"within the past {window_hours} hours ({total_fresh_scanned} total articles scanned). "
                            f"At least 2 distinct clusters are required to establish an incremental directional news consensus "
                            f"and avoid reporting false sentiment."
                        ),
                        "net_signal": "UNCERTAIN",
                        "overall_signal": "UNCERTAIN",
                        "net_confidence": "LOW",
                        "bullish_weight": 0.0,
                        "bearish_weight": 0.0,
                        "mixed_weight": 0.0,
                        "net_sentiment_score": 0.0,
                        "market_vs_news_alignment": "INSUFFICIENT_FRESH_NEWS",
                        "driver_breakdown": {},
                        "driver_distribution": {},
                        "top_drivers": [],
                        "directional_breakdown": {"BULLISH": 0, "BEARISH": 0, "MIXED": 0, "UNCERTAIN": 0, "UNKNOWN": 0},
                        "market_reference": {
                            "xauusd": market.get("xauusd"),
                            "xauusd_change_24h_pct": xauusd_change_pct,
                            "usdinr": market.get("usdinr"),
                        },
                        "representative_articles": cluster_representatives,
                    }

                # Compute weights with source diversity and category saturation discounting
                bullish_weight = 0.0
                bearish_weight = 0.0
                mixed_weight = 0.0
                source_counts: dict[str, int] = {}
                driver_counts: dict[str, int] = {}
                directional_counts: dict[str, int] = {"BULLISH": 0, "BEARISH": 0, "MIXED": 0, "UNCERTAIN": 0, "UNKNOWN": 0}

                for rep in cluster_representatives:
                    d = rep["directional_implication"]
                    c = rep["confidence"]
                    fw = rep["freshness_weight"]
                    base_weight = fw * CONFIDENCE_WEIGHTS.get(c, 0.5)

                    src = (rep.get("source") or "Unknown").strip().lower()
                    source_counts[src] = source_counts.get(src, 0) + 1
                    source_discount = 1.0 / math.sqrt(source_counts[src])

                    driver_cat = rep.get("driver_category") or "OTHER_MACRO"
                    driver_counts[driver_cat] = driver_counts.get(driver_cat, 0) + 1
                    cat_discount = 1.0 / math.sqrt(driver_counts[driver_cat])

                    effective_weight = base_weight * source_discount * cat_discount
                    rep["effective_weight"] = round(effective_weight, 4)
                    rep["source_discount"] = round(source_discount, 3)
                    rep["category_discount"] = round(cat_discount, 3)

                    if d in directional_counts:
                        directional_counts[d] += 1
                    else:
                        directional_counts[d] = 1

                    # Directional weight allocation: UNKNOWN / UNCERTAIN never adds to directional weight
                    if d == "BULLISH":
                        bullish_weight += effective_weight
                    elif d == "BEARISH":
                        bearish_weight += effective_weight
                    elif d == "MIXED":
                        mixed_weight += effective_weight

                bullish_weight = round(bullish_weight, 3)
                bearish_weight = round(bearish_weight, 3)
                mixed_weight = round(mixed_weight, 3)

                total_dir_weight = bullish_weight + bearish_weight
                spread = abs(bullish_weight - bearish_weight)

                # Determine net directional consensus
                if bullish_weight >= 0.35 and bearish_weight >= 0.35 and (spread / max(total_dir_weight, 0.001)) < 0.30:
                    net_signal = "MIXED"
                elif bullish_weight >= 1.25 * bearish_weight and bullish_weight >= 0.35:
                    net_signal = "BULLISH"
                elif bearish_weight >= 1.25 * bullish_weight and bearish_weight >= 0.35:
                    net_signal = "BEARISH"
                elif spread < 0.2 and total_dir_weight > 0.35:
                    net_signal = "MIXED"
                else:
                    net_signal = "NEUTRAL"

                # Determine net confidence
                if distinct_clusters_count >= 5 and spread >= 0.6 and net_signal in ("BULLISH", "BEARISH"):
                    net_confidence = "HIGH"
                elif distinct_clusters_count >= 3 and spread >= 0.3:
                    net_confidence = "MEDIUM"
                else:
                    net_confidence = "LOW"

                # Evaluate market alignment
                if net_signal == "BULLISH":
                    if xauusd_change_pct >= 0.15:
                        market_vs_news_alignment = "AGREES_BULLISH"
                    elif xauusd_change_pct <= -0.15:
                        market_vs_news_alignment = "DIVERGES_NEWS_BULLISH_MARKET_BEARISH"
                    else:
                        market_vs_news_alignment = "NEWS_BULLISH_MARKET_FLAT"
                elif net_signal == "BEARISH":
                    if xauusd_change_pct <= -0.15:
                        market_vs_news_alignment = "AGREES_BEARISH"
                    elif xauusd_change_pct >= 0.15:
                        market_vs_news_alignment = "DIVERGES_NEWS_BEARISH_MARKET_BULLISH"
                    else:
                        market_vs_news_alignment = "NEWS_BEARISH_MARKET_FLAT"
                elif net_signal == "MIXED":
                    market_vs_news_alignment = "MIXED_SIGNALS_CONSOLIDATING"
                else:
                    market_vs_news_alignment = "NEUTRAL_MARKET_DRIVEN"

                primary_macro_driver = (
                    max(driver_counts, key=driver_counts.get) if driver_counts else "GENERAL"
                )

                top_drivers = [
                    {"driver": d, "count": c}
                    for d, c in sorted(driver_counts.items(), key=lambda x: x[1], reverse=True)
                ]

                net_score = round(bullish_weight - bearish_weight, 3)

                return {
                    "status": "ACTIVE",
                    "is_sufficient": True,
                    "window_hours": window_hours,
                    "evaluated_at": now.isoformat(),
                    "total_fresh_scanned": total_fresh_scanned,
                    "distinct_clusters_count": distinct_clusters_count,
                    "relevant_fresh_count": len(relevant_evaluated),
                    "total_articles": total_fresh_scanned,
                    "message": (
                        f"Active live news consensus derived from {distinct_clusters_count} distinct fresh story cluster(s) "
                        f"over the past {window_hours} hours. Primary macro driver: {primary_macro_driver}."
                    ),
                    "net_signal": net_signal,
                    "overall_signal": net_signal,
                    "net_confidence": net_confidence,
                    "bullish_weight": bullish_weight,
                    "bearish_weight": bearish_weight,
                    "mixed_weight": mixed_weight,
                    "net_sentiment_score": net_score,
                    "market_vs_news_alignment": market_vs_news_alignment,
                    "driver_breakdown": driver_counts,
                    "driver_distribution": driver_counts,
                    "top_drivers": top_drivers,
                    "directional_breakdown": directional_counts,
                    "market_reference": {
                        "xauusd": market.get("xauusd"),
                        "xauusd_change_24h_pct": xauusd_change_pct,
                        "usdinr": market.get("usdinr"),
                    },
                    "representative_articles": cluster_representatives[:25],
                }
        except Exception as exc:
            if not _is_csv_fallback_allowed():
                raise RuntimeError(f"Database query failed in production/PostgreSQL mode: {exc}") from exc
    else:
        if not _is_csv_fallback_allowed():
            raise RuntimeError("Database session is required in production/PostgreSQL mode")

    # 2. Fallback to existing CSV-based intelligence calculation (local dev only)
    return get_live_news_intelligence(window_hours=window_hours, reference_time=reference_time)


def get_news_summary_db(
    db: Session | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Aggregated news metrics separating gold-specific impact from generic tone.
    Queries PostgreSQL via get_latest_news_db.
    """
    articles = get_latest_news_db(db=db, limit=limit)
    total = len(articles)
    if total == 0:
        return {
            "total": 0,
            "total_analyzed": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "mixed_count": 0,
            "unknown_count": 0,
            "unknown_or_neutral_count": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "neutral_ratio": 0.0,
            "gold_relevant_count": 0,
            "gold_relevant_ratio": 0.0,
        }

    bullish = sum(
        1 for a in articles
        if a.get("gold_impact") == "BULLISH"
    )
    bearish = sum(
        1 for a in articles
        if a.get("gold_impact") == "BEARISH"
    )
    neutral = sum(
        1 for a in articles
        if a.get("gold_impact") == "NEUTRAL"
    )
    mixed = sum(
        1 for a in articles
        if a.get("gold_impact") == "MIXED"
    )
    unknown = sum(
        1 for a in articles
        if a.get("gold_impact", "UNKNOWN") in ("UNKNOWN", None)
    )
    unknown_or_mixed = neutral + mixed + unknown

    gold_rel = sum(
        1 for a in articles
        if a.get("is_gold_relevant") and not a.get("is_price_report")
    )

    return {
        "total": total,
        "total_analyzed": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "mixed_count": mixed,
        "unknown_count": unknown,
        "unknown_or_neutral_count": unknown_or_mixed,
        "positive_ratio": round(bullish / total, 2),
        "negative_ratio": round(bearish / total, 2),
        "neutral_ratio": round(unknown_or_mixed / total, 2),
        "gold_relevant_count": gold_rel,
        "gold_relevant_ratio": round(gold_rel / total, 2),
    }


def get_news_archive_db(
    db: Session | None = None,
    limit: int = 50,
    offset: int = 0,
    is_gold_relevant: bool | None = None,
) -> dict[str, Any]:
    """
    Returns historical news articles from the research archive.
    Queries PostgreSQL directly via SQLAlchemy; fallback allowed only in local dev.
    """
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except Exception:
            limit = 50
    limit = max(1, min(limit, 100))

    if not isinstance(offset, int):
        try:
            offset = int(offset)
        except Exception:
            offset = 0
    offset = max(0, offset)

    # 1. Primary: PostgreSQL / SQLAlchemy
    if db is not None:
        try:
            total_records = db.scalar(select(func.count(NewsArticleRecord.id))) or 0
            if total_records > 0 or not _is_csv_fallback_allowed():
                subq = _latest_label_subquery()
                stmt = (
                    select(
                        NewsArticleRecord,
                        subq.c.gold_impact,
                        subq.c.driver,
                        subq.c.is_price_report,
                        subq.c.confidence,
                    )
                    .outerjoin(
                        subq,
                        and_(
                            NewsArticleRecord.story_cluster_id == subq.c.lbl_cluster_id,
                            subq.c.rn == 1,
                        ),
                    )
                )

                if is_gold_relevant is True:
                    stmt = stmt.where(
                        and_(
                            or_(subq.c.is_price_report.is_(False), subq.c.is_price_report.is_(None)),
                            subq.c.driver.is_not(None),
                            subq.c.driver != "UNKNOWN",
                            subq.c.gold_impact.is_not(None),
                            subq.c.gold_impact not in ("UNKNOWN", "NEUTRAL"),
                        )
                    )
                elif is_gold_relevant is False:
                    stmt = stmt.where(
                        or_(
                            subq.c.is_price_report.is_(True),
                            subq.c.driver == "UNKNOWN",
                            subq.c.driver.is_(None),
                            subq.c.gold_impact.in_(("UNKNOWN", "NEUTRAL")),
                            subq.c.gold_impact.is_(None),
                        )
                    )

                stmt = stmt.order_by(
                    NewsArticleRecord.published_at.desc(),
                    NewsArticleRecord.id.desc(),
                ).offset(offset).limit(limit)

                rows = db.execute(stmt).all()

                results: list[dict[str, Any]] = []
                for row in rows:
                    art, gold_impact, driver, is_price_report, confidence = row
                    pub_dt = _ensure_utc(art.published_at)

                    g_impact = gold_impact or "UNKNOWN"
                    g_driver = driver or "UNKNOWN"
                    is_pr = bool(is_price_report) if is_price_report is not None else False
                    is_rel = bool(
                        not is_pr
                        and g_driver != "UNKNOWN"
                        and g_impact not in ("UNKNOWN", "NEUTRAL")
                    )

                    results.append({
                        "id": str(art.id),
                        "published_at": pub_dt.isoformat() if pub_dt else "",
                        "title": art.title,
                        "url": art.url,
                        "canonical_url": art.canonical_url,
                        "source": art.source,
                        "is_gold_relevant": is_rel,
                        "primary_category": "GOLD_DIRECT",
                        "driver": g_driver,
                        "directional_implication": g_impact if g_impact != "UNKNOWN" else "UNCERTAIN",
                    })

                return {
                    "total_archive_records": total_records,
                    "returned": len(results),
                    "offset": offset,
                    "limit": limit,
                    "articles": results,
                }
        except Exception as exc:
            if not _is_csv_fallback_allowed():
                raise RuntimeError(f"Database query failed in production/PostgreSQL mode: {exc}") from exc
    else:
        if not _is_csv_fallback_allowed():
            raise RuntimeError("Database session is required in production/PostgreSQL mode")

    # 2. Fallback: CSV Archive (local dev only)
    if not NEWS_ARCHIVE_CSV.exists():
        return {
            "total_archive_records": 0,
            "returned": 0,
            "offset": offset,
            "limit": limit,
            "articles": [],
        }

    results = []
    with NEWS_ARCHIVE_CSV.open("r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        total_records = len(reader)
        reversed_records = list(reversed(reader))

        for r in reversed_records[offset: offset + limit * 2]:
            rel = _scorer.score_text(r.get("title", ""))
            if is_gold_relevant is not None and rel.is_gold_relevant != is_gold_relevant:
                continue

            results.append({
                "published_at": r.get("published_at", ""),
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "source": r.get("source", ""),
                "is_gold_relevant": rel.is_gold_relevant,
                "primary_category": rel.primary_category.value if hasattr(rel.primary_category, "value") else str(rel.primary_category),
            })
            if len(results) >= limit:
                break

    return {
        "total_archive_records": total_records,
        "returned": len(results),
        "offset": offset,
        "limit": limit,
        "articles": results,
    }
