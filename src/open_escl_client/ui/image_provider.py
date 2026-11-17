"""`QQuickImageProvider` serving in-memory preview scan images (`image://preview/<rev>`) to QML."""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtQuick import QQuickImageProvider


class PreviewImageProvider(QQuickImageProvider):
    """Provides scanned preview `QImage` instances to QML `Image` elements."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._current_image: QImage = self._create_placeholder_image()
        self._revision: int = 0

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
        """Load `image_bytes` into a `QImage`, increment revision, and return the `image://preview/<rev>` URL."""
        loaded = QImage.fromData(image_bytes)
        if loaded.isNull():
            raise ValueError("スキャン画像データのデコードに失敗しました")
        self._current_image = loaded
        self._revision += 1
        return f"image://preview/{self._revision}"

    def clear(self) -> None:
        """Reset to a transparent placeholder image."""
        self._current_image = self._create_placeholder_image()
        self._revision += 1

    def requestImage(self, id_str: str, size: QSize, requested_size: QSize) -> QImage:  # noqa: N802
        """Called by Qt Quick engine when resolving `image://preview/<id>`."""
        _ = id_str
        img = self._current_image
        if size is not None:
            size.setWidth(img.width())
            size.setHeight(img.height())
        if requested_size.isValid() and requested_size.width() > 0 and requested_size.height() > 0:
            return img.scaled(requested_size)
        return img
