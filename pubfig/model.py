"""Pure project model and tree helpers for pubfig.

This module intentionally has no Qt dependency.  The UI may keep transient
widget state, but the objects below are the canonical persisted project state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Literal
from uuid import uuid4

import pandas as pd

from .plot_config import AnnotationConfig, PlotConfig, SeriesConfig


NodeType = Literal["folder", "sheet", "graph"]
NODE_TYPES: frozenset[str] = frozenset({"folder", "sheet", "graph"})


def new_id(prefix: str) -> str:
    """Return a compact, process-independent id using the legacy id shape."""

    return f"{prefix}{uuid4().hex[:8]}"


@dataclass
class Sheet:
    """One editable data table that may feed any number of graphs."""

    id: str
    name: str
    df: pd.DataFrame


@dataclass
class Graph:
    """A plot definition that references exactly one :class:`Sheet`."""

    id: str
    name: str
    sheet_id: str
    plot_config: PlotConfig = field(default_factory=PlotConfig)
    series_by_y: dict[str, SeriesConfig] = field(default_factory=dict)
    checked_y: list[str] = field(default_factory=list)
    annotations: list[AnnotationConfig] = field(default_factory=list)


@dataclass
class TreeNode:
    """A node in the project explorer.

    Folders contain folders or sheets, sheets contain their graphs, and graph
    nodes are leaves.  ``ref_id`` points into ``ProjectDocument.sheets`` or
    ``ProjectDocument.graphs`` for the corresponding leaf type.
    """

    id: str
    type: NodeType
    name: str
    ref_id: str | None = None
    children: list["TreeNode"] = field(default_factory=list)
    expanded: bool = True

    def walk(self, *, include_self: bool = True) -> Iterator["TreeNode"]:
        return iter_tree(self, include_root=include_self)

    def find(self, node_id: str | None) -> "TreeNode | None":
        return find_node(self, node_id)

    def find_parent(self, node_id: str | None) -> "TreeNode | None":
        return find_parent(self, node_id)


def project_root(name: str = "Project") -> TreeNode:
    """Create an empty project root."""

    return TreeNode(id=new_id("nd"), type="folder", name=name)


def iter_tree(root: TreeNode, *, include_root: bool = True) -> Iterator[TreeNode]:
    """Yield nodes in stable pre-order, guarding against accidental cycles."""

    stack = [root]
    seen_objects: set[int] = set()
    first = True
    while stack:
        node = stack.pop()
        marker = id(node)
        if marker in seen_objects:
            continue
        seen_objects.add(marker)
        if include_root or not first:
            yield node
        first = False
        stack.extend(reversed(node.children))


def find_node(root: TreeNode, node_id: str | None) -> TreeNode | None:
    """Find the first tree node with ``node_id``."""

    if node_id is None:
        return None
    return next((node for node in iter_tree(root) if node.id == node_id), None)


def find_parent(root: TreeNode, node_id: str | None) -> TreeNode | None:
    """Find the parent of the first node with ``node_id``."""

    if node_id is None or root.id == node_id:
        return None
    seen_objects: set[int] = set()
    stack = [root]
    while stack:
        parent = stack.pop()
        marker = id(parent)
        if marker in seen_objects:
            continue
        seen_objects.add(marker)
        for child in parent.children:
            if child.id == node_id:
                return parent
        stack.extend(reversed(parent.children))
    return None


def first_leaf(root: TreeNode) -> TreeNode | None:
    """Return the first selectable sheet/graph node in display order."""

    return next(
        (node for node in iter_tree(root, include_root=False) if node.type in {"sheet", "graph"}),
        None,
    )


@dataclass(frozen=True)
class RepairReport:
    """Summary of changes made by :func:`repair_document`."""

    dropped_graph_ids: tuple[str, ...] = ()
    removed_tree_nodes: int = 0
    added_tree_nodes: int = 0
    reassigned_node_ids: int = 0


@dataclass
class ProjectDocument:
    """The complete, UI-independent pubfig project state."""

    sheets: dict[str, Sheet] = field(default_factory=dict)
    graphs: dict[str, Graph] = field(default_factory=dict)
    tree_root: TreeNode = field(default_factory=project_root)
    active_node_id: str | None = None

    @property
    def tree(self) -> TreeNode:
        """Alias matching the v3 JSON key."""

        return self.tree_root

    @tree.setter
    def tree(self, value: TreeNode) -> None:
        self.tree_root = value

    def iter_nodes(self, *, include_root: bool = True) -> Iterator[TreeNode]:
        return iter_tree(self.tree_root, include_root=include_root)

    def find_node(self, node_id: str | None) -> TreeNode | None:
        return find_node(self.tree_root, node_id)

    def find_parent(self, node_id: str | None) -> TreeNode | None:
        return find_parent(self.tree_root, node_id)

    def first_leaf(self) -> TreeNode | None:
        return first_leaf(self.tree_root)

    def repair(self) -> RepairReport:
        return repair_document(self)


def repair_document(document: ProjectDocument) -> RepairReport:
    """Restore the project model/tree invariants in place.

    The repair is deliberately loss-minimizing:

    * graphs whose backing sheet is gone are removed from the object model;
    * valid folder placement is retained;
    * valid sheet/graph nodes in the wrong place are re-homed;
    * missing nodes are recreated, duplicate references are removed;
    * graph children are always placed under the sheet they reference; and
    * node ids are made non-empty and unique.

    Walking all original descendants before rebuilding also fixes the legacy
    repair bug where invalid graph nodes below an otherwise-valid sheet were
    never visited.
    """

    dropped_graph_ids = tuple(
        graph_id
        for graph_id, graph in document.graphs.items()
        if graph.sheet_id not in document.sheets
    )
    for graph_id in dropped_graph_ids:
        del document.graphs[graph_id]

    root = document.tree_root
    original_nodes = list(iter_tree(root))
    original_objects = {id(node) for node in original_nodes}

    sheet_candidates: dict[str, TreeNode] = {}
    graph_candidates: dict[str, TreeNode] = {}
    for node in original_nodes:
        if node.type == "sheet" and node.ref_id in document.sheets:
            sheet_candidates.setdefault(node.ref_id, node)
        elif node.type == "graph" and node.ref_id in document.graphs:
            graph_candidates.setdefault(node.ref_id, node)

    used_objects: set[int] = set()
    used_node_ids: set[str] = set()
    placed_sheet_ids: set[str] = set()
    placed_graph_ids: set[str] = set()
    added_tree_nodes = 0
    reassigned_node_ids = 0

    def prepare_node_id(node: TreeNode) -> None:
        nonlocal reassigned_node_ids
        node_id = str(node.id or "")
        if not node_id or node_id in used_node_ids:
            node.id = new_id("nd")
            reassigned_node_ids += 1
        else:
            node.id = node_id
        used_node_ids.add(node.id)

    def prepare_graph_node(node: TreeNode, graph_id: str) -> None:
        graph = document.graphs[graph_id]
        used_objects.add(id(node))
        prepare_node_id(node)
        node.type = "graph"
        node.name = graph.name
        node.ref_id = graph_id
        node.children = []
        placed_graph_ids.add(graph_id)

    def prepare_sheet_node(node: TreeNode, sheet_id: str) -> None:
        sheet = document.sheets[sheet_id]
        used_objects.add(id(node))
        prepare_node_id(node)
        node.type = "sheet"
        node.name = sheet.name
        node.ref_id = sheet_id
        placed_sheet_ids.add(sheet_id)

        children: list[TreeNode] = []
        for child in list(node.children):
            graph_id = child.ref_id
            if (
                child.type == "graph"
                and graph_id in document.graphs
                and graph_id not in placed_graph_ids
                and document.graphs[graph_id].sheet_id == sheet_id
                and id(child) not in used_objects
            ):
                prepare_graph_node(child, graph_id)
                children.append(child)
        node.children = children

    def prepare_folder(node: TreeNode) -> None:
        used_objects.add(id(node))
        prepare_node_id(node)
        node.type = "folder"
        node.ref_id = None
        if not node.name:
            node.name = "Folder"

        children: list[TreeNode] = []
        for child in list(node.children):
            if id(child) in used_objects:
                continue
            if child.type == "folder":
                prepare_folder(child)
                children.append(child)
            elif (
                child.type == "sheet"
                and child.ref_id in document.sheets
                and child.ref_id not in placed_sheet_ids
            ):
                prepare_sheet_node(child, child.ref_id)
                children.append(child)
            # Graphs directly under a folder and all other invalid children are
            # re-homed or dropped below after the valid hierarchy is retained.
        node.children = children

    prepare_folder(root)
    if root.name == "Folder":
        root.name = "Project"

    sheet_nodes: dict[str, TreeNode] = {
        node.ref_id: node
        for node in iter_tree(root)
        if node.type == "sheet" and node.ref_id is not None
    }
    for sheet_id, sheet in document.sheets.items():
        if sheet_id in placed_sheet_ids:
            continue
        candidate = sheet_candidates.get(sheet_id)
        if candidate is None or id(candidate) in used_objects:
            candidate = TreeNode(
                id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet_id
            )
            added_tree_nodes += 1
        prepare_sheet_node(candidate, sheet_id)
        root.children.append(candidate)
        sheet_nodes[sheet_id] = candidate

    # Refresh because prepare_sheet_node may have retained sheet nodes above.
    sheet_nodes.update(
        {
            node.ref_id: node
            for node in iter_tree(root)
            if node.type == "sheet" and node.ref_id is not None
        }
    )
    for graph_id, graph in document.graphs.items():
        if graph_id in placed_graph_ids:
            continue
        candidate = graph_candidates.get(graph_id)
        if candidate is None or id(candidate) in used_objects:
            candidate = TreeNode(
                id=new_id("nd"), type="graph", name=graph.name, ref_id=graph_id
            )
            added_tree_nodes += 1
        prepare_graph_node(candidate, graph_id)
        sheet_nodes[graph.sheet_id].children.append(candidate)

    final_objects = {id(node) for node in iter_tree(root)}
    removed_tree_nodes = len(original_objects - final_objects)

    if find_node(root, document.active_node_id) is None:
        first = first_leaf(root)
        document.active_node_id = first.id if first is not None else root.id

    return RepairReport(
        dropped_graph_ids=dropped_graph_ids,
        removed_tree_nodes=removed_tree_nodes,
        added_tree_nodes=added_tree_nodes,
        reassigned_node_ids=reassigned_node_ids,
    )


def repair_model(
    sheets: dict[str, Sheet],
    graphs: dict[str, Graph],
    tree_root: TreeNode,
) -> RepairReport:
    """Compatibility wrapper for callers that keep the three model parts."""

    return repair_document(
        ProjectDocument(sheets=sheets, graphs=graphs, tree_root=tree_root)
    )


__all__ = [
    "Graph",
    "NODE_TYPES",
    "NodeType",
    "ProjectDocument",
    "RepairReport",
    "Sheet",
    "TreeNode",
    "find_node",
    "find_parent",
    "first_leaf",
    "iter_tree",
    "new_id",
    "project_root",
    "repair_document",
    "repair_model",
]
