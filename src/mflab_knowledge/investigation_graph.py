"""Public, bounded representation of the structural path used by an investigation."""

from __future__ import annotations

from hashlib import sha256
from typing import Iterable


MAX_GRAPH_NODES = 24
MAX_GRAPH_EDGES = 36


def _text(value: object) -> str:
    return str(value or "").strip()


def _occurrence(result: dict[str, object]) -> dict[str, object]:
    value = result.get("selected_occurrence")
    return value if isinstance(value, dict) else {}


def _node(result: dict[str, object], source_id: str | None) -> dict[str, object]:
    occurrence = _occurrence(result)
    chunk_id = _text(result.get("chunk_id"))
    return {
        "id": f"chunk:{chunk_id}",
        "chunk_id": chunk_id,
        "label": _text(result.get("title")) or _text(result.get("path")) or chunk_id,
        "title": _text(result.get("title")) or None,
        "path": _text(result.get("path")) or None,
        "project": _text(result.get("project")) or None,
        "branch": _text(occurrence.get("branch")) or None,
        "commit_sha": _text(occurrence.get("commit_sha")) or None,
        "line_start": result.get("line_start"),
        "line_end": result.get("line_end"),
        "source_kind": _text(result.get("source_kind")) or None,
        "source_id": source_id,
    }


def traversal_edges(
    *,
    tool: str,
    origin_chunk_id: str,
    results: Iterable[dict[str, object]],
    iteration: int | None = None,
) -> list[dict[str, object]]:
    """Create only edges established by a completed read-only traversal."""

    origin = _text(origin_chunk_id)
    if not origin:
        return []
    relation = {
        "find_callers": ("calls", "result_to_origin", True),
        "find_callees": ("calls", "origin_to_result", True),
        "open_related": ("related", "origin_to_result", False),
        "open_neighborhood": ("neighbor", "origin_to_result", False),
    }.get(tool)
    if relation is None:
        return []
    kind, direction, directed = relation
    edges: list[dict[str, object]] = []
    for result in results:
        target = _text(result.get("chunk_id"))
        if not target or target == origin:
            continue
        source_chunk, target_chunk = (
            (target, origin) if direction == "result_to_origin" else (origin, target)
        )
        identity = f"{tool}\0{source_chunk}\0{target_chunk}"
        edges.append(
            {
                "id": "edge:" + sha256(identity.encode("utf-8")).hexdigest()[:20],
                "source": f"chunk:{source_chunk}",
                "target": f"chunk:{target_chunk}",
                "kind": kind,
                "directed": directed,
                "tool": tool,
                "iteration": iteration,
                "evidence": "persisted_structure",
            }
        )
    return edges


def build_investigation_graph(
    *,
    results: Iterable[dict[str, object]],
    traversals: Iterable[dict[str, object]],
    sources: Iterable[dict[str, object]],
) -> dict[str, object]:
    """Build a small UI payload without source text or model-only hypotheses."""

    result_by_chunk: dict[str, dict[str, object]] = {}
    for result in results:
        chunk_id = _text(result.get("chunk_id"))
        if chunk_id and chunk_id not in result_by_chunk:
            result_by_chunk[chunk_id] = result

    source_ids: dict[str, str] = {}
    source_chunks: list[str] = []
    for source in sources:
        chunk_id = _text(source.get("chunk_id"))
        source_id = _text(source.get("source_id"))
        if not chunk_id:
            continue
        if chunk_id not in result_by_chunk:
            result_by_chunk[chunk_id] = source
        if source_id:
            source_ids[chunk_id] = source_id
        source_chunks.append(chunk_id)

    edges: list[dict[str, object]] = []
    seen_edges: set[str] = set()
    connected_chunks: list[str] = []
    for traversal in traversals:
        edge_id = _text(traversal.get("id"))
        source = _text(traversal.get("source")).removeprefix("chunk:")
        target = _text(traversal.get("target")).removeprefix("chunk:")
        if (
            not edge_id
            or edge_id in seen_edges
            or source not in result_by_chunk
            or target not in result_by_chunk
        ):
            continue
        seen_edges.add(edge_id)
        edges.append(dict(traversal))
        connected_chunks.extend((source, target))
        if len(edges) >= MAX_GRAPH_EDGES:
            break

    ordered_chunks: list[str] = []
    for chunk_id in [*source_chunks, *connected_chunks]:
        if chunk_id in result_by_chunk and chunk_id not in ordered_chunks:
            ordered_chunks.append(chunk_id)
        if len(ordered_chunks) >= MAX_GRAPH_NODES:
            break
    allowed_nodes = set(ordered_chunks)
    edges = [
        edge
        for edge in edges
        if _text(edge.get("source")).removeprefix("chunk:") in allowed_nodes
        and _text(edge.get("target")).removeprefix("chunk:") in allowed_nodes
    ]
    nodes = [
        _node(result_by_chunk[chunk_id], source_ids.get(chunk_id))
        for chunk_id in ordered_chunks
    ]
    return {
        "algorithm": "observed_structural_trace_v1",
        "status": "available" if nodes and edges else "empty",
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "truncated": (
            len(result_by_chunk) > len(nodes)
            or len(seen_edges) > len(edges)
        ),
    }
