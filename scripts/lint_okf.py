#!/usr/bin/env python3
"""lint_okf.py — 检查 log-wiki 的 OKF-ish 结构与排查护栏。"""
import collections
import pathlib
import re
import sys
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIKI_DIR = ROOT / "wiki"
CASES_DIR = WIKI_DIR / "cases"
RAW_DIR = ROOT / "raw" / "sources"
RESERVED = {"index.md", "log.md"}


def rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_doc(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text, "missing frontmatter"
    try:
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body, ""
    except Exception as exc:
        return {}, text, f"invalid frontmatter: {exc}"


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def target_exists(link: str, current: pathlib.Path) -> bool:
    if re.match(r"^[a-z]+://", link):
        return True
    link = link.split("#", 1)[0]
    if not link:
        return True
    candidates = [ROOT / link.lstrip("/")]
    if not link.startswith("/"):
        candidates.append(current.parent / link)
    return any(path.exists() for path in candidates)


def main() -> int:
    errors = []
    warnings = []
    signature_to_cases = collections.defaultdict(list)
    sourced_raw = set()

    docs = [p for p in WIKI_DIR.rglob("*.md") if p.name not in RESERVED]
    for path in sorted(docs):
        fm, body, err = read_doc(path)
        if err:
            errors.append(f"{rel(path)}: {err}")
            continue

        if not fm.get("type"):
            errors.append(f"{rel(path)}: missing required OKF frontmatter field `type`")
        if not fm.get("title"):
            warnings.append(f"{rel(path)}: missing recommended field `title`")
        if not fm.get("description"):
            warnings.append(f"{rel(path)}: missing recommended field `description`")

        for key in ("sources", "related", "cases"):
            for link in as_list(fm.get(key)):
                link = str(link)
                if key == "sources" and link.startswith("raw/"):
                    sourced_raw.add(link)
                if not target_exists(link, path):
                    errors.append(f"{rel(path)}: broken `{key}` link -> {link}")

        if path.is_relative_to(CASES_DIR):
            if not as_list(fm.get("signatures")):
                errors.append(f"{rel(path)}: missing `signatures`")
            if not as_list(fm.get("sources")):
                errors.append(f"{rel(path)}: missing `sources`")
            if not re.search(r"##\s*解决方案\s*\n", body):
                errors.append(f"{rel(path)}: missing `## 解决方案` section")
            if not re.search(r"##\s*Citations\s*\n", body, re.I):
                warnings.append(f"{rel(path)}: missing `## Citations` section")
            for sig in as_list(fm.get("signatures")):
                sig = str(sig).strip()
                if sig:
                    signature_to_cases[sig.lower()].append(rel(path))

    for sig, paths in sorted(signature_to_cases.items()):
        if len(paths) > 1:
            errors.append(f"duplicate signature `{sig}` in {', '.join(paths)}")

    if RAW_DIR.exists():
        for raw in sorted(RAW_DIR.glob("*.md")):
            raw_rel = rel(raw)
            if raw_rel not in sourced_raw:
                warnings.append(f"{raw_rel}: raw source is not referenced by any wiki doc")

    for index in (WIKI_DIR / "index.md", CASES_DIR / "index.md"):
        if not index.exists():
            warnings.append(f"{rel(index)}: missing progressive-disclosure index")

    if errors:
        print("ERRORS")
        for item in errors:
            print(f"- {item}")
    if warnings:
        print("WARNINGS")
        for item in warnings:
            print(f"- {item}")
    if not errors and not warnings:
        print("OK: wiki passes OKF-ish lint checks")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
