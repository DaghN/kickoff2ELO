"""
Classic two-player Elo (win/draw/loss) with provisional dual‑K tuning.

Symmetric replay uses `BASE_RATING`, `K_FACTOR`, and provisional scaling rules
living in `kool_elo.config`—tweak constants there rather than scattering magic numbers.
"""

from __future__ import annotations

from kool_elo.config import (
    PROVISIONAL_GAMES_FULL_THRESHOLD,
    PROVISIONAL_K_CEILING_ABOVE_RATING,
)


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


def provisional_self_multiplier(prematch_games: int) -> float:
    """Scale provisional player's own responsiveness (forum factor A)."""

    if prematch_games >= PROVISIONAL_GAMES_FULL_THRESHOLD:
        return 1.0
    return 3.0 - (2.0 * prematch_games / PROVISIONAL_GAMES_FULL_THRESHOLD)


def provisional_opponent_multiplier(opponent_prematch_games: int) -> float:
    """Damp swings when opponent is still provisional (forum factor B)."""

    if opponent_prematch_games >= PROVISIONAL_GAMES_FULL_THRESHOLD:
        return 1.0
    return (1.0 / 3.0) + (2.0 / 3.0) * (
        opponent_prematch_games / PROVISIONAL_GAMES_FULL_THRESHOLD
    )


def provisional_k_player(
    base_k: float,
    *,
    prematch_games_self: int,
    prematch_games_opponent: int,
    prematch_rating_self: float,
) -> float:
    """
    Forum-style per-player K using prematch ladders for self + opponent damping.

    If still provisional (< threshold games) AND prematch_rating_self strictly exceeds
    `PROVISIONAL_K_CEILING_ABOVE_RATING`, clamp **from above** to `base_k` only — never inflate.
    """

    k_raw = base_k * provisional_self_multiplier(prematch_games_self) * provisional_opponent_multiplier(
        prematch_games_opponent
    )
    if (
        prematch_games_self < PROVISIONAL_GAMES_FULL_THRESHOLD
        and prematch_rating_self > PROVISIONAL_K_CEILING_ABOVE_RATING
    ):
        return float(min(k_raw, base_k))
    return float(k_raw)


def update_pair_after_game(
    rating_a: float,
    rating_b: float,
    *,
    k: float,
    score_a: int,
    score_b: int,
) -> tuple[float, float]:
    """Legacy symmetric helper (same K on both seats)."""

    sa = fraction_for_a(score_a, score_b)
    sb = fraction_for_a(score_b, score_a)
    ea = expected_fraction_a(rating_a, rating_b)
    eb = expected_fraction_a(rating_b, rating_a)
    rating_a_next = rating_a + k * (sa - ea)
    rating_b_next = rating_b + k * (sb - eb)

    return rating_a_next, rating_b_next


def update_pair_dual_k(
    rating_a: float,
    rating_b: float,
    *,
    base_k: float,
    score_a: int,
    score_b: int,
    prematch_games_a: int,
    prematch_games_b: int,
) -> tuple[float, float, float, float]:
    """
    Asymmetric provisional update returning ``(Ra', Rb', Ka, Kb)`` using prematch ladders.

    Each seat gets its own K; rating sums are generally **not** conserved — intentional.
    """

    k_a = provisional_k_player(
        base_k,
        prematch_games_self=prematch_games_a,
        prematch_games_opponent=prematch_games_b,
        prematch_rating_self=rating_a,
    )
    k_b = provisional_k_player(
        base_k,
        prematch_games_self=prematch_games_b,
        prematch_games_opponent=prematch_games_a,
        prematch_rating_self=rating_b,
    )

    sa = fraction_for_a(score_a, score_b)
    sb = fraction_for_a(score_b, score_a)
    ea = expected_fraction_a(rating_a, rating_b)
    eb = expected_fraction_a(rating_b, rating_a)

    rating_a_next = rating_a + k_a * (sa - ea)
    rating_b_next = rating_b + k_b * (sb - eb)

    return rating_a_next, rating_b_next, k_a, k_b
