"""Project explorer commands and tree presentation."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import TYPE_CHECKING

from PySide6.QtCore import QItemSelectionModel, QObject, Qt
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QTreeWidgetItem

from ..model import Graph, Sheet, TreeNode, find_node, find_parent, first_leaf, new_id
from ..plot_config import PlotConfig, default_series

if TYPE_CHECKING:
    from .main_window import GraphDrawerWindow


class ProjectTreeController(QObject):
    """Project explorer commands and tree presentation."""

    def __init__(self, window: GraphDrawerWindow) -> None:
        super().__init__(window)
        self.window = window
        self.session = window.session
        self._tree_items: dict[str, "QTreeWidgetItem"] = {}

    def _find_node(
        self, node_id: str | None, root: TreeNode | None = None
    ) -> TreeNode | None:
        return find_node(root or self.session.tree_root, node_id)

    def _find_parent(
        self, node_id: str, root: TreeNode | None = None
    ) -> TreeNode | None:
        return find_parent(root or self.session.tree_root, node_id)

    def _first_leaf(self, root: TreeNode | None = None) -> TreeNode | None:
        return first_leaf(root or self.session.tree_root)

    def active_node(self) -> TreeNode | None:
        return self._find_node(self.session.active_node_id)

    def current_sheet_id(self) -> str | None:
        """The sheet whose data the center table should show for the current
        selection: the sheet itself, or a graph's referenced sheet."""
        node = self.active_node()
        if node is None:
            return None
        if node.type == "sheet":
            return node.ref_id
        if node.type == "graph":
            graph = self.session.graphs.get(node.ref_id)
            return graph.sheet_id if graph else None
        return None

    def _target_folder_for_new(self) -> TreeNode:
        """Where a new sheet/folder should land: the selected folder, or the
        folder containing the selected leaf, else the root. Graph nodes are
        never folders, so their containing folder is used."""
        node = self.active_node()
        if node is None:
            return self.session.tree_root
        if node.type == "folder":
            return node
        # For a sheet/graph leaf, climb to the nearest folder ancestor.
        return self._containing_folder(node)

    def _containing_folder(self, node: TreeNode) -> TreeNode:
        """The nearest folder (or root) that should contain a sibling of node."""
        parent = self._find_parent(node.id)
        while parent is not None and parent.type not in ("folder",):
            # parent is a sheet (graph's parent) -> go one level up
            parent = self._find_parent(parent.id)
        return parent or self.session.tree_root

    def _sheet_node_for(self, sheet_id: str) -> TreeNode | None:
        """The tree node of type 'sheet' that backs the given sheet id."""

        def walk(node: TreeNode) -> TreeNode | None:
            for child in node.children:
                if child.type == "sheet" and child.ref_id == sheet_id:
                    return child
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(self.session.tree_root)

    def refresh_tree(self, *, preserve_selection: bool = True) -> None:
        selected_ids = {
            item.data(0, Qt.UserRole) for item in self.window.figure_tree.selectedItems()
        } if preserve_selection else set()
        self.window.figure_tree.blockSignals(True)
        self.window.figure_tree.clear()
        self._tree_items = {}
        for child in self.session.tree_root.children:
            self._add_tree_item(child, self.window.figure_tree.invisibleRootItem())
        active_item = self._tree_items.get(self.session.active_node_id)
        if active_item is not None:
            self.window.figure_tree.setCurrentItem(active_item, 0, QItemSelectionModel.NoUpdate)
        selected_items = [self._tree_items[node_id] for node_id in selected_ids if node_id in self._tree_items]
        for item in selected_items or ([active_item] if active_item is not None else []):
            item.setSelected(True)
        self.window.figure_tree.blockSignals(False)
        self.update_project_move_buttons()
        self.window.preview.update_export_selection()

    def _node_label(self, node: TreeNode) -> str:
        prefix = {"folder": "[F] ", "sheet": "[S] ", "graph": "[G] "}.get(node.type, "")
        if node.type == "sheet":
            name = (
                self.session.sheets[node.ref_id].name
                if node.ref_id in self.session.sheets
                else node.name
            )
        elif node.type == "graph":
            name = (
                self.session.graphs[node.ref_id].name
                if node.ref_id in self.session.graphs
                else node.name
            )
        else:
            name = node.name
        return f"{prefix}{name}"

    def _add_tree_item(self, node: TreeNode, parent_item) -> None:
        item = QTreeWidgetItem(parent_item)
        item.setText(0, self._node_label(node))
        item.setData(0, Qt.UserRole, node.id)
        self._tree_items[node.id] = item
        for child in node.children:
            self._add_tree_item(child, item)
        if node.children:
            item.setExpanded(node.expanded)

    def _on_item_expanded(self, item) -> None:
        node = self._find_node(item.data(0, Qt.UserRole))
        if node is not None:
            node.expanded = True

    def _on_item_collapsed(self, item) -> None:
        node = self._find_node(item.data(0, Qt.UserRole))
        if node is not None:
            node.expanded = False

    def update_project_move_buttons(self) -> None:
        node = self.active_node()
        enabled = False
        if node is not None:
            parent = self._find_parent(node.id)
            enabled = parent is not None and len(parent.children) > 1
        self.window.move_figure_up_btn.setEnabled(enabled)
        self.window.move_figure_down_btn.setEnabled(enabled)

    def on_tree_selection_changed(self, current, previous) -> None:
        if self.session.loading_project_figure or current is None:
            self.update_project_move_buttons()
            return
        node_id = current.data(0, Qt.UserRole)
        if node_id == self.session.active_node_id:
            self.update_project_move_buttons()
            return
        self.window.workspace.save_active_state()
        node = self._find_node(node_id)
        if node is None:
            return
        self.session.active_node_id = node_id
        self.window.workspace.load_node(node)
        self.window.workspace.update_undo_baseline()
        self.update_project_move_buttons()
        self.window.set_status(f"Selected {self._node_label(node).strip()}.")

    def _select_node(self, node_id: str) -> None:
        self.session.active_node_id = node_id
        self.refresh_tree(preserve_selection=False)
        node = self._find_node(node_id)
        if node is not None:
            self.window.workspace.load_node(node)

    def new_sheet(self) -> None:
        self.window.workspace.push_current_undo_state()
        self.window.workspace.save_active_state()
        sheet = Sheet(
            id=new_id("sh"),
            name=self._unique_sheet_name(),
            df=self.window.workspace.blank_dataframe(),
        )
        self.session.sheets[sheet.id] = sheet
        node = TreeNode(id=new_id("nd"), type="sheet", name=sheet.name, ref_id=sheet.id)
        self._target_folder_for_new().children.append(node)
        self._select_node(node.id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Created sheet {sheet.name}.")

    def new_graph(self) -> None:
        sheet_id = self.current_sheet_id()
        if sheet_id is None or sheet_id not in self.session.sheets:
            QMessageBox.information(
                self.window, "New graph", "Select a sheet (or a graph) first."
            )
            return
        self.window.workspace.push_current_undo_state()
        self.window.workspace.save_active_state()
        y_columns = [
            column
            for column in map(str, self.session.df.columns)
            if self.window.table_editor._column_role(column) == "Y"
            and self.window.table_editor._nearest_left_x(column)
        ]
        series_by_y = {
            column: default_series(
                self.window.table_editor._nearest_left_x(column), column, index
            )
            for index, column in enumerate(y_columns)
        }
        graph = Graph(
            id=new_id("gr"),
            name=self._unique_graph_name(),
            sheet_id=sheet_id,
            plot_config=PlotConfig(),
            series_by_y=series_by_y,
            checked_y=y_columns,
            annotations=[],
        )
        self.session.graphs[graph.id] = graph
        node = TreeNode(id=new_id("nd"), type="graph", name=graph.name, ref_id=graph.id)
        # A graph always lives under the sheet node it references (hierarchy).
        sheet_node = self._sheet_node_for(sheet_id)
        if sheet_node is None:
            self._target_folder_for_new().children.append(node)
        else:
            sheet_node.expanded = True
            sheet_node.children.append(node)
        self._select_node(node.id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(
            f"Created graph {graph.name} on {self.session.sheets[sheet_id].name}."
        )

    def new_folder(self) -> None:
        self.window.workspace.push_current_undo_state()
        self.window.workspace.save_active_state()
        node = TreeNode(id=new_id("nd"), type="folder", name=self._unique_folder_name())
        self._target_folder_for_new().children.append(node)
        self._select_node(node.id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Created folder {node.name}.")

    def duplicate_selected_node(self) -> None:
        node = self.active_node()
        if node is None:
            return
        self.window.workspace.push_current_undo_state()
        self.window.workspace.save_active_state()
        parent = self._find_parent(node.id) or self.session.tree_root
        clone = self._duplicate_node(node)
        parent.children.insert(parent.children.index(node) + 1, clone)
        self._select_node(clone.id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Duplicated selection.")

    def _duplicate_node(self, node: TreeNode) -> TreeNode:
        """Deep-copy a tree node and the objects it references. For folders, the
        whole subtree is copied and graph→sheet references are remapped so a
        duplicated graph points at the duplicated sheet (when both are copied)."""
        sheet_id_map: dict[str, str] = {}

        def clone(n: TreeNode) -> TreeNode:
            if n.type == "sheet":
                src = self.session.sheets[n.ref_id]
                new_sheet = Sheet(
                    id=new_id("sh"),
                    name=self._unique_sheet_name(src.name),
                    df=src.df.copy(deep=True),
                )
                self.session.sheets[new_sheet.id] = new_sheet
                sheet_id_map[src.id] = new_sheet.id
                node_copy = TreeNode(
                    id=new_id("nd"),
                    type="sheet",
                    name=new_sheet.name,
                    ref_id=new_sheet.id,
                    expanded=n.expanded,
                )
                # Clone the sheet's child graphs so duplicates point at the new sheet.
                node_copy.children = [clone(child) for child in n.children]
                return node_copy
            if n.type == "graph":
                src = self.session.graphs[n.ref_id]
                new_g = deepcopy(src)
                new_g.id = new_id("gr")
                new_g.name = self._unique_graph_name(src.name)
                new_g.sheet_id = sheet_id_map.get(src.sheet_id, src.sheet_id)
                self.session.graphs[new_g.id] = new_g
                return TreeNode(
                    id=new_id("nd"), type="graph", name=new_g.name, ref_id=new_g.id
                )
            # folder: clone children first so sheet ids are available for remap
            folder = TreeNode(
                id=new_id("nd"),
                type="folder",
                name=self._unique_folder_name(n.name),
                expanded=n.expanded,
            )
            folder.children = [clone(child) for child in n.children]
            return folder

        return clone(node)

    def _node_name(self, node: TreeNode) -> str:
        if node.type == "sheet":
            return (
                self.session.sheets[node.ref_id].name
                if node.ref_id in self.session.sheets
                else node.name
            )
        if node.type == "graph":
            return (
                self.session.graphs[node.ref_id].name
                if node.ref_id in self.session.graphs
                else node.name
            )
        return node.name

    @staticmethod
    def _node_name_key(name: str) -> str:
        return unicodedata.normalize("NFC", str(name).strip()).casefold()

    def _sibling_name_collision(self, node: TreeNode, name: str) -> TreeNode | None:
        parent = self._find_parent(node.id)
        if parent is None:
            return None
        requested = self._node_name_key(name)
        return next(
            (
                sibling
                for sibling in parent.children
                if sibling is not node
                and sibling.type == node.type
                and self._node_name_key(self._node_name(sibling)) == requested
            ),
            None,
        )

    def rename_node(self, node_id: str) -> None:
        node = self._find_node(node_id)
        if node is None:
            return
        current = self._node_name(node)
        title = "Rename project" if node is self.session.tree_root else "Rename"
        name, ok = QInputDialog.getText(self.window, title, "Name", text=current)
        if not ok:
            return
        name = unicodedata.normalize("NFC", str(name).strip())
        if not name:
            return
        if any(unicodedata.category(character) == "Cc" for character in name):
            QMessageBox.warning(
                self.window, title, "Names cannot contain control characters."
            )
            return
        if name == current and node.name == name:
            self.window.set_status("Name unchanged.")
            return
        collision = self._sibling_name_collision(node, name)
        if collision is not None:
            QMessageBox.warning(
                self.window,
                title,
                f"A {node.type} named '{self._node_name(collision)}' already exists here.",
            )
            return
        self.window.workspace.push_current_undo_state()
        if not self.session.document.rename_node(node.id, name):
            return
        self.refresh_tree()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Renamed to {name}.")

    def rename_selected_node(self) -> None:
        node = self.active_node()
        if node is not None:
            self.rename_node(node.id)

    def rename_project_root(self) -> None:
        self.rename_node(self.session.tree_root.id)

    def _graphs_referencing(self, sheet_id: str) -> list[str]:
        return [gid for gid, g in self.session.graphs.items() if g.sheet_id == sheet_id]

    def delete_selected_node(self) -> None:
        node = self.active_node()
        if node is None:
            return
        # Collect sheets that would be deleted, and guard against orphaning graphs.
        sheet_ids = self._collect_sheet_ids(node)
        if sheet_ids:
            dependents = [
                gid
                for sid in sheet_ids
                for gid in self._graphs_referencing(sid)
                if gid not in self._collect_graph_ids(node)
            ]
            if dependents:
                names = ", ".join(self.session.graphs[g].name for g in dependents)
                reply = QMessageBox.question(
                    self.window,
                    "Delete",
                    f"{len(dependents)} graph(s) reference this data and will also be deleted:\n{names}\n\nContinue?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return

        self.window.workspace.push_current_undo_state()
        self._remove_node_objects(node, also_dependents=True)
        parent = self._find_parent(node.id) or self.session.tree_root
        if node in parent.children:
            parent.children.remove(node)

        # Never leave an empty project.
        if not self.session.tree_root.children:
            self.session.sheets.clear()
            self.session.graphs.clear()
            self.session.active_node_id = self.window.workspace._seed_blank_sheet(
                self.session.sheets, self.session.tree_root
            )
        else:
            leaf = self._first_leaf() or self.session.tree_root.children[0]
            self.session.active_node_id = leaf.id

        self.session.active_graph_id = None
        self.session.active_sheet_id = None
        self._select_node(self.session.active_node_id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status("Deleted selection.")

    def _collect_sheet_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        if node.type == "sheet" and node.ref_id:
            ids.add(node.ref_id)
        for child in node.children:
            ids |= self._collect_sheet_ids(child)
        return ids

    def _collect_graph_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        if node.type == "graph" and node.ref_id:
            ids.add(node.ref_id)
        for child in node.children:
            ids |= self._collect_graph_ids(child)
        return ids

    def _remove_node_objects(self, node: TreeNode, also_dependents: bool) -> None:
        """Delete the Sheet/Graph objects backing a node subtree. When
        also_dependents, graphs referencing a deleted sheet are removed too."""
        sheet_ids = self._collect_sheet_ids(node)
        graph_ids = self._collect_graph_ids(node)
        for gid in graph_ids:
            self.session.graphs.pop(gid, None)
        for sid in sheet_ids:
            self.session.sheets.pop(sid, None)
        if also_dependents:
            for sid in sheet_ids:
                for gid in self._graphs_referencing(sid):
                    self.session.graphs.pop(gid, None)
                    self._remove_nodes_by_ref(self.session.tree_root, "graph", gid)

    def _remove_nodes_by_ref(self, root: TreeNode, node_type: str, ref_id: str) -> None:
        root.children = [
            c for c in root.children if not (c.type == node_type and c.ref_id == ref_id)
        ]
        for child in root.children:
            self._remove_nodes_by_ref(child, node_type, ref_id)

    def move_selected_node(self, offset: int) -> None:
        node = self.active_node()
        if node is None:
            return
        parent = self._find_parent(node.id)
        if parent is None:
            return
        idx = parent.children.index(node)
        target = idx + offset
        if target < 0 or target >= len(parent.children):
            self.update_project_move_buttons()
            return
        self.window.workspace.push_current_undo_state()
        parent.children[idx], parent.children[target] = (
            parent.children[target],
            parent.children[idx],
        )
        self.refresh_tree()
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Moved {'up' if offset < 0 else 'down'}.")

    def show_tree_menu(self, position) -> None:
        item = self.window.figure_tree.itemAt(position)
        if item is not None:
            self.window.figure_tree.setCurrentItem(item)
            node = self._find_node(item.data(0, Qt.UserRole))
        else:
            node = None
        menu = QMenu(self.window)
        menu.addAction("New Sheet", self.new_sheet)
        if node is not None and node.type in ("sheet", "graph"):
            menu.addAction("New Graph", self.new_graph)
        menu.addAction("New Folder", self.new_folder)
        if node is not None:
            menu.addSeparator()
            menu.addAction("Duplicate", self.duplicate_selected_node)
            menu.addAction("Rename", lambda node_id=node.id: self.rename_node(node_id))
            menu.addAction("Delete", self.delete_selected_node)
            move_menu = menu.addMenu("Move to folder")
            self._build_move_menu(move_menu, node)
        else:
            menu.addSeparator()
            menu.addAction("Rename Project", self.rename_project_root)
        menu.exec(self.window.figure_tree.viewport().mapToGlobal(position))

    def _build_move_menu(self, menu, node: TreeNode) -> None:
        """Populate a menu with every folder the node can be moved into."""
        descendants = {node.id} | self._collect_descendant_ids(node)

        def add_folder(folder: TreeNode, depth: int) -> None:
            if folder.id not in descendants:
                label = ("    " * depth) + (folder.name or "Project")
                menu.addAction(
                    label, lambda f=folder: self._move_node_to_folder(node, f)
                )
            for child in folder.children:
                if child.type == "folder":
                    add_folder(child, depth + 1)

        add_folder(self.session.tree_root, 0)

    def _collect_descendant_ids(self, node: TreeNode) -> set[str]:
        ids: set[str] = set()
        for child in node.children:
            ids.add(child.id)
            ids |= self._collect_descendant_ids(child)
        return ids

    def _move_node_to_folder(self, node: TreeNode, folder: TreeNode) -> None:
        selected_node_id = node.id
        # Rule A: a graph follows its sheet, so move the sheet instead.
        if node.type == "graph":
            sheet_id = (
                self.session.graphs[node.ref_id].sheet_id
                if node.ref_id in self.session.graphs
                else None
            )
            node = self._sheet_node_for(sheet_id) if sheet_id else None
            if node is None:
                return
        parent = self._find_parent(node.id)
        if parent is None or folder is parent:
            return
        self.window.workspace.push_current_undo_state()
        parent.children.remove(node)
        folder.children.append(node)
        self._show_moved_node(selected_node_id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Moved to {folder.name or 'Project'}.")

    def _show_moved_node(self, node_id: str) -> None:
        # Reveal the result even when the destination (or an ancestor) was
        # collapsed. Keep the dragged graph selected when its sheet moves.
        parent = self._find_parent(node_id)
        while parent is not None:
            parent.expanded = True
            parent = self._find_parent(parent.id)
        selection_changed = self.session.active_node_id != node_id
        self.session.active_node_id = node_id
        self.refresh_tree()
        self.window.figure_tree.scrollToItem(self._tree_items[node_id])
        if selection_changed:
            self.window.workspace.load_node(self._find_node(node_id))

    def handle_tree_drop(self, source_id: str, target_id: str, position: str) -> None:
        """Apply a drag-and-drop move to the model. Only sheets and folders move
        freely; a graph always stays under its sheet, so dragging a graph moves
        its parent sheet instead."""
        source = self._find_node(source_id)
        if source is None:
            return
        # Rule A: graphs follow their sheet. Redirect a graph drag to its sheet.
        if source.type == "graph":
            sheet_id = (
                self.session.graphs[source.ref_id].sheet_id
                if source.ref_id in self.session.graphs
                else None
            )
            source = self._sheet_node_for(sheet_id) if sheet_id else None
            if source is None:
                return

        # Determine destination folder + insertion index.
        if position == "root" or not target_id:
            dest_folder, insert_index = (
                self.session.tree_root,
                len(self.session.tree_root.children),
            )
        else:
            target = self._find_node(target_id)
            if target is None:
                return
            # Can't drop a folder into itself or its own descendants.
            if source.type == "folder" and target_id in (
                {source.id} | self._collect_descendant_ids(source)
            ):
                self.window.set_status("Cannot move a folder into itself.")
                return
            if position == "on" and target.type == "folder":
                dest_folder, insert_index = target, len(target.children)
            elif position == "on" and target.type == "sheet":
                # Dropping onto a sheet means "into the sheet's folder, next to it".
                dest_folder = self._containing_folder(target)
                insert_index = dest_folder.children.index(target) + 1
            else:
                # above/below a node: same folder as that node (graphs excluded as targets)
                anchor = (
                    target
                    if target.type != "graph"
                    else (
                        self._sheet_node_for(
                            self.session.graphs[target.ref_id].sheet_id
                        )
                        if target.ref_id in self.session.graphs
                        else None
                    )
                )
                if anchor is None:
                    return
                dest_folder = self._containing_folder(anchor)
                if anchor not in dest_folder.children:
                    return
                base = dest_folder.children.index(anchor)
                insert_index = base if position == "above" else base + 1

        src_parent = self._find_parent(source.id)
        if src_parent is None:
            return
        if dest_folder is src_parent and source in dest_folder.children:
            # Reorder within the same folder.
            old = dest_folder.children.index(source)
            if old < insert_index:
                insert_index -= 1
            if old == insert_index:
                return
        self.window.workspace.push_current_undo_state()
        src_parent.children.remove(source)
        insert_index = max(0, min(insert_index, len(dest_folder.children)))
        dest_folder.children.insert(insert_index, source)
        self._show_moved_node(source_id)
        self.window.workspace.update_undo_baseline()
        self.window.set_status(f"Moved {self._node_label(source).strip()}.")

    def _unique_sheet_name(self, base: str = "Sheet") -> str:
        return self._unique_name(base, {s.name for s in self.session.sheets.values()})

    def _unique_graph_name(self, base: str = "Graph") -> str:
        return self._unique_name(base, {g.name for g in self.session.graphs.values()})

    def _unique_folder_name(self, base: str = "Folder") -> str:
        existing = set()

        def walk(node: TreeNode) -> None:
            for child in node.children:
                if child.type == "folder":
                    existing.add(child.name)
                walk(child)

        walk(self.session.tree_root)
        return self._unique_name(base, existing)

    @staticmethod
    def _unique_name(base: str, existing: set[str]) -> str:
        root = re.sub(r"\s+\d+$", "", base).strip() or base
        idx = 1
        while f"{root} {idx}" in existing:
            idx += 1
        return f"{root} {idx}"
