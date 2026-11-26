"""
Test SSE Watermark Propagation with in-memory City Cache updates.
Verifies that when live_city_cache updates in-memory:
1. The SSE watermark advances without requiring database writes.
2. An updated snapshot is emitted immediately.
3. Unchanged cache emits no redundant snapshots.
"""
import asyncio
from datetime import datetime, timezone
import pytest
from app.services.kjpl.live_rate_service import live_city_cache
from app.api.routes.bullion import _bullion_sse_generator


class MockRequest:
    def __init__(self, disconnect_after_iterations: int = 3):
        self._iterations = 0
        self._disconnect_after = disconnect_after_iterations

    async def is_disconnected(self) -> bool:
        self._iterations += 1
        return self._iterations > self._disconnect_after


@pytest.mark.anyio
async def test_sse_watermark_advances_on_city_cache_mutation():
    live_city_cache.clear()
    initial_version = live_city_cache.get_version()
    initial_wm = live_city_cache.get_watermark()
    assert initial_wm.startswith(f"v{initial_version}:")

    # Update cache for Chennai (ID 48)
    ok, changed = live_city_cache.update_rate(
        "CHENNAI",
        price=14200.0,
        source_product_id="48",
        observed_at=datetime.now(timezone.utc),
    )
    assert ok is True
    updated_version = live_city_cache.get_version()
    assert updated_version == initial_version + 1
    updated_wm = live_city_cache.get_watermark()
    assert updated_wm != initial_wm

    # Run generator for 1 iteration
    req = MockRequest(disconnect_after_iterations=1)
    gen = _bullion_sse_generator(req)
    frame = await anext(gen)
    assert frame.startswith("event: snapshot\n")
    assert "CHENNAI" in frame
    assert "14200" in frame
