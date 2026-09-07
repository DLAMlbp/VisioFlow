from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class EmbeddedAsset(Protocol):
    id: str
    embedding: Sequence[float] | None


def select_diverse_group_prototypes(
    assets: Sequence[EmbeddedAsset], *, limit: int
) -> list[str]:
    """Choose a central medoid followed by diverse, deterministic representatives."""
    valid = sorted(
        (asset for asset in assets if asset.embedding is not None),
        key=lambda asset: asset.id,
    )
    if not valid or limit <= 0:
        return []
    matrix = np.asarray([list(asset.embedding or []) for asset in valid], dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        return []
    norms = np.linalg.norm(matrix, axis=1)
    usable = norms > 0
    if not np.all(usable):
        valid = [asset for asset, keep in zip(valid, usable, strict=True) if keep]
        matrix = matrix[usable]
        norms = norms[usable]
    if not valid:
        return []
    normalized = matrix / norms[:, None]
    count = min(limit, len(valid))

    centroid = normalized.mean(axis=0)
    centroid_norm = float(np.linalg.norm(centroid))
    central_similarity = (
        normalized @ (centroid / centroid_norm)
        if centroid_norm > 0
        else np.zeros(len(valid), dtype=np.float32)
    )
    selected = [int(np.argmax(central_similarity))]
    closest_similarity = normalized @ normalized[selected[0]]
    while len(selected) < count:
        remaining = [index for index in range(len(valid)) if index not in selected]
        next_index = min(
            remaining,
            key=lambda index: (float(closest_similarity[index]), valid[index].id),
        )
        selected.append(next_index)
        closest_similarity = np.maximum(
            closest_similarity,
            normalized @ normalized[next_index],
        )
    return [valid[index].id for index in selected]
