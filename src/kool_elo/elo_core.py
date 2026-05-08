"""
Classic two-player Elo (win/draw/loss).

Parameters are threaded from callers (`config.BASE_RATING`, `config.K_FACTOR`) so
experiments only touch one place—or CLI overrides—without rewriting formulas.
"""


def expected_fraction_a(rating_a: float, rating_b: float) -> float:
    """Expected fractional score for player A (1 = win, 0.5 draw, 0 loss)."""

    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def fraction_for_a(score_a: int, score_b: int) -> float:
    """Observed fractional score from A's perspective."""

    if score_a > score_b:
        return 1.0
    if score_a < score_b:
        return 0.0
    return 0.5


def update_pair_after_game(
    rating_a: float,
    rating_b: float,
    *,
    k: float,
    score_a: int,
    score_b: int,
) -> tuple[float, float]:
    """Return (new_ra, new_rb) after processing one symmetric game."""

    sa = fraction_for_a(score_a, score_b)
    sb = 1.0 - sa  # symmetric two-player totals
    ea = expected_fraction_a(rating_a, rating_b)
    eb = 1.0 - ea

    rating_a_next = rating_a + k * (sa - ea)
    rating_b_next = rating_b + k * (sb - eb)

    return rating_a_next, rating_b_next

