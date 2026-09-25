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
├── editor_session.py    # canonical document and active editing context
├── project_io.py        # JSON validation, migration, atomic persistence
├── dataframe_codec.py   # DataFrame JSON encoding and dtype validation
├── graph_editing.py     # graph references after sheet column changes
├── sheet_data.py        # table roles and DataFrame normalization
├── plot_config.py       # persisted figure/series/annotation settings
├── undo.py              # Qt-independent bounded undo/redo history
├── rendering/           # render orchestration, axes, artists, export
└── ui/                  # main window, settings panels, reusable widgets
```

`ui/main_window.py` composes the application and routes window events. Feature
controllers own the workflows that used to live in the window:

| Module in `ui/` | Responsibility |
| --- | --- |
| `workspace_controller.py` | Active sheet/graph loading and undo/redo transactions |
| `project_files.py` | Open/save, autosave, recovery, and writer shutdown |
| `project_tree.py` | Project explorer creation, renaming, duplication, deletion, and moves |
| `table_editor.py`, `table_actions.py` | Sheet edits, reference updates, and cell/header menus |
| `series_editor.py`, `figure_editor.py` | Series and figure settings workflows |
| `annotations_editor.py`, `legend_editor.py` | Annotation and legend editing |
| `style_controller.py` | Style copying, application, and named style storage |
| `preview_controller.py` | Rendering, canvas ownership, sizing, and export |
| `canvas_interaction.py` | Dragging, hit testing, handles, and preview gestures |
| `window_layout.py` | Widget construction and signal connections |

`EditorSession` resolves the active DataFrame and graph configuration directly
from `ProjectDocument`. The table controller publishes structural table changes
before snapshots or saves, and settings controllers commit widget values into
the active config. `Graph.annotations` is an accessor for
`Graph.plot_config.annotations`; there is only one annotation list. Leaving a
graph for a bare sheet uses separate draft settings, so sheet selection cannot
overwrite the previous graph. The current save path and file revision also
come from the document instead of a separate window copy.

`window_compat.py` provides an explicit list of legacy window methods and state
properties. Existing calls such as `window.save_project()` and
`window.add_column(position="left")` remain available. New integrations can use
`window.files.save_project()` and `window.table_actions.insert_column("left")`.
To instrument or customize a workflow internally, override the method on its
owning controller, for example `window.preview.render_plot`.

The root-level `app.py`, `renderer.py`, `plot_config.py`, `data_parser.py`,
`table_view.py`, and `theme.py` files are compatibility façades. New code
should import from the package:

| Legacy import | Preferred import |
| --- | --- |
| `from app import GraphDrawerWindow` | `from pubfig.ui.main_window import GraphDrawerWindow` |
| `from renderer import render_figure` | `from pubfig.rendering import render_figure` |
| `from plot_config import PlotConfig` | `from pubfig.plot_config import PlotConfig` |
| `from data_parser import parse_table_text` | `from pubfig.data_parser import parse_table_text` |

## Renaming project items and columns

Select a sheet, graph, or folder in Project Explorer and use **Rename**, the
context menu, or **F2**. Use **Edit → Rename Project...** (or the empty-area
tree context menu) for the hidden project root. Names must be unique among
same-type siblings, while repeated names in different folders remain valid.

For table columns, **double-click the column header** to rename it, or
right-click the header or a cell and choose **Rename column**. The clicked
header is the target even when another cell was previously active. This works
both on a sheet alone and while editing one of its graphs.
Internal identifiers containing units such as `Voltage (V)`
are preserved exactly, and every graph backed by that sheet has its X/Y,
error-column, and legend-source references remapped together. Editing or
pasting the table's `Name` row changes the display/series label across all
graphs that share the sheet; it does not change the internal column ID.

Project-item names, graph titles, and the JSON filename are intentionally
independent, so rename the title or file separately when those should change.

## Inserting rows and columns

Right-click a cell to choose **Insert row above**, **Insert row below**,
**Insert column left**, or **Insert column right**. Row headers also offer
above/below insertion; column headers offer left/right insertion. Placement
is relative to the clicked row or column, and the inserted row or column
becomes the current selection.

The `Role` and `Name` rows always stay at the top. Inserting a row next to
either metadata row adds the first data row underneath them. Existing cell
values and graph column references are preserved. Header renames and all
four insertion directions support undo and redo.

## Per-point error bars

Add a table column containing one Y-error magnitude per data row, then select
the plotted series and open **Series style → Error bars**. Choose that column
under **Y error column (±)**. Each finite numeric cell draws a symmetric error
bar around the scatter point in the same row; leave a cell blank when that
point should have no error bar. Negative magnitudes are treated as absolute
values.

The error bars inherit the series color and alpha and remain centered after a
visual Y offset. Divisors affect tick labels only, leaving error-bar geometry unchanged. Set the error
column's table Role to blank if it should not also appear as its own plotted Y
series. Error bars on broken axes are not currently supported.

## Linear fitting

Select a plotted Y series, open **Series style → Linear fit**, and enable the
fit to add an ordinary least-squares `y = mx + b` overlay. Optional inclusive
X minimum/maximum fields limit the points used; leave either field blank to
use all valid data in that direction. The result panel reports the equation,
R², and the number of fitted points.

Fits use finite numeric X/Y pairs, require at least two distinct X values, and
are currently available on linear numeric axes. Coefficients and fit ranges use
original data units, unaffected by divisors, while visual Y offsets are excluded from the
calculation and applied only to the overlay. Fit lines inherit their series
color and alpha, can use a separate line style/width, and are intentionally
left out of the legend.

## Free-form legends

The legend editor uses an Origin-style text format instead of forcing one
table row per plotted series:

- `\L(1)` inserts the marker/line sample from plot 1.
- `%(1)` inserts plot 1's current display name.
- Any other text is kept literally, including text-only headings or notes.
- A newline starts a new legend row; a Tab adds another entry to the same row.
  A subsequent valid `\L(n)` after spaces is also accepted as a same-row entry.

For example, `\L(2) Control` reuses plot 2's visual sample with a custom name,
while a line containing only `Measurements` creates a text-only heading. In
manual mode the edited content is authoritative: omitted plots are not added
back automatically. **Reset to automatic** rebuilds the legend from each
series' **Show in legend** setting. Text-only entries start at the legend's
left edge instead of reserving an empty sample column. Place the cursor in an
entry to set its font, size, bold/italic style, or color; each Tab-separated
entry can have its own formatting.

## Existing project migration

No manual JSON conversion is required. `project_io` accepts valid legacy
schema v1 and v2 projects as well as current schema v3 projects, repairs their
model/tree relationships, and normalizes them in memory. The next save writes
the current v3 format. Keeping a backup before overwriting an important legacy
project is still recommended.

Source integrations can migrate incrementally because the root compatibility
imports and `python main.py` launcher remain available. Prefer package imports
for all new work.

## Large-project performance

The JSON-compatible edition keeps the existing schema while avoiding the main
sources of UI stalls in large scientific projects:

- fully empty trailing spreadsheet rows are removed on import, load, and save;
- project JSON is written compactly with a vectorized DataFrame codec;
- periodic autosave snapshots use pandas Copy-on-Write and are encoded and
  written on a background thread; each window owns a separate recovery file,
  and recovery verifies that its original project has not changed before a
  normal Save may replace it;
- undo snapshots share unchanged DataFrame buffers and charge the memory budget
  only for buffers retained exclusively by history; and
- interactive previews cache numeric conversion, filter explicit X ranges,
  and use an extrema-preserving point envelope.

Preview reduction never changes a project or its analytical result. Linear
fits use every finite source point, and clipboard/image export and batch export
always render at full resolution. Editing table data invalidates the numeric
cache immediately.

Legacy JSON still has to be parsed eagerly when it is opened. For projects in
which even that initial load is too large, use the sibling `pubfig_v4` edition;
its `.pubfig` project container keeps sheet data in per-sheet Parquet members
and loads sheets on demand.

### Supplied HfO2 workload diagnosis

The investigated `0828_flash_simulation/graph_project.json` is 222,366,281
bytes and contains 8 sheets, 12 graphs, 1,273,397 serialized row arrays, and
9,129,276 cells. One sheet was exported at Excel's full 1,048,576-row limit,
although its final 848,573 rows were completely empty. Removing only that
empty tail cuts 74,674,424 bytes from the indented source representation and
reduces that sheet's row-wise plotting work by 80.9%, without changing its
plotted points.

With the compact JSON writer, the optimized current-edition save measured
63,752,594 bytes for this workload. Initial legacy JSON parsing is still an
eager operation and measured roughly 1.5 GB peak process memory in the test
environment; compact output cannot eliminate the millions of temporary
Python strings and lists created while reading JSON. The separate v4 edition
addresses that remaining format-level cost with manifest-first lazy loading.

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
- Per-point symmetric Y error bars, dual Y axes, broken axes, log scales, and custom ticks
- Fixed tick-label decimal places: in Figure → Axes, set X/Y/Y2 tick decimals
  to Auto or 0–12 (for example, 2 displays `1.00`). Each axis is independent;
  the setting is saved with the graph and used for preview and export.
- Figure → Axes → X/Y/Y2 number format chooses Auto, Plain numbers (`1000000`),
  Scientific — each tick (`1×10⁶`, `2×10⁶`), or Scientific — shared exponent
  (ticks `1`, `2` with `×10⁶` at the axis edge). In scientific modes, tick
  decimals controls the coefficient's precision. Formatting applies after
  the divisor and is preserved in project files and exports.
- X/Y/Y2 divisor divides tick labels only: `1000000` or `1e6` labels one million
  as 1; `0.001` labels seconds as milliseconds. Curves, limits, tick positions,
  fits, and error bars stay in original data units. Enter `1` to reset;
  incomplete or invalid input retains the last valid divisor and is highlighted.
- Per-series linear least-squares fits with optional X ranges and R² results
- Per-series colors, 9×12 filled/open/partially filled marker palette, alpha, offsets,
  gradients, and free-form legends
- Text, textbox, line, arrow, and rectangle annotations
- Journal presets and exact physical canvas/plot dimensions
- PNG, PDF, SVG, TIFF, and EPS export
- Shift-click or Ctrl/Cmd-click Graph entries in Project Explorer, then use
  Export selected figures to save only those graphs as PNG files. Sheet/folder
  selections do not include unselected graphs; existing files are not overwritten.
- Multi-figure projects, autosave, crash recovery, undo, and redo
