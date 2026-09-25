"""Canonical document and active editing context, independent of Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .model import Graph, ProjectDocument, Sheet, TreeNode
from .plot_config import AnnotationConfig, PlotConfig, SeriesConfig


@dataclass
class EditorSession:
    """Resolve active data from the document instead of keeping editor copies.

    Detached values are used only when no sheet/graph is active. Widget edits
    are committed by the workspace/table controllers before history or save
    captures a document. Replacing a sheet buffer or graph config therefore
    cannot leave a second, stale copy in the main window.
    """

    document: ProjectDocument = field(default_factory=ProjectDocument)
    active_sheet_id: str | None = None
    active_graph_id: str | None = None
    loading_project_figure: bool = False
    modified: bool = False
    _empty_df: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    _draft_config: PlotConfig = field(default_factory=PlotConfig, repr=False)
    _draft_series: dict[str, SeriesConfig] = field(default_factory=dict, repr=False)

    @property
    def sheet(self) -> Sheet | None:
        return self.document.sheets.get(self.active_sheet_id)

    @property
    def graph(self) -> Graph | None:
        return self.document.graphs.get(self.active_graph_id)

    @property
    def df(self) -> pd.DataFrame:
        sheet = self.sheet
        return sheet.df if sheet is not None else self._empty_df

    @df.setter
    def df(self, value: pd.DataFrame) -> None:
        sheet = self.sheet
        if sheet is not None:
            sheet.df = value
        else:
            self._empty_df = value

    @property
    def plot_config(self) -> PlotConfig:
        graph = self.graph
        return graph.plot_config if graph is not None else self._draft_config

    @plot_config.setter
    def plot_config(self, value: PlotConfig) -> None:
        graph = self.graph
        if graph is not None:
            graph.plot_config = value
        else:
            self._draft_config = value

    @property
    def annotations(self) -> list[AnnotationConfig]:
        if self.plot_config.annotations is None:
            self.plot_config.annotations = []
        return self.plot_config.annotations

    @annotations.setter
    def annotations(self, value: list[AnnotationConfig]) -> None:
        self.plot_config.annotations = value

    @property
    def series_by_y(self) -> dict[str, SeriesConfig]:
        graph = self.graph
        return graph.series_by_y if graph is not None else self._draft_series

    @series_by_y.setter
    def series_by_y(self, value: dict[str, SeriesConfig]) -> None:
        graph = self.graph
        if graph is not None:
            graph.series_by_y = value
        else:
            self._draft_series = value

    @property
    def sheets(self) -> dict[str, Sheet]:
        return self.document.sheets

    @sheets.setter
    def sheets(self, value: dict[str, Sheet]) -> None:
        self.document.sheets = value

    @property
    def graphs(self) -> dict[str, Graph]:
        return self.document.graphs

    @graphs.setter
    def graphs(self, value: dict[str, Graph]) -> None:
        self.document.graphs = value

    @property
    def tree_root(self) -> TreeNode:
        return self.document.tree_root

    @tree_root.setter
    def tree_root(self, value: TreeNode) -> None:
        self.document.tree_root = value

    @property
    def active_node_id(self) -> str | None:
        return self.document.active_node_id

    @active_node_id.setter
    def active_node_id(self, value: str | None) -> None:
        self.document.active_node_id = value

    def select_sheet(self, sheet_id: str | None) -> None:
        """Leave the old graph before loading controls for another context."""
        self.active_graph_id = None
        self.active_sheet_id = sheet_id
        self._draft_config = PlotConfig()
        self._draft_series = {}

    def replace_document(self, document: ProjectDocument) -> None:
        self.select_sheet(None)
        self._empty_df = pd.DataFrame()
        self.document = document
