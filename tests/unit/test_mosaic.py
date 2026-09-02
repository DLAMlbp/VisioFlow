import numpy as np

from src.services.images.mosaic import apply_pixel_mosaic, expand_box


def test_expand_box_clamps_to_image_bounds() -> None:
    assert expand_box(
        (0, 5, 20, 25), image_width=100, image_height=80, expansion=0.25
    ) == (0, 0, 25, 30)
    assert (
        expand_box(
            (110, 5, 120, 25), image_width=100, image_height=80, expansion=0.25
        )
        is None
    )


def test_mosaic_only_changes_the_expanded_detection_box() -> None:
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    for x in range(100):
        image[:, x] = (x, 255 - x, (x * 7) % 255)

    result, boxes = apply_pixel_mosaic(
        image, [(30, 20, 70, 60)], expansion=0.1, block_ratio=0.2
    )

    assert boxes == [(26, 16, 74, 64)]
    changed = np.any(result != image, axis=2)
    assert changed[16:64, 26:74].any()
    changed[16:64, 26:74] = False
    assert not changed.any()


def test_mosaic_ignores_empty_and_out_of_bounds_boxes() -> None:
    image = np.full((40, 50, 3), 127, dtype=np.uint8)

    result, boxes = apply_pixel_mosaic(
        image,
        [(10, 10, 10, 20), (100, 100, 110, 110)],
    )

    assert boxes == []
    assert np.array_equal(result, image)

