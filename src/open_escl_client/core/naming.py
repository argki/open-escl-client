"""Flexible file naming rule engine supporting date, time, and auto-incrementing sequence tokens."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from string import Formatter

DEFAULT_NAMING_TEMPLATE = "Scan_{date}_{time}_{seq:03d}"

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FORMAT_TO_EXTENSION: dict[str, str] = {
    "PDF": ".pdf",
    "JPEG": ".jpg",
    "JPG": ".jpg",
    "PNG": ".png",
}


def normalize_extension(file_format_or_ext: str) -> str:
    """Convert a format name (`PDF`, `JPEG`, `PNG`) or extension (`.jpg`, `png`) to a lowercase `.ext`."""
    cleaned = file_format_or_ext.strip()
    if not cleaned:
        return ".pdf"
    upper = cleaned.lstrip(".").upper()
    if upper in _FORMAT_TO_EXTENSION:
        return _FORMAT_TO_EXTENSION[upper]
    ext = cleaned.lstrip(".").lower()
    return f".{ext}"


def sanitize_filename_stem(stem: str) -> str:
    """Remove characters illegal on macOS/Windows filesystems and trim trailing dots/spaces."""
    sanitized = _INVALID_FILENAME_CHARS_RE.sub("_", stem).strip(" .")
    return sanitized if sanitized else "Scan"


def template_has_sequence_token(template: str) -> bool:
    """Return True if the naming template explicitly references a `{seq...}` placeholder."""
    try:
        for _literal, field_name, _spec, _conv in Formatter().parse(template):
            if field_name == "seq":
                return True
    except ValueError:
        return False
    return False


class _SafeFormatDict(dict[str, object]):
    """Dictionary that preserves unknown `{token}` placeholders instead of raising `KeyError`."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class FileNamingEngine:
    """Expands user naming templates and resolves collision-free output file paths."""

    def __init__(self, default_template: str = DEFAULT_NAMING_TEMPLATE) -> None:
        self.default_template = default_template

    def build_tokens(
        self,
        seq: int,
        now: datetime | None = None,
        extra_tokens: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Build the token map for template expansion."""
        ts = now if now is not None else datetime.now()
        tokens: dict[str, object] = {
            "date": ts.strftime("%Y%m%d"),
            "date_iso": ts.strftime("%Y-%m-%d"),
            "yyyy": ts.strftime("%Y"),
            "year": ts.strftime("%Y"),
            "mm": ts.strftime("%m"),
            "month": ts.strftime("%m"),
            "dd": ts.strftime("%d"),
            "day": ts.strftime("%d"),
            "time": ts.strftime("%H%M%S"),
            "HH": ts.strftime("%H"),
            "MM": ts.strftime("%M"),
            "SS": ts.strftime("%S"),
            "seq": max(1, int(seq)),
        }
        if extra_tokens:
            tokens.update(extra_tokens)
        return tokens

    def render_filename(
        self,
        template: str,
        *,
        seq: int = 1,
        file_format: str = "PDF",
        now: datetime | None = None,
        extra_tokens: dict[str, object] | None = None,
    ) -> str:
        """Render a sanitized filename (with extension) from `template` and `seq`."""
        effective_template = template.strip() or self.default_template
        tokens = self.build_tokens(seq=seq, now=now, extra_tokens=extra_tokens)
        ext = normalize_extension(file_format)

        try:
            rendered = effective_template.format_map(_SafeFormatDict(tokens))
        except (ValueError, KeyError, IndexError):
            # Fall back safely when the user is mid-typing an incomplete brace/format spec in the UI
            rendered = self.default_template.format_map(_SafeFormatDict(tokens))

        # Avoid double extension if the user already wrote `.pdf` / `.jpg` / `.png` in the template
        if rendered.lower().endswith(ext):
            rendered = rendered[: -len(ext)]

        stem = sanitize_filename_stem(rendered)
        return f"{stem}{ext}"

    def resolve_next_path(
        self,
        save_dir: Path | str,
        template: str,
        *,
        file_format: str = "PDF",
        start_seq: int = 1,
        now: datetime | None = None,
        extra_tokens: dict[str, object] | None = None,
        max_attempts: int = 10000,
    ) -> tuple[Path, int]:
        """Resolve the next non-existing file path in `save_dir`, returning `(path, used_seq)`.

        Never overwrites an existing file: increments `seq` until an unused filename is found.
        If the template has no `{seq}` placeholder and the base file already exists, automatically
        appends `_{seq:03d}` to avoid collision.
        """
        directory = Path(save_dir).expanduser()
        ts = now if now is not None else datetime.now()
        has_seq = template_has_sequence_token(template.strip() or self.default_template)
        seq = max(1, int(start_seq))

        if not has_seq:
            base_name = self.render_filename(
                template,
                seq=seq,
                file_format=file_format,
                now=ts,
                extra_tokens=extra_tokens,
            )
            base_path = directory / base_name
            if not base_path.exists():
                return base_path, seq

            # Append `_{seq:03d}` when colliding on a template without `{seq}`
            fallback_template = f"{(template.strip() or self.default_template)}_{{seq:03d}}"
            for attempt in range(max_attempts):
                candidate_seq = seq + attempt
                candidate_name = self.render_filename(
                    fallback_template,
                    seq=candidate_seq,
                    file_format=file_format,
                    now=ts,
                    extra_tokens=extra_tokens,
                )
                candidate_path = directory / candidate_name
                if not candidate_path.exists():
                    return candidate_path, candidate_seq
            raise RuntimeError("保存可能な連番ファイル名が見つかりませんでした")

        for attempt in range(max_attempts):
            candidate_seq = seq + attempt
            candidate_name = self.render_filename(
                template,
                seq=candidate_seq,
                file_format=file_format,
                now=ts,
                extra_tokens=extra_tokens,
            )
            candidate_path = directory / candidate_name
            if not candidate_path.exists():
                return candidate_path, candidate_seq

        raise RuntimeError("保存可能な連番ファイル名が見つかりませんでした")

    def preview_next_filename(
        self,
        save_dir: Path | str,
        template: str,
        *,
        file_format: str = "PDF",
        start_seq: int = 1,
        now: datetime | None = None,
        extra_tokens: dict[str, object] | None = None,
    ) -> str:
        """Return the filename that will be used for the next scan save."""
        next_path, _used_seq = self.resolve_next_path(
            save_dir,
            template,
            file_format=file_format,
            start_seq=start_seq,
            now=now,
            extra_tokens=extra_tokens,
        )
        return next_path.name
