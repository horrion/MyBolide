from mybolide.disk import GiB, MiB, cache_limit_bytes, filesystem_usage


def test_large_card_keeps_half_and_leaves_a_quarter():
    limit = cache_limit_bytes(32 * GiB, 26 * GiB, None)
    assert limit.nbytes == 16 * GiB
    assert "SD card" in limit.summary


def test_busy_card_only_uses_space_above_the_reserve():
    limit = cache_limit_bytes(32 * GiB, 12 * GiB, None)
    assert limit.nbytes == 4 * GiB


def test_tight_card_uses_a_quarter_of_what_is_free():
    limit = cache_limit_bytes(4 * GiB, GiB // 2, None)
    assert limit.nbytes == (GiB // 2) // 4


def test_override_sets_the_cap():
    limit = cache_limit_bytes(32 * GiB, 26 * GiB, 512)
    assert limit.nbytes == 512 * MiB
    assert "max_db_mb" in limit.summary


def test_override_is_cut_down_to_leave_margin_on_the_card():
    limit = cache_limit_bytes(32 * GiB, GiB, 10_000)
    assert limit.nbytes == GiB - 64 * MiB
    assert "free space" in limit.summary


def test_filesystem_usage_sees_a_real_volume(tmp_path):
    usage = filesystem_usage(tmp_path)
    assert usage.total_bytes > 0
    assert 0 <= usage.free_bytes <= usage.total_bytes
