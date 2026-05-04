"""Visualization routes — pre-shaped JSON for the demo UI's right column.

Two endpoints:

- `GET /viz/access-matrix?entity_type=X` returns a flat-cells access decision
  for every (agent × field) tuple of a single entity type. Computed via
  `kentro.viz.access_matrix(...)` over the active ruleset; the response is a
  list of `(agent_id, entity_type, field_name, read, write, visible)` cells
  the JS table renderer can index by composite key.

- `GET /viz/graph` returns a bipartite document↔entity graph keyed off
  `FieldWriteRow`s: nodes are documents (left column) and entities (right
  column), one edge per distinct (document, entity, field) write. The UI
  renders this with inline SVG; the server stays in the data shape.

Both routes are tenant-scoped via the bearer (any agent on the tenant can
read; the response is the *static* graph + matrix and doesn't leak field
*values*). The matrix route uses the active ruleset to compute decisions —
so an agent calling it sees the same matrix every other agent does, which
is what the UI wants ("here's the policy applied to everyone").
"""

import logging

from fastapi import APIRouter
from kentro.types import (
    AccessMatrixCellView,
    AccessMatrixView,
    GraphEdge,
    GraphNode,
    GraphView,
)
from kentro.viz import access_matrix as compute_access_matrix
from sqlalchemy import or_
from sqlmodel import col, select

from kentro_server.api.auth import PrincipalDep
from kentro_server.api.deps import SchemaRegistryDep, TenantRegistryDep
from kentro_server.core.rules import load_active_ruleset
from kentro_server.store.models import DocumentRow, EntityRow, EventRow, FieldWriteRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/viz", tags=["viz"])


@router.get("/access-matrix", response_model=AccessMatrixView)
def get_access_matrix(
    entity_type: str,
    principal: PrincipalDep,
    schema: SchemaRegistryDep,
    registry: TenantRegistryDep,
) -> AccessMatrixView:
    """Compute the access matrix for one entity type across every agent on the tenant.

    Pulls the entity type's `FieldDef`s from the schema registry; the agents
    list comes from `tenants.json` via the `TenantRegistry`. The matrix is
    computed against the currently-active ruleset.
    """
    ruleset = load_active_ruleset(principal.store)
    type_defs = [td for td in schema.list_all() if td.name == entity_type]
    agent_ids = [a.id for a in registry.agents_for(principal.tenant_id)]
    matrix = compute_access_matrix(
        ruleset=ruleset,
        agents=agent_ids,
        entity_type_defs=type_defs,
    )
    field_names = tuple(f.name for td in type_defs for f in td.fields)
    cells: list[AccessMatrixCellView] = []
    for agent in agent_ids:
        for fname in field_names:
            cell = matrix.cells.get((agent, entity_type, fname))
            if cell is None:
                continue
            cells.append(
                AccessMatrixCellView(
                    agent_id=agent,
                    entity_type=entity_type,
                    field_name=fname,
                    read=cell.read == "allow",
                    write=cell.write == "allow",
                    visible=cell.visibility == "allow",
                )
            )
    return AccessMatrixView(
        entity_type=entity_type,
        fields=field_names,
        agents=tuple(agent_ids),
        cells=tuple(cells),
    )


@router.get("/graph", response_model=GraphView)
def get_graph(principal: PrincipalDep) -> GraphView:
    """Bipartite (document, entity) graph keyed off FieldWriteRow.

    Nodes:
      - One per `DocumentRow` (kind=`document`, label=its filename/label).
      - One per `EntityRow` (kind=`entity`, label=`<key>`, sub=`<type>`).

    Edges: one per `FieldWriteRow` with a `source_document_id` set, carrying
    the field name + writing agent so the UI can highlight a specific lineage
    (e.g. when the LineageDrawer is open on `Customer.Acme.deal_size`).

    Tenant-scoped (every row in the tenant's DB is visible). Per-agent ACL
    filtering would change the picture every render and isn't what this panel
    is for — see `<AccessMatrix>` for the agent-scoped view.
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    # The graph reflects the LIVE world: documents and writes whose owning
    # catalog event has been toggled off are filtered out so the picture
    # matches what the AgentPanels and the matrix show. NULL event_id is
    # always live.
    with principal.store.session() as session:
        docs = session.exec(
            select(DocumentRow)
            .join(EventRow, isouter=True)
            .where(
                or_(
                    col(DocumentRow.event_id).is_(None),
                    col(EventRow.active).is_(True),
                )
            )
        ).all()
        ents = session.exec(select(EntityRow)).all()
        writes = session.exec(
            select(FieldWriteRow)
            .join(EventRow, isouter=True)
            .where(
                col(FieldWriteRow.source_document_id).is_not(None),
                or_(
                    col(FieldWriteRow.event_id).is_(None),
                    col(EventRow.active).is_(True),
                ),
            )
        ).all()
        ent_by_id = {e.id: e for e in ents}
        for d in docs:
            nodes.append(
                GraphNode(
                    id=f"doc:{d.id}",
                    kind="document",
                    label=d.label or str(d.id)[:8],
                    sub=d.source_class,
                )
            )
        for e in ents:
            nodes.append(
                GraphNode(
                    id=f"ent:{e.type}:{e.key}",
                    kind="entity",
                    label=e.key,
                    sub=e.type,
                )
            )
        # Dedupe (doc, entity, field) triples — the UI doesn't need a separate
        # edge per write event, just per write target.
        seen: set[tuple[str, str, str, str]] = set()
        for w in writes:
            ent = ent_by_id.get(w.entity_id)
            if ent is None:
                continue
            src = f"doc:{w.source_document_id}"
            tgt = f"ent:{ent.type}:{ent.key}"
            key = (src, tgt, w.field_name, w.written_by_agent_id)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                GraphEdge(
                    source=src,
                    target=tgt,
                    field_name=w.field_name,
                    agent_id=w.written_by_agent_id,
                )
            )
    return GraphView(nodes=tuple(nodes), edges=tuple(edges))
