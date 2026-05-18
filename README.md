# pubfig

PySide6 + Matplotlib GUI for making publication-style 2D figures from copied table data.

## Features

**Data input**
- Paste from Excel, Origin, Google Sheets, or CSV — tab/comma/semicolon auto-detected
- Edit cells directly; right-click to add, delete, or rename rows/columns
- Origin-style `Role` (X/Y) and `Name` (legend label) header rows

**Series styling**
- Per-series color, alpha, label, plot type, marker, line width, Y offset
- Plot types: line, scatter, line+marker, bar, step, area, stem
- Left/right dual Y axis with independent color
- Apply Matplotlib colormaps or alpha-only gradients to selected series
- Hide individual series from the legend

**Annotations**
- Text, textbox, line, arrow, and rectangle — drag, resize, rotate on the preview
- Subscript/superscript/overbar shorthand (`TiO_2`, `cm^2`, `bar{1}`)
- Greek letters and symbol palette

**Figure control**
- Journal presets (Nature, ACS, Physical Review) in exact mm
- Separate canvas size vs. plot box size; lockable aspect ratio
- Broken X/Y axis, log scale, custom major tick intervals, and per-axis minor tick divisions
- Publication-oriented default colors and recommended discrete palettes for multi-series plots
- Draggable legend; copy style across figures

**Export**
- PNG, PDF, SVG, TIFF, EPS at exact physical size
- Save/load project JSON; multi-figure Project Explorer
- Unsaved-change prompt on exit

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

On Windows, if `python` opens the Microsoft Store, use `py main.py` instead.
