# pubfig

`pubfig` is a PySide6 desktop editor for turning pasted or imported table data
into publication-ready Matplotlib figures. It supports multi-sheet projects,
per-series styling, annotations, journal-sized canvases, and exact-size vector
or raster export.

Implementation code lives in the `pubfig` package, with root-level
compatibility entry points for existing workflows.

## Requirements

- Python 3.10 or newer
- A desktop environment supported by Qt/PySide6
- Matplotlib 3.7+, pandas 2.0+, and PySide6 6.5+
- SciencePlots 2.1+ is optional; pubfig uses its publication style when
  available and otherwise falls back to its built-in style

The old `tol-colors` dependency is no longer required because the palettes
used by pubfig are defined in the package itself. NumPy is installed through
Matplotlib and pandas and is not a direct application dependency.

## Install

From this directory, create a virtual environment and install in editable
mode:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[styles]"
```

On Windows PowerShell, activate with:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[styles]"
```

The legacy dependency-file workflow remains available:

```bash
python -m pip install -r requirements.txt
```

Omit `[styles]` when the optional SciencePlots theme is not needed.

## Run

An editable or regular install provides the `pubfig` command:

```bash
pubfig
```

The package and source-tree entry points are equivalent:

```bash
python -m pubfig
python main.py
```

On Windows, `py -m pubfig` can be used when `python` opens the Microsoft
Store.

## Non-blocking application API

Application construction is separate from the Qt event loop:

```python
from pubfig.application import create_application

session = create_application(show=False)
window = session.window

# Configure or inspect the window here.
window.show()
raise SystemExit(session.exec())
```

`create_application()` returns immediately and reuses an existing
`QApplication` when embedded in another Qt process. Only `session.exec()` or
`pubfig.application.main()` enters the blocking event loop.

## Project layout

```text
pubfig/
├── application.py       # QApplication/window creation and CLI bootstrap
├── model.py             # sheets, graphs, project tree, model repair
├── project_io.py        # JSON validation, migration, atomic persistence
├── sheet_data.py        # table roles and DataFrame normalization
├── plot_config.py       # persisted figure/series/annotation settings
├── undo.py              # Qt-independent bounded undo/redo history
├── rendering/           # render orchestration, axes, artists, export
└── ui/                  # main window, settings panels, reusable widgets
```

The root-level `app.py`, `renderer.py`, `plot_config.py`, `data_parser.py`,
`table_view.py`, and `theme.py` files are compatibility façades. New code
should import from the package:

| Legacy import | Preferred import |
| --- | --- |
| `from app import GraphDrawerWindow` | `from pubfig.ui.main_window import GraphDrawerWindow` |
| `from renderer import render_figure` | `from pubfig.rendering import render_figure` |
| `from plot_config import PlotConfig` | `from pubfig.plot_config import PlotConfig` |
| `from data_parser import parse_table_text` | `from pubfig.data_parser import parse_table_text` |

## Existing project migration

No manual JSON conversion is required. `project_io` accepts valid legacy
schema v1 and v2 projects as well as current schema v3 projects, repairs their
model/tree relationships, and normalizes them in memory. The next save writes
the current v3 format. Keeping a backup before overwriting an important legacy
project is still recommended.

Source integrations can migrate incrementally because the root compatibility
imports and `python main.py` launcher remain available. Prefer package imports
for all new work.

## Test

Qt tests run with the offscreen platform and do not require opening a visible
window:

```bash
QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg \
  python -m unittest discover -s tests -v
```

The application bootstrap tests inject a fake window/session, so they verify
`create_application`, `python -m pubfig`, and the legacy launcher without
entering a real GUI event loop.

## Main features

- Paste from spreadsheets or import CSV/TXT/TSV/DAT data
- Virtualized editable table with X/Y role and display-name rows
- Line, scatter, line+marker, bar, step, area, and stem plots
- Error bars, dual Y axes, broken axes, log scales, and custom ticks
- Per-series colors, markers, alpha, offsets, gradients, and legend entries
- Text, textbox, line, arrow, and rectangle annotations
- Journal presets and exact physical canvas/plot dimensions
- PNG, PDF, SVG, TIFF, and EPS export
- Multi-figure projects, autosave, crash recovery, undo, and redo
