"""Подсказка порядка просмотра: только порядок, ничего не снимается."""

from __future__ import annotations

import pandas as pd

from vantage.triage import review_order, review_priority


def test_far_from_road_and_fragmented_goes_first():
    frame = pd.DataFrame({
        "candidate_id": ["склад", "свалка", "средний"],
        "dist_road_m": [10, 300, 120],
        "n_pieces": [1, 4, 2],
        "area_m2": [900, 800, 850],
    })
    assert list(review_order(frame)["candidate_id"]) == ["свалка", "средний", "склад"]


def test_nothing_is_removed():
    frame = pd.DataFrame({"dist_road_m": [5, None, 50], "n_pieces": [1, 2, None]})
    assert len(review_order(frame)) == len(frame)


def test_missing_features_are_neutral():
    frame = pd.DataFrame({"area_m2": [1, 2]})
    assert (review_priority(frame) == 0.5).all()


def test_highres_score_joins_with_weight_one_to_two():
    frame = pd.DataFrame({
        "dist_road_m": [10, 10], "n_pieces": [1, 1], "highres_score": [0.9, 0.1],
    })
    p = review_priority(frame)
    assert p.iloc[0] > p.iloc[1]
