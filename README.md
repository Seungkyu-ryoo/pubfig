# pubfig

PySide6 + Matplotlib GUI for making publication-style 2D figures from copied table data.

## Features

- Paste data from Excel, Origin, Google Sheets, or CSV text
- Edit cells directly like a small spreadsheet
- Copy/paste selected cells with `Ctrl+C` / `Ctrl+V`
- Add, delete, clear, and rename rows/columns from the table context menu
- Origin-style column roles: set the `Role` row to `X` or `Y`
- Each Y column uses the nearest X column to its left
- The `Name` row is used for legend labels
- Auto-detect tab, comma, semicolon, and whitespace separated data
- Select which Y columns to plot from the `Plot Y` list
- Per-series color, label, plot type, marker, line width, and marker size
- Edit one series style without changing which series are plotted
- Assign each series to the left or right Y axis for double-axis plots
- Change the Y2 axis color independently from the left axis
- Plot types: line, scatter, line+marker, bar, step, area, and stem
- Inline plot type descriptions in the Series style panel
- Series style controls are split into `Style` and `Colormap` tabs
- Apply continuous Matplotlib colormaps to all plotted series with stretch start/end
- Apply a per-series Y offset for stacked curves
- Add text, textbox, line, arrow, and rectangle box annotations in axes/screen coordinates
- Drag existing annotations on the plot to reposition them
- Select multiple annotations with `Ctrl`/`Shift` in the annotation list and drag one selected item to move them together
- Resize and rotate the selected annotation with on-plot handles
- Hold `Ctrl` while dragging to constrain annotation movement or resizing to horizontal/vertical directions
- Adjust arrow head size from the annotation Style tab
- Delete the selected annotation with `Delete` or `Backspace`
- Undo workspace edits with `Ctrl+Z`
- Copy/paste the selected annotation with `Ctrl+C` / `Ctrl+V`
- Select an annotation and edit its text, position, font size, fill, and color
- Annotation `X/Y` use axes fraction coordinates. `Width/Height` use a screen-scaled axes unit, so equal width and height look equal on screen.
- Arrow geometry uses `X/Y + Width/Height`; the arrow tip is `X + Width`, `Y + Height` in that screen-scaled unit.
- Arrow picking uses a wider invisible hit box, so it is easier to drag
- Annotation arrows, boxes, and textboxes support multiple line styles plus alpha transparency
- Plot text supports subscript/superscript/overbar shorthand such as `TiO_2`, `cm^2`, `10^{-3}`, and `bar{1}`
- Special character palette for annotation text, axis labels, titles, series labels, and column Name cells
- Hide X/Y/Y2 tick number labels from the Axes tab
- Fix the Y label gap from the plot box so y-axis titles align across figures with different tick-label widths
- Leave tick intervals on auto or set custom X/Y/Y2 major tick spacing
- Format legend/column names with subscript, superscript, and overbar shorthand such as `LaNiO_3`, `cm^2`, and `bar{1}`
- Lock the plot box width/height ratio or apply common plot ratio presets without changing the canvas size
- Drag the visible legend directly on the preview to reposition it
- Journal/generic figure presets in mm
- Add extra figure padding around the plot without changing the default zero-padding layout
- Optionally fix the physical plot area so hidden tick labels do not resize the axes box
- Control title, labels, font sizes, axis scale, limits, grid, and legend
- Export PNG, PDF, SVG, TIFF, and EPS
- Exact-size export by default; optional whitespace trimming
- Save/load project JSON with data and plot settings
- Manage multiple independent figures in one project from the Project Explorer
- Copy the current figure style and apply it to another figure, optionally including annotations

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

If `python` opens the Microsoft Store launcher on Windows, use:

```powershell
py main.py
```

The app opens with an editable blank table. Paste copied table data into the left table with `Ctrl+V`, edit cells directly, and right-click the table to add/delete/rename rows or columns.

Use `Project Explorer` to create, duplicate, rename, delete, and switch between independent figures. Each figure keeps its own table data, plotted series, figure settings, and annotations.

Use the first two rows as metadata:

- `Role`: type `X`, `Y`, or leave blank. A Y column is plotted against the nearest X column on its left.
- `Name`: legend label for that column. If empty, the column header is used.

`Plot Y` checkboxes control which series are drawn. `Edit series` controls which plotted series receives manual style changes, so the full plot stays visible while colors and line styles are adjusted.

Plot type quick guide:

- `step`: stair-step trace for binned, digital, or piecewise-constant data.
- `area`: filled area under a curve for cumulative or contribution-style plots.
- `stem`: vertical sticks from a baseline for peaks, impulses, or sparse events.

