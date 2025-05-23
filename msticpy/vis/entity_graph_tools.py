# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""Creates an entity graph for a Microsoft Sentinel Incident."""

from datetime import datetime, timezone
from importlib.metadata import version
from typing import List, Optional, Union

import networkx as nx
import numpy as np
import pandas as pd
from bokeh.io import output_notebook, show
from bokeh.layouts import column
from bokeh.models import Circle, HoverTool, Label, LayoutDOM  # type: ignore
from bokeh.plotting import figure, from_networkx
from dateutil import parser
from packaging.version import Version, parse

from .._version import VERSION
from ..common.exceptions import MsticpyUserError
from ..common.utility import export
from ..datamodel.entities import Entity
from ..datamodel.entities.alert import Alert
from ..datamodel.soc.incident import Incident
from ..nbtools.security_alert import SecurityAlert
from ..vis.timeline import display_timeline
from ..vis.timeline_duration import display_timeline_duration
from .figure_dimension import bokeh_figure

__version__ = VERSION
__author__ = "Pete Bryan"

# mypy and Bokeh are not best friends
# mypy: disable-error-code="arg-type"

req_alert_cols = ["DisplayName", "Severity", "AlertType"]
req_inc_cols = ["id", "name", "properties.severity"]

_BOKEH_VERSION: Version = parse(version("bokeh"))

# wrap figure function to handle v2/v3 parameter renaming
figure = bokeh_figure(figure)  # type: ignore[assignment, misc]


