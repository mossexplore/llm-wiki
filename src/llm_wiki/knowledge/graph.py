#!/usr/bin/env python3
"""graph.py — 从 OKF-ish wiki bundle 生成知识图谱 JSON。"""
import itertools
import json
import logging
import pathlib
import re
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
WIKI_DIR = ROOT / "wiki"
RAW_DIR = ROOT / "raw" / "sources"
RESERVED = {"index.md", "log.md"}
logger = logging.getLogger("log_wiki.graph")


def rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def read_doc(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        return {}, text
    return yaml.safe_load(fm) or {}, body


def node_type(path: pathlib.Path, fm: dict) -> str:
    if path.match("wiki/cases/*.md") or "/cases/" in rel(path):
        return "case"
    if path.match("wiki/concepts/*.md") or "/concepts/" in rel(path):
        return "concept"
    return str(fm.get("type", "concept")).lower().replace(" ", "-")


def normalize_repo_link(link: str, current: pathlib.Path) -> str:
    if re.match(r"^[a-z]+://", link):
        return ""
    link = link.split("#", 1)[0]
    if not link:
        return ""
    candidates = []
    if link.startswith("/"):
        candidates.append(ROOT / link.lstrip("/"))
    else:
        candidates.append(ROOT / link)
        candidates.append(current.parent / link)
    for candidate in candidates:
        try:
            if candidate.exists():
                return rel(candidate)
        except ValueError:
            continue
    return link.lstrip("/")


def add_node(nodes: dict, node_id: str, kind: str, title: str, **extra):
    if not node_id:
        return
    node = nodes.setdefault(node_id, {"id": node_id, "type": kind, "title": title})
    node.update({k: v for k, v in extra.items() if v not in (None, "", [])})


def add_edge(edges: set, source: str, target: str, kind: str):
    if source and target and source != target:
        edges.add((source, target, kind))


def build_graph() -> dict:
    nodes = {}
    edges = set()
    docs = [p for p in WIKI_DIR.rglob("*.md") if p.name not in RESERVED]

    doc_meta = {}
    for path in sorted(docs):
        fm, body = read_doc(path)
        node_id = rel(path)
        kind = node_type(path, fm)
        tags = [str(t) for t in as_list(fm.get("tags"))]
        components = [str(c) for c in as_list(fm.get("components"))]
        doc_meta[node_id] = {"path": path, "fm": fm, "body": body, "tags": tags, "components": components}
        add_node(
            nodes,
            node_id,
            kind,
            fm.get("title") or path.stem,
            description=fm.get("description"),
            status=fm.get("status"),
            confidence=fm.get("confidence"),
            tags=tags,
            components=components,
        )

    for node_id, meta in doc_meta.items():
        path = meta["path"]
        fm = meta["fm"]
        body = meta["body"]

        for source in as_list(fm.get("sources")):
            target = normalize_repo_link(str(source), path)
            if target:
                add_node(nodes, target, "raw", pathlib.Path(target).name)
                add_edge(edges, node_id, target, "cites")

        for key, kind in (("related", "related"), ("cases", "supports")):
            for link in as_list(fm.get(key)):
                target = normalize_repo_link(str(link), path)
                if target:
                    add_edge(edges, node_id, target, kind)

        for component in meta["components"]:
            cid = f"component:{component}"
            add_node(nodes, cid, "component", component)
            add_edge(edges, node_id, cid, "mentions")

        for tag in meta["tags"]:
            tid = f"tag:{tag}"
            add_node(nodes, tid, "tag", tag)
            add_edge(edges, node_id, tid, "tagged")

        for label, link in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body):
            target = normalize_repo_link(link, path)
            if target and target in nodes:
                add_edge(edges, node_id, target, "links")

    case_ids = [nid for nid, node in nodes.items() if node["type"] == "case"]
    for a, b in itertools.combinations(case_ids, 2):
        am = doc_meta.get(a, {})
        bm = doc_meta.get(b, {})
        shared = (set(am.get("tags", [])) & set(bm.get("tags", []))) | (
            set(am.get("components", [])) & set(bm.get("components", []))
        )
        if shared:
            add_edge(edges, a, b, "similar")

    return {
        "nodes": sorted(nodes.values(), key=lambda n: (n["type"], n["id"])),
        "edges": [
            {"source": source, "target": target, "type": kind}
            for source, target, kind in sorted(edges)
        ],
    }


def main():
    logger.info(json.dumps(build_graph(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
