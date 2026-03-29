from __future__ import annotations

import glob
import hashlib
import os
import pickle
from dataclasses import dataclass, field
from pathlib import Path

from pyang import context as pyang_context
from pyang import repository


@dataclass
class YangEntry:
    xpath: str
    node_type: str  # container, list, leaf, leaf-list
    yang_type: str  # e.g. counter64, string, uint32
    description: str
    module: str
    keys: list[str] = field(default_factory=list)


class YangIndex:
    def __init__(self, entries: list[YangEntry]) -> None:
        self.entries = entries
        self._search_text = [
            f"{e.xpath} {e.description}".lower() for e in entries
        ]

    def __len__(self) -> int:
        return len(self.entries)

    def search(
        self,
        keyword: str,
        module_filter: str | None = None,
        max_results: int = 20,
    ) -> list[YangEntry]:
        terms = keyword.lower().split()
        if not terms:
            return []

        matches: list[tuple[int, int, int, YangEntry]] = []
        for i, text in enumerate(self._search_text):
            entry = self.entries[i]

            if module_filter and module_filter.lower() not in entry.module.lower():
                continue

            if not all(t in text for t in terms):
                continue

            # Score: terms matching in xpath (higher = better), negative depth (shallower = better)
            xpath_lower = entry.xpath.lower()
            xpath_hits = sum(1 for t in terms if t in xpath_lower)
            depth = entry.xpath.count("/")
            # Sort key: more xpath hits first, then shallower paths
            matches.append((-xpath_hits, depth, i, entry))

        matches.sort()
        return [m[3] for m in matches[:max_results]]


def _compute_hash(yang_dir: Path) -> str:
    yang_files = sorted(glob.glob(str(yang_dir / "**" / "*.yang"), recursive=True))
    h = hashlib.sha256()
    for f in yang_files:
        h.update(f.encode())
        h.update(str(os.path.getmtime(f)).encode())
    return h.hexdigest()[:16]


def _find_search_dirs(yang_dir: Path) -> list[str]:
    dirs: list[str] = []
    for root, _, files in os.walk(yang_dir):
        if any(f.endswith(".yang") for f in files):
            dirs.append(root)
    return dirs


def _walk_node(node, path: str, entries: list[YangEntry]) -> None:
    keyword = node.keyword
    if keyword not in ("container", "list", "leaf", "leaf-list"):
        return

    current_path = f"{path}/{node.arg}"

    yang_type = ""
    if keyword in ("leaf", "leaf-list"):
        t = node.search_one("type")
        if t:
            yang_type = t.arg

    desc_stmt = node.search_one("description")
    description = desc_stmt.arg.strip() if desc_stmt else ""

    module_name = node.i_module.arg if hasattr(node, "i_module") else ""

    keys: list[str] = []
    if keyword == "list":
        k = node.search_one("key")
        if k:
            keys = k.arg.split()

    entries.append(
        YangEntry(
            xpath=current_path,
            node_type=keyword,
            yang_type=yang_type,
            description=description,
            module=module_name,
            keys=keys,
        )
    )

    if hasattr(node, "i_children"):
        for child in node.i_children:
            _walk_node(child, current_path, entries)


def _parse_yang_models(yang_dir: Path) -> list[YangEntry]:
    search_dirs = _find_search_dirs(yang_dir)
    repo = repository.FileRepository(":".join(search_dirs), use_env=False)
    ctx = pyang_context.Context(repo)

    yang_files = sorted(glob.glob(str(yang_dir / "**" / "*.yang"), recursive=True))
    for yf in yang_files:
        with open(yf) as f:
            text = f.read()
        ctx.add_module(yf, text)

    ctx.validate()

    entries: list[YangEntry] = []
    for mod in ctx.modules.values():
        if hasattr(mod, "i_children"):
            for child in mod.i_children:
                _walk_node(child, "", entries)

    # Deduplicate by xpath (augmentations can create duplicates across modules)
    seen: set[str] = set()
    unique: list[YangEntry] = []
    for e in entries:
        if e.xpath not in seen:
            seen.add(e.xpath)
            unique.append(e)

    return unique


def build_or_load_yang_index(yang_dir: Path, cache_dir: Path) -> YangIndex:
    cache_dir.mkdir(parents=True, exist_ok=True)
    content_hash = _compute_hash(yang_dir)
    cache_file = cache_dir / f"yang_index_{content_hash}.pkl"

    if cache_file.exists():
        # Never eat a pickle from someone you don't trust — pickle deserialization
        # can execute arbitrary code. This cache is safe because we generate it
        # ourselves from known YANG files.
        with open(cache_file, "rb") as f:
            entries = pickle.load(f)
        return YangIndex(entries)

    entries = _parse_yang_models(yang_dir)

    with open(cache_file, "wb") as f:
        pickle.dump(entries, f)

    return YangIndex(entries)
