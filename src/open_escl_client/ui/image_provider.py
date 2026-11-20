"""`QQuickImageProvider` serving in-memory preview and cropped thumbnail images to QML."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader
from PySide6.QtQuick import QQuickImageProvider


def _decode_qimage_with_exif(image_bytes: bytes) -> QImage:
    """Decode image bytes into `QImage`, honoring EXIF Orientation like OpenCV."""
    buf = QBuffer()
    buf.setData(QByteArray(image_bytes))
    buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    reader.setAutoTransform(True)
    img = reader.read()
    buf.close()
    if not img.isNull():
        return img
    return QImage.fromData(image_bytes)


class PreviewImageProvider(QQuickImageProvider):
    """Provides scanned preview and cropped thumbnail `QImage` instances to QML `Image` elements.

    URL schemes:
    - `image://preview/<rev>`: Main preview image
    - `image://preview/crop/<index>/<rev>`: Deskewed & rotated thumbnail for crop box `#<index>`
    """

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._current_image: QImage = self._create_placeholder_image()
        self._crop_thumbnails: dict[int, QImage] = {}
        self._revision: int = 0
        self._crop_revision: int = 0

    @staticmethod
    def _create_placeholder_image() -> QImage:
        img = QImage(2, 2, QImage.Format.Format_RGBA8888)
        img.fill(QColor(255, 255, 255, 0))
        return img

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def current_image(self) -> QImage:
        return self._current_image

    def update_image_bytes(self, image_bytes: bytes) -> str:
        """Load `image_bytes` into a `QImage`, increment revision, and return URL."""
        loaded = _decode_qimage_with_exif(image_bytes)
        if loaded.isNull():
            raise ValueError("スキャン画像データのデコードに失敗しました")
        self._current_image = loaded
        self._revision += 1
        return f"image://preview/{self._revision}"

    def update_image_bgr(self, bgr: np.ndarray) -> str:
        """Load an in-memory BGR ndarray directly into `QImage` without JPEG re-compression."""
        if bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] != 3:
            raise ValueError("スキャン画像配列が不正です")
        rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        h, w = rgb.shape[:2]
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        if qimg.isNull():
            raise ValueError("プレビュー画像の変換に失敗しました")
        self._current_image = qimg
        self._revision += 1
        return f"image://preview/{self._revision}"

    def set_crop_thumbnail(self, index: int, image_bytes: bytes) -> str:
        """Store a single cropped/deskewed thumbnail for 1-based `index` and return its URL."""
        loaded = _decode_qimage_with_exif(image_bytes)
        if loaded.isNull():
            raise ValueError(f"トリミングサムネイル #{index} のデコードに失敗しました")
        self._crop_thumbnails[int(index)] = loaded
        self._crop_revision += 1
        return f"image://preview/crop/{int(index)}/{self._crop_revision}"

    def clear_crop_thumbnails(self) -> None:
        """Remove all cached cropped thumbnails."""
        self._crop_thumbnails.clear()
        self._crop_revision += 1

    def clear(self) -> None:
        """Reset main image and thumbnails to transparent placeholders."""
        self._current_image = self._create_placeholder_image()
        self._crop_thumbnails.clear()
        self._revision += 1
        self._crop_revision += 1

    def requestImage(  # noqa: N802
        self,
        id_str: str,
        size: QSize,
        requested_size: QSize,
    ) -> QImage:
        """Called by Qt Quick engine when resolving `image://preview/<id>`."""
        img = self._current_image
        if id_str.startswith("crop/"):
            parts = id_str.split("/")
            if len(parts) >= 2:
                try:
                    crop_idx = int(parts[1])
                    img = self._crop_thumbnails.get(crop_idx, self._create_placeholder_image())
                except ValueError:
                    img = self._create_placeholder_image()

        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        if requested_size.isValid() and requested_size.width() > 0 and requested_size.height() > 0:
            return img.scaled(
                requested_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return img