@export
class EntityGraph:
    """Create a graph for visualizing and tracking links between entities."""

    def __init__(
        self,
        entity: Union[Incident, Alert, pd.DataFrame, pd.Series, Entity, SecurityAlert],
    ):
        """
        Create a new instance of the entity graph.

        Parameters
        ----------
        entity : Union[Incident, Alert, pd.DataFrame, pd.Series, Entity, SecurityAlert]
            The initial item to add to the graph.
            Can be an Incident, Alert, SecurityAlert or other Entity

        """
        output_notebook()
        self.alertentity_graph = nx.Graph(id="IncidentGraph")
        if isinstance(entity, (Incident, Alert)):
            self._add_incident_or_alert_node(entity)
        elif isinstance(entity, pd.DataFrame):
            self.add_incident(entity)
        elif isinstance(entity, pd.Series):
            self.add_incident(entity.to_frame().T)
        elif isinstance(entity, Entity):
            self._add_entity_node(entity)
        elif isinstance(entity, SecurityAlert):
            entity = Alert(entity)  # type: ignore
            self._add_incident_or_alert_node(entity)

    def plot(self, hide: bool = False, timeline: bool = False, **kwargs) -> LayoutDOM:
        """
        Plot a graph of entities.

        Parameters
        ----------
        hide : bool, optional
            Set true to not display the graphic, by default False
        timeline : bool, optional
            Set to True to display a timeline, by default False
        node_size : int, optional
            Size of the nodes in pixels, by default 25
        font_size : int, optional
            Font size for node labels, by default 10
            Can be an integer (point size) or a string (e.g. "10pt")
        width : int, optional
            Width in pixels, by default 800
        height : int, optional
            Image height (the default is 800)
        scale : int, optional
            Position scale (the default is 2)

        Returns
        -------
        LayoutDOM
            A Bokeh figure object

        """
        if timeline:
            return self._plot_with_timeline(hide=hide, **kwargs)
        return self._plot_no_timeline(hide=hide, **kwargs)

    def _plot_no_timeline(self, hide: bool = False, **kwargs) -> LayoutDOM:
        """
        Plot a graph of entities.

        Parameters
        ----------
        hide : bool, optional
            Set true to not display the graphic, by default False

        Returns
        -------
        LayoutDOM
            A Bokeh figure object

        """
        return plot_entitygraph(self.alertentity_graph, hide=hide, **kwargs)

    def _plot_with_timeline(self, hide: bool = False, **kwargs) -> LayoutDOM:
        """
        Plot the entity graph with a timeline.

        Parameters
        ----------
        hide : bool, optional
            Set true to not display the graphic, by default False

        Returns
        -------
        LayoutDOM
            A Bokeh figure object

        """
        timeline = None
        tl_df = self.to_df()

        tl_type = "duration"
        # pylint: disable=unsubscriptable-object
        if len(tl_df["EndTime"].unique()) == 1 and not tl_df["EndTime"].unique()[0]:
            tl_type = "discreet"
            if (
                len(tl_df["TimeGenerated"].unique()) == 1
                and not tl_df["TimeGenerated"].unique()[0]
            ):
                print("No timestamps available to create timeline")
                return self._plot_no_timeline(timeline=False, hide=hide, **kwargs)

        graph = self._plot_no_timeline(hide=True, **kwargs)
        if tl_type == "duration":
            # remove missing time values
            timeline = display_timeline_duration(
                tl_df.dropna(subset=["StartTime", "EndTime"]),
                group_by="Name",
                title="Entity Timeline",
                time_column="StartTime",
                end_time_column="EndTime",
                source_columns=["Name", "Description", "Type", "StartTime", "EndTime"],
                hide=True,
                width=800,
            )
        elif tl_type == "discreet":
            tl_df = tl_df.dropna(subset=["TimeGenerated"])
            timeline = display_timeline(
                tl_df.dropna(subset=["TimeGenerated"]),
                group_by="Type",
                title="Entity Timeline",
                time_column="TimeGenerated",
                source_columns=["Name", "Description", "Type", "TimeGenerated"],
                hide=True,
                width=800,
            )
        plot_layout = column(graph, timeline) if timeline else graph
        if not hide:
            show(plot_layout)
        return plot_layout

    def add_entity(self, ent: Entity, attached_to: str = None):
        """
        Add an entity to the graph.

        Parameters
        ----------
        ent : Entity
            The entity object to add the graph
        attached_to : str, optional
            The name of the node to attach the entity to, by default None

        """
        self._add_entity_node(ent, attached_to)

    def add_incident(self, incident: Union[Incident, Alert, pd.DataFrame]):
        """
        Add another incident or set of incidents to the graph.

        Parameters
        ----------
        incident : Union[Incident, Alert, pd.DataFrame]
            This can be an alert, and incident or a DataFrame of alerts or incidents

        """
        inc = None
        if isinstance(incident, pd.DataFrame):
            for row in incident.iterrows():
                if "name" in row[1]:
                    inc = Incident(src_event=row[1])  # type: ignore
                elif "AlertName" in row[1]:
                    inc = Alert(src_event=row[1])  # type: ignore
                self._add_incident_or_alert_node(inc)
        else:
            self._add_incident_or_alert_node(incident)

    def add_note(
        self,
        name: str,
        description: Optional[str] = None,
        attached_to: Union[str, List] = None,
    ):
        """
        Add a node to the graph representing a note or comment.

        Parameters
        ----------
        name : str
            The name of the node to add
        description : Optional[str], optional
            A description of the note, by default None
        attached_to : Union[str, List], optional
            What existing nodes on the graph to attach it the note to, by default None
        user: str, optional
            What user to associate the note with

        """
        self.alertentity_graph.add_node(
            name,
            Name=name,
            Description=description,
            Type="analystnote",
            TimeGenerated=datetime.now(),
        )
        if attached_to:
            if isinstance(attached_to, str):
                attached_to = [attached_to]
            for link in attached_to:
                self.add_link(name, link)

    def add_link(self, source: str, target: str):
        """
        Add a link between 2 nodes on the graph.

        Parameters
        ----------
        source : str
            Name of node to link from
        target : str
            Name of node to link to

        Raises
        ------
        MsticpyUserError
            If nodes aren't present in the graph


        """
        # Check names are present
        if (
            source in self.alertentity_graph.nodes()
            and target in self.alertentity_graph.nodes()
        ):
            self.alertentity_graph.add_edge(source, target)
        else:
            missing = [
                name
                for name in [source, target]
                if name not in self.alertentity_graph.nodes()
            ]
            raise MsticpyUserError(title=f"Node(s) {missing} not found in graph")

    def remove_link(self, source: str, target: str):
        """
        Remove a link between 2 nodes on the graph.

        Parameters
        ----------
        source : str
            Name of node to remove link from
        target : str
            name of node to remove link to

        Raises
        ------
        MsticpyUserError
            If edge isn't present in the graph

        """
        if (
            source in self.alertentity_graph.nodes()
            and target in self.alertentity_graph.nodes()
            and self.alertentity_graph.has_edge(source, target)
        ):
            self.alertentity_graph.remove_edge(source, target)
        else:
            raise MsticpyUserError(
                title=f"No edge exists between {source} and {target}"
            )

    def remove_node(self, name: str):
        """
        Remove a node from the graph.

        Parameters
        ----------
        name : str
            The name of the node to remove.

        """
        # Check node is present
        if name in self.alertentity_graph.nodes():
            self.alertentity_graph.remove_node(name)
        else:
            raise MsticpyUserError(f"Node named {name} not found")

    def to_df(self) -> pd.DataFrame:
        """Generate a dataframe of nodes in the graph."""
        node_list = [
            {
                "Name": node.get("Name"),
                "Description": node.get("Description"),
                "Type": node.get("Type"),
                "TimeGenerated": _convert_to_tz_aware_ts(node.get("TimeGenerated")),
                "EndTime": _convert_to_tz_aware_ts(node.get("EndTime")),
                "StartTime": _convert_to_tz_aware_ts(
                    node.get("StartTime", node.get("TimeGenerated"))
                ),
            }
            for node in self.alertentity_graph.nodes.values()
        ]
        return pd.DataFrame(node_list).replace("None", np.nan)

    def _add_incident_or_alert_node(self, incident: Union[Incident, Alert, None]):
        """Check what type of entity is passed in and creates relevant graph."""
        if isinstance(incident, Incident):
            self._add_incident_node(incident)
        elif isinstance(incident, Alert):
            self._add_alert_node(incident)

    def _add_entity_node(self, ent, attached_to=None):
        """Add an Entity to the graph."""
        self.alertentity_graph = nx.compose(self.alertentity_graph, ent.to_networkx())
        if attached_to:
            self.add_link(attached_to, ent.name_str)

    def _add_alert_node(self, alert, incident_name=None):
        """Add an alert entity to the graph."""
        self.alertentity_graph = nx.compose(self.alertentity_graph, alert.to_networkx())
        if alert["Entities"]:
            for ent in alert["Entities"]:
                self._add_entity_node(ent, alert.name_str)
        if incident_name:
            self.add_link(incident_name, alert.name_str)

    def _add_incident_node(self, incident):
        """Add an incident entity to the graph."""
        self.alertentity_graph = nx.compose(
            self.alertentity_graph, incident.to_networkx()
        )
        if incident.Alerts:
            for alert in incident.Alerts:
                self._add_alert_node(alert, incident.name_str)
        if incident.Entities:
            entities = _dedupe_entities(incident.Alerts, incident.Entities)
            for ent in entities:
                self._add_entity_node(ent, incident.name_str)

    def _add_entity_edges(self, edges: set, attached_to: str):
        """Check entity edges and add them."""
        for edge in edges:
            if isinstance(edge.target, Entity):
                if not self.alertentity_graph.has_node(edge.target.name_str):
                    self._add_entity_node(edge.target)
                try:
                    self.add_link(attached_to, edge.target.name_str)
                except MsticpyUserError:
                    pass

    @property
    def graph(self) -> nx.Graph:
        """Return the raw NetworkX graph."""
        return self.alertentity_graph


