"""Backward-compatible GUI imports.

New code should import from :mod:`pubfig.ui`; this façade keeps existing
scripts and third-party imports working while the implementation stays split.
"""

from PySide6.QtWidgets import QInputDialog

from pubfig.model import Graph, ProjectDocument, Sheet, TreeNode, new_id
from pubfig.ui.main_window import GraphDrawerWindow
from pubfig.ui.widgets import (
    AnnotationTextEdit,
    GradientEditorWidget,
    LegendEditorDialog,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    ProjectTreeWidget,
)

__all__ = [
    "AnnotationTextEdit",
    "GradientEditorWidget",
    "Graph",
    "GraphDrawerWindow",
    "LegendEditorDialog",
    "NoWheelComboBox",
    "NoWheelDoubleSpinBox",
    "NoWheelSpinBox",
    "ProjectDocument",
    "ProjectTreeWidget",
    "QInputDialog",
    "Sheet",
    "TreeNode",
    "new_id",
]
