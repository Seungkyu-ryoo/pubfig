"""Atomic Matplotlib figure export."""

from __future__ import annotations

from pathlib import Path
import tempfile

from matplotlib.figure import Figure

from ..plot_config import PlotConfig


def export_figure(fig: Figure, path: str | Path, config: PlotConfig) -> None:
    path = Path(path)
    save_kwargs = {
        "dpi": config.dpi,
        "facecolor": "none" if config.transparent else "white",
        "transparent": config.transparent,
        # A third-party style must not turn a fixed canvas into an implicitly
        # tight-cropped export.
        "bbox_inches": None,
    }
    if config.trim_whitespace:
        save_kwargs["bbox_inches"] = "tight"
        bbox_extra_artists = [
            artist
            for artist in fig.findobj()
            if artist.get_visible()
            and hasattr(artist, "get_in_layout")
            and not artist.get_in_layout()
            and hasattr(artist, "get_window_extent")
        ]
        if bbox_extra_artists:
            save_kwargs["bbox_extra_artists"] = bbox_extra_artists

    suffix = path.suffix or ".png"
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f".{path.stem}.",
        suffix=suffix,
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    try:
        fig.savefig(temp_path, **save_kwargs)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


__all__ = ["export_figure"]