def _convert_to_tz_aware_ts(date_string: Optional[str]) -> Optional[datetime]:
    """Convert a date string to a timezone aware datetime object."""
    if date_string is None:
        return None
    date_time = parser.parse(date_string)
    if date_time.tzinfo is None:
        return date_time.replace(tzinfo=timezone.utc)
    return date_time


def _dedupe_entities(alerts, ents) -> list:
    """Deduplicate incident and alert entities."""
    alert_entities = []
    for alert in alerts:
        if alert["Entities"]:
            alert_entities += [hash(ent) for ent in alert["Entities"]]
    for ent in ents:
        if hash(ent) in alert_entities:
            ents.remove(ent)
    return ents


@export
def plot_entitygraph(  # pylint: disable=too-many-locals
    entity_graph: nx.Graph,
    node_size: int = 25,
    font_size: Union[int, str] = 10,
    height: int = 800,
    width: int = 800,
    scale: int = 2,
    hide: bool = False,
) -> figure:
    """
    Plot entity graph with Bokeh.

    Parameters
    ----------
    entity_graph : nx.Graph
        The entity graph as a networkX graph
    node_size : int, optional
        Size of the nodes in pixels, by default 25
    font_size : int, optional
        Font size for node labels, by default 10
        Can be an integer (point size) or a string (e.g. "10pt")
    width : int, optional
        Width in pixels, by default 800
    height : int, optional
        Image height (the default is 800)
    scale : int, optional
        Position scale (the default is 2)
    hide : bool, optional
        Don't show the plot, by default False. If True, just
        return the figure.

    Returns
    -------
    bokeh.plotting.figure
        The network plot.

    """
    color_map = {
        "incident": "red",
        "alert": "orange",
        "alerts": "orange",
        "securityalert": "orange",
        "analystnote": "blue",
    }
    output_notebook()
    font_pnt = f"{font_size}pt" if isinstance(font_size, int) else font_size
    node_attrs = {}
    for node, attrs in entity_graph.nodes(data=True):
        try:
            color = color_map.get(attrs["Type"].lower(), "green")
        except KeyError:
            color = "green"
        node_attrs.update({node: color})

    nx.set_node_attributes(entity_graph, node_attrs, "node_color")

    plot = figure(
        title="Alert Entity graph",
        x_range=(-3, 3),
        y_range=(-3, 3),
        width=width,
        height=height,
    )

    plot.add_tools(
        HoverTool(
            tooltips=[
                ("Name", "@Name"),
                ("Description", "@Description"),
                ("Type", "@Type"),
            ]
        )
    )

    entity_graph_for_plotting = nx.Graph()
    index_node = 0
    rev_index = {}
    fwd_index = {}
    node_attributes = {}
    for node_key in entity_graph.nodes:
        entity_graph_for_plotting.add_node(index_node)
        rev_index[node_key] = index_node
        fwd_index[index_node] = node_key
        node_attributes[index_node] = entity_graph.nodes[node_key]
        index_node += 1

    nx.set_node_attributes(entity_graph_for_plotting, node_attributes)

    for source_node, target_node in entity_graph.edges:
        entity_graph_for_plotting.add_edge(
            rev_index[source_node], rev_index[target_node]
        )

    graph_renderer = from_networkx(
        entity_graph_for_plotting, nx.spring_layout, scale=scale, center=(0, 0)
    )
    if _BOKEH_VERSION > Version("3.2.0"):
        circle_parms = {
            "radius": node_size // 2,
            "fill_color": "node_color",
            "fill_alpha": 0.5,
        }
    else:
        circle_parms = {
            "size": node_size,
            "fill_color": "node_color",
            "fill_alpha": 0.5,
        }
    graph_renderer.node_renderer.glyph = Circle(**circle_parms)  # type: ignore[attr-defined]

    # pylint: disable=no-member
    plot.renderers.append(graph_renderer)  # type: ignore[attr-defined]

    # Create labels
    label_layout = graph_renderer.layout_provider.graph_layout  # type: ignore[attr-defined]
    for index, pos in label_layout.items():
        label = Label(
            x=pos[0],
            y=pos[1],
            x_offset=5,
            y_offset=5,
            text=fwd_index[int(index)],
            text_font_size=font_pnt,
        )
        plot.add_layout(label)
    # pylint: enable=no-member
    if not hide:
        show(plot)
    return plot
