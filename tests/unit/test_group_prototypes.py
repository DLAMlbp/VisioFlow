from types import SimpleNamespace

import numpy as np

from src.services.images.group_prototypes import select_diverse_group_prototypes


def _asset(asset_id: str, values: list[float] | None):
    return SimpleNamespace(id=asset_id, embedding=values)


def test_prototype_selection_keeps_central_and_diverse_assets() -> None:
    assets = [
        _asset("a", [1.0, 0.0]),
        _asset("b", [0.99, 0.01]),
        _asset("c", [0.0, 1.0]),
        _asset("d", [0.01, 0.99]),
    ]

    selected = select_diverse_group_prototypes(assets, limit=2)

    assert len(selected) == 2
    vectors = {asset.id: np.asarray(asset.embedding) for asset in assets}
    assert float(vectors[selected[0]] @ vectors[selected[1]]) < 0.1


def test_prototype_selection_is_deterministic_and_respects_limit() -> None:
    assets = [
        _asset("c", [0.0, 1.0]),
        _asset("a", [1.0, 0.0]),
        _asset("b", [0.7, 0.7]),
    ]

    first = select_diverse_group_prototypes(assets, limit=2)
    second = select_diverse_group_prototypes(list(reversed(assets)), limit=2)

    assert first == second
    assert len(first) == 2


def test_prototype_selection_ignores_missing_and_zero_vectors() -> None:
    assets = [
        _asset("missing", None),
        _asset("zero", [0.0, 0.0]),
        _asset("valid", [1.0, 0.0]),
    ]

    assert select_diverse_group_prototypes(assets, limit=6) == ["valid"]
