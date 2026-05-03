"""Rich/typer renderers for the `kentro.viz` data shapes.

This module is the CLI mirror the plan called for: it consumes the dataclasses
from `kentro.viz` (which are pure data, no rendering) and prints them with
Rich tables/panels suitable for terminal output.

Lives in `kentro_server` (not the SDK) because:
- `rich` is a server-side dep (used by `kentro-server` CLI commands), not an
  SDK dep — keeps the SDK lean for users who only want HTTP + types.
- The CLI commands that wrap these renderers also live in `kentro-server`.

Per IMPLEMENTATION_PLAN.md "Step 9": v0 ships the renderers; the CLI commands
that wire them in (`kentro-server access-matrix`, etc.) are deferred until
users ask — `viz_cli.print_*` is callable directly from any CLI command or
notebook today.
"""

from kentro.rules import render_rule
from kentro.viz import (
    AccessMatrix,
    ConflictsView,
    LineageView,
    RuleDiffView,
)
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table


def print_access_matrix(matrix: AccessMatrix, *, console: Console) -> None:
    """Render the access matrix as one Rich table.

    Rows = agents. Cols = (entity_type, field_name). Each cell shows three
    glyphs: read / write / visibility. The glyph encoding is deliberately
    terse so a wide schema fits in a terminal:

        ✓✓●  = read allow, write allow, visibility allow
        ✗✓●  = read deny,  write allow, visibility allow
        ✗✗○  = read deny,  write deny,  visibility hidden
    """
    table = Table(title=f"Access matrix ({len(matrix.rows)} agents × {len(matrix.cols)} fields)")
    table.add_column("agent", style="bold")
    for entity_type, field_name in matrix.cols:
        table.add_column(f"{entity_type}.{field_name}", justify="center")

    for agent in matrix.rows:
        cells: list[str] = [agent]
        for entity_type, field_name in matrix.cols:
            cell = matrix.cells[(agent, entity_type, field_name)]
            r = "[green]✓[/green]" if cell.read == "allow" else "[red]✗[/red]"
            w = "[green]✓[/green]" if cell.write == "allow" else "[red]✗[/red]"
            v = "[green]●[/green]" if cell.visibility == "allow" else "[yellow]○[/yellow]"
            cells.append(f"{r}{w}{v}")
        table.add_row(*cells)

    console.print(table)
    console.print(
        "[dim]glyphs: read / write / visibility · ✓ allow · ✗ deny · ● visible · ○ hidden[/dim]"
    )


def print_rule_diff(diff: RuleDiffView, *, console: Console) -> None:
    """Render a `RuleDiffView` as sectioned `+`/`-` blocks per rule type.

    One Rich panel per non-empty section. Header carries the `+N -M` summary."""
    title = f"Rule diff: [green]+{diff.total_added}[/green] [red]-{diff.total_removed}[/red]"
    if diff.total_added == 0 and diff.total_removed == 0:
        console.print(Panel.fit("(no changes)", title=title, border_style="dim"))
        return

    for section in diff.sections:
        if not section.added and not section.removed:
            continue
        # Build the panel body as a single markup string. `render_rule` returns
        # plain text containing literal `[allow]`/`[skill]` brackets; Rich would
        # parse those as markup, so we `escape()` before wrapping in our color.
        lines: list[str] = []
        for r in section.added:
            lines.append(f"[green]+ {escape(render_rule(r))}[/green]")
        for r in section.removed:
            lines.append(f"[red]- {escape(render_rule(r))}[/red]")
        console.print(
            Panel(
                "\n".join(lines),
                title=f"[bold]{section.rule_type}[/bold]  "
                f"(+{len(section.added)} -{len(section.removed)})",
                border_style="bright_black",
            )
        )


def print_lineage(view: LineageView, *, console: Console) -> None:
    """Render lineage as a per-field section: each field gets a header + its
    lineage entries indented underneath. Suitable for "where did this come
    from?" terminal queries.
    """
    console.print(f"[bold]Lineage[/bold] for [cyan]{view.entity_type}/{view.entity_key}[/cyan]")
    if not view.fields:
        console.print("  [dim](no declared fields)[/dim]")
        return
    for field_view in view.fields:
        status_style = {
            FieldStatus_KNOWN: "green",
            FieldStatus_UNRESOLVED: "yellow",
            FieldStatus_HIDDEN: "red",
            FieldStatus_UNKNOWN: "dim",
        }.get(field_view.status.value, "white")
        console.print(
            f"  [bold]{field_view.field_name}[/bold] "
            f"[{status_style}]{field_view.status.value}[/{status_style}]"
        )
        if not field_view.entries:
            continue
        for entry in field_view.entries:
            r = entry.record
            confidence = f" (conf={entry.confidence:.2f})" if entry.confidence is not None else ""
            doc = (
                f" doc={r.source_document_id}"
                if r.source_document_id is not None
                else " (direct write)"
            )
            console.print(
                f"      = [cyan]{entry.value!r}[/cyan]{confidence} "
                f"by [bold]{r.written_by_agent_id}[/bold] "
                f"at {r.written_at.isoformat()} "
                f"[dim](rule v{r.rule_version}{doc})[/dim]"
            )


def print_conflicts(view: ConflictsView, *, console: Console) -> None:
    """Render unresolved fields as a Rich table. One row per (entity, field)
    with its candidates listed inline."""
    if not view.rows:
        console.print("[green]No unresolved conflicts.[/green]")
        return
    table = Table(title=f"Unresolved fields ({len(view.rows)})")
    table.add_column("entity", style="cyan")
    table.add_column("field", style="bold")
    table.add_column("candidates")
    table.add_column("reason", style="dim")
    for row in view.rows:
        candidates_str = "  |  ".join(
            f"{c.value!r} ({c.lineage[0].written_by_agent_id if c.lineage else '?'})"
            for c in row.candidates
        )
        table.add_row(
            f"{row.entity_type}/{row.entity_key}",
            row.field_name,
            candidates_str,
            row.reason or "",
        )
    console.print(table)


# Local FieldStatus value mirrors so we don't need to import FieldStatus into
# print_lineage's tight string-mapping table — Pydantic StrEnum values are the
# wire form ("known", "unresolved", "hidden", "unknown").
FieldStatus_KNOWN = "known"
FieldStatus_UNRESOLVED = "unresolved"
FieldStatus_HIDDEN = "hidden"
FieldStatus_UNKNOWN = "unknown"


__all__ = [
    "print_access_matrix",
    "print_conflicts",
    "print_lineage",
    "print_rule_diff",
]