Figure settings are grouped into `Size`, `Padding`, `Layout`, `Labels`, `Axes`, and `Style` tabs.
Use `Figure > Style > Y2 axis color` to recolor the right Y axis label, tick labels, tick marks, and right spine.
The `Padding` tab adds extra white canvas around the normal figure area. Leave all padding values at `0` for the previous behavior.
The `Size` tab separates `Canvas width/height` from `Plot width/height`. Enable `Layout > Lock plot box size` to make `Plot width/height` the physical size of the axes box inside the exported canvas. This is useful when stacking figures where one plot hides X tick labels but must keep the same data-area size as another plot. If separate images will be resized by width in PowerPoint, keep their canvas widths the same too; otherwise the wider canvas will be scaled down and its plot box will look shorter.
Use `Size > Plot ratio preset` to apply a common width:height ratio to the plot box only. Enable `Size > Lock plot ratio` to preserve that plot-box ratio while changing either plot width or plot height; canvas size is not changed automatically.
Use `Labels > Y label gap (mm)` to keep the visible gap between the Y-axis title and the plot box fixed. This helps normal and broken-axis figures align even when one plot has wider tick labels such as `1000`.
Use `Layout > Center plot box` to center only the locked axes box inside the canvas. Use `Layout > Center content` to center the visible figure content, including labels, ticks, legend, and annotations. You can also `Alt`-drag the plot area to reposition it while preserving its physical plot width and height. Use `Layout > Fit canvas to content` after arranging a large working canvas; it keeps the plot box, fonts, line widths, marker sizes, and annotations unchanged, then updates only the canvas size and plot margins around the visible content.
In the `Axes` tab, leave tick interval fields blank for automatic ticks, or enter positive values to set custom major tick spacing for linear X/Y/Y2 axes. When Y or Y2 scale is `log`, Y/Y2 min and max are interpreted as base-10 exponents, so `-10` means `10^-10` and `-4` means `10^-4`.
Enable `Y broken axis` to split the left Y axis into lower and upper visible ranges, for example lower `0` to `2` and upper `75` to `85`. This is intended for linear left-Y plots where one or a few values are much larger than the rest; Y2 and log-Y plots fall back to the normal axis.

Annotations are drawn relative to the axes, not the data scale. `X/Y` are axes fraction coordinates where `0,0` is the lower-left plot area and `1,1` is the upper-right plot area. `Width/Height` use a screen-scaled axes unit, so a box with equal width and height appears square even when the X and Y data ranges are very different.
After adding an annotation, drag it directly on the plot to move it. Arrow annotations move the text and arrow tip together.
Use `Ctrl` or `Shift` in the annotation list to select multiple annotations, then drag one selected annotation on the plot to move the group together.
The selected annotation shows two small handles: drag the end handle to resize and the upper handle to rotate.
Hold `Ctrl` while dragging to lock movement and resize direction to horizontal or vertical. Hold `Ctrl` while rotating to snap to 90-degree increments.
Use `Arrow head size` in the annotation Style tab to resize arrow heads. Press `Delete` or `Backspace` to remove the selected annotation, and use `Ctrl+Z` to undo recent workspace edits.
With an annotation selected, `Ctrl+C` copies it and `Ctrl+V` pastes a duplicate with a small offset.
Selecting an annotation in the list, or clicking one on the plot, loads it into the annotation controls. Edits to the text, position, size, fill, and color update the selected annotation.
Annotation line style options include solid, dashed, dotted, dashdot, and loose/dense variants. Alpha controls transparency for annotation text, arrows, borders, and filled boxes.
Use underscore notation for subscripts, caret notation for superscripts, and `bar{...}` for crystallographic overbars: `TiO_2` renders the 2 as a subscript, `cm^2` renders 2 as a superscript, `10^{-3}` renders -3 as a superscript, and `bar{1}` renders 1 with an overbar. Existing Matplotlib math text wrapped in `$...$` is left unchanged.
Use the `Special characters` palette to insert Greek letters, math/unit symbols, arrows, and simple shapes into the last selected annotation, title, axis label, series label text field, or spreadsheet column `Name` cell.
New annotations are placed inside the plot area by default, and annotations are excluded from layout/autoscale so they do not change the plot axis limits or panel size.

The preview has a `Fit preview` mode and a manual `Zoom %` control. These only affect the on-screen preview, not the exported figure size.
When the legend is visible, drag it directly on the preview to reposition it. Hold `Ctrl` while dragging to constrain the movement horizontally or vertically.

`Style copy` copies the current figure styling for reuse on another figure. It applies figure size, padding, font sizes, axes visibility, grid/legend style, and per-series style by plotted-series order while preserving the destination figure's data labels and axis limits. Enable `Include annotations` to copy annotation objects too.
Use `Series style > Style > Y offset` for a custom offset on one plotted series. `Figure > Style > Y offset` remains the automatic step offset applied by plotted-series order; the two offsets are added together. Press `Ctrl+S` to save the current project directly after it has a file path; a new unsaved project asks for a JSON path the first time.

## Notes

Journal presets are helper defaults, not a substitute for checking the final submission guidelines. Exact-size export is the default because `bbox_inches="tight"` can change the final physical figure size. Enable `Trim whitespace` only when that behavior is desired.
