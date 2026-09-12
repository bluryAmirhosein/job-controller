import pytest

from app.infrastructure.cache.job_list_cache import (
    ADMIN_SCOPE,
    JOB_LIST_CACHE_TTL_SECONDS,
    bump_job_list_version,
    get_cached_job_list,
    get_job_list_version,
    set_cached_job_list,
)


class FakeRedis:
    """
    Minimal in-memory stand-in for the subset of redis.asyncio.Redis used
    by job_list_cache: get, set (with an optional ex= TTL), and incr.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: list[dict] = []

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value
        self.set_calls.append({"key": key, "value": value, "ex": ex})

    async def incr(self, key: str) -> int:
        current = int(self._store.get(key, 0)) + 1
        self._store[key] = str(current)
        return current


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


class TestJobListVersion:
    async def test_returns_zero_when_no_version_has_been_set(self, fake_redis):
        version = await get_job_list_version(fake_redis, "owner-a")

        assert version == 0

    async def test_bump_increments_the_version_starting_from_zero(self, fake_redis):
        await bump_job_list_version(fake_redis, "owner-a")

        version = await get_job_list_version(fake_redis, "owner-a")

        assert version == 1

    async def test_bump_is_scoped_independently_per_owner(self, fake_redis):
        await bump_job_list_version(fake_redis, "owner-a")

        assert await get_job_list_version(fake_redis, "owner-a") == 1
        assert await get_job_list_version(fake_redis, "owner-b") == 0

    async def test_admin_scope_version_is_independent_from_any_owner_scope(self, fake_redis):
        await bump_job_list_version(fake_redis, ADMIN_SCOPE)

        assert await get_job_list_version(fake_redis, ADMIN_SCOPE) == 1
        assert await get_job_list_version(fake_redis, "owner-a") == 0

    async def test_parses_bytes_values_as_returned_by_a_real_redis_client(self, fake_redis):
        # A real redis client without decode_responses=True returns bytes;
        # make sure the parsing logic tolerates that.
        fake_redis._store["jobs:list:version:owner-a"] = b"3"

        version = await get_job_list_version(fake_redis, "owner-a")

        assert version == 3


class TestCachedJobListRoundTrip:
    async def test_set_then_get_with_the_same_key_returns_the_same_payload(self, fake_redis):
        await set_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page", '{"items": []}')

        cached = await get_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page")

        assert cached == '{"items": []}'

    async def test_get_returns_none_for_a_key_that_was_never_cached(self, fake_redis):
        cached = await get_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page")

        assert cached is None

    async def test_a_different_scope_does_not_see_another_scopes_cache(self, fake_redis):
        await set_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page", "owner-a-data")

        cached_for_owner_b = await get_cached_job_list(
            fake_redis, "owner-b", 1, 50, "first-page"
        )

        assert cached_for_owner_b is None

    async def test_admin_scope_cache_is_isolated_from_owner_scope_cache(self, fake_redis):
        await set_cached_job_list(fake_redis, ADMIN_SCOPE, 1, 50, "first-page", "admin-data")

        cached_for_owner = await get_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page")

        assert cached_for_owner is None

    async def test_a_different_limit_is_treated_as_a_different_cache_entry(self, fake_redis):
        await set_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page", "fifty-per-page")

        cached_with_different_limit = await get_cached_job_list(
            fake_redis, "owner-a", 1, 20, "first-page"
        )

        assert cached_with_different_limit is None

    async def test_a_different_cursor_is_treated_as_a_different_cache_entry(self, fake_redis):
        await set_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page", "page-one-data")

        cached_for_other_cursor = await get_cached_job_list(
            fake_redis, "owner-a", 1, 50, "some-other-cursor"
        )

        assert cached_for_other_cursor is None

    async def test_bumping_the_version_makes_the_previously_cached_page_unreachable(
        self, fake_redis
    ):
        version_before = await get_job_list_version(fake_redis, "owner-a")
        await set_cached_job_list(
            fake_redis, "owner-a", version_before, 50, "first-page", "stale-data"
        )

        await bump_job_list_version(fake_redis, "owner-a")
        version_after = await get_job_list_version(fake_redis, "owner-a")

        cached_at_new_version = await get_cached_job_list(
            fake_redis, "owner-a", version_after, 50, "first-page"
        )
        assert cached_at_new_version is None

        # The stale entry is not deleted, just orphaned under the old
        # version's key — nothing in the code path reads it again once
        # the version has moved on.
        cached_at_old_version = await get_cached_job_list(
            fake_redis, "owner-a", version_before, 50, "first-page"
        )
        assert cached_at_old_version == "stale-data"

    async def test_set_cached_job_list_applies_the_configured_ttl(self, fake_redis):
        await set_cached_job_list(fake_redis, "owner-a", 1, 50, "first-page", "data")

        assert fake_redis.set_calls[-1]["ex"] == JOB_LIST_CACHE_TTL_SECONDS