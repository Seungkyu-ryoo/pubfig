from copy import deepcopy
from dataclasses import replace
import unittest

import pandas as pd

from pubfig.editor_session import EditorSession
from pubfig.model import Graph, ProjectDocument, Sheet
from pubfig.plot_config import AnnotationConfig, PlotConfig, SeriesConfig


class EditorSessionTests(unittest.TestCase):
    def setUp(self):
        self.sheet = Sheet("sh1", "Data", pd.DataFrame({"X": [1], "Y": [2]}))
        self.graph = Graph("gr1", "Figure", self.sheet.id)
        self.document = ProjectDocument(
            sheets={self.sheet.id: self.sheet}, graphs={self.graph.id: self.graph}
        )
        self.session = EditorSession(self.document, self.sheet.id, self.graph.id)

    def test_replacing_active_buffers_and_settings_has_one_owner(self):
        frame = pd.DataFrame({"X": [3], "Y": [4]})
        self.session.df = frame
        self.assertIs(self.sheet.df, frame)
        self.sheet.df = frame.copy()
        self.assertIs(self.session.df, self.sheet.df)
        config = PlotConfig(title="Updated")
        self.session.plot_config = config
        self.assertIs(self.graph.plot_config, config)
        self.graph.plot_config = PlotConfig(title="Replaced externally")
        self.assertIs(self.session.plot_config, self.graph.plot_config)
        series = {"Y": SeriesConfig(x="X", y="Y")}
        self.session.series_by_y = series
        self.assertIs(self.graph.series_by_y, series)

    def test_annotations_follow_config_replacement_and_legacy_constructor(self):
        annotations = [AnnotationConfig(text="First")]
        graph = Graph("gr2", "Annotated", self.sheet.id, annotations=annotations)
        self.assertIs(graph.annotations, graph.plot_config.annotations)
        self.assertIs(graph.annotations, annotations)
        config = PlotConfig(annotations=[AnnotationConfig(text="Replacement")])
        graph.plot_config = config
        self.assertIs(graph.annotations, config.annotations)
        clone = deepcopy(graph)
        self.assertIs(clone.annotations, clone.plot_config.annotations)
        self.assertIsNot(clone.annotations, graph.annotations)
        updated = replace(graph, name="Renamed")
        self.assertEqual(updated.name, "Renamed")
        self.assertIs(updated.annotations, config.annotations)

    def test_leaving_graph_for_sheet_does_not_edit_previous_graph(self):
        self.graph.plot_config.title = "Keep this graph"
        self.graph.series_by_y = {"Y": SeriesConfig(x="X", y="Y")}
        self.session.select_sheet(self.sheet.id)
        self.session.plot_config.title = "Draft only"
        self.session.series_by_y = {}
        self.session.annotations = [AnnotationConfig(text="Draft")]
        self.assertEqual(self.graph.plot_config.title, "Keep this graph")
        self.assertIn("Y", self.graph.series_by_y)
        self.assertEqual(self.graph.annotations, [])
        self.session.active_graph_id = self.graph.id
        self.assertEqual(self.session.plot_config.title, "Keep this graph")

    def test_replacing_document_drops_old_active_context(self):
        self.session.replace_document(ProjectDocument())
        self.assertIsNone(self.session.sheet)
        self.assertIsNone(self.session.graph)
        self.assertTrue(self.session.df.empty)
        self.session.plot_config.title = "New draft"
        self.assertNotEqual(self.graph.plot_config.title, "New draft")

    def test_detached_state_is_not_shared_between_sessions(self):
        first, second = EditorSession(), EditorSession()
        first.annotations.append(AnnotationConfig(text="Private"))
        first.series_by_y["Y"] = SeriesConfig()
        self.assertEqual(second.annotations, [])
        self.assertEqual(second.series_by_y, {})
