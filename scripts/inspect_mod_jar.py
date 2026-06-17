#!/usr/bin/env python3
"""Static inventory generator for Minecraft mod jars.

This script reads a .jar as a ZIP archive. It does not execute the jar.
It extracts loader metadata, class/package distribution, mixin configs,
lang keys, recipes, tags, loot tables, advancements, worldgen files and
other data-driven content that helps a human/LLM decide where to decompile
and what gameplay systems to inspect first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except Exception:  # pragma: no cover - compatibility fallback
    tomllib = None


TEXT_EXTENSIONS = {
    ".json",
    ".mcmeta",
    ".toml",
    ".cfg",
    ".properties",
    ".accesswidener",
    ".mixins",
    ".txt",
    ".md",
}

METADATA_FILES = {
    "fabric.mod.json",
    "quilt.mod.json",
    "META-INF/mods.toml",
    "META-INF/neoforge.mods.toml",
    "META-INF/MANIFEST.MF",
}

RESOURCE_BUCKETS = {
    "recipes": re.compile(r"^data/([^/]+)/recipes/(.+)\.json$"),
    "tags": re.compile(r"^data/([^/]+)/tags/(.+)\.json$"),
    "loot_tables": re.compile(r"^data/([^/]+)/loot_tables/(.+)\.json$"),
    "advancements": re.compile(r"^data/([^/]+)/advancements/(.+)\.json$"),
    "worldgen": re.compile(r"^data/([^/]+)/worldgen/(.+)\.json$"),
    "structures": re.compile(r"^data/([^/]+)/structures/(.+)$"),
    "dimensions": re.compile(r"^data/([^/]+)/dimension(?:s)?/(.+)\.json$"),
    "dimension_types": re.compile(r"^data/([^/]+)/dimension_type/(.+)\.json$"),
    "patchouli_books": re.compile(r"^(?:data|assets)/([^/]+)/patchouli_books/(.+)$"),
    "guideme_books": re.compile(r"^(?:data|assets)/([^/]+)/guideme/(.+)$"),
    "assets_models": re.compile(r"^assets/([^/]+)/models/(.+)\.json$"),
    "assets_textures": re.compile(r"^assets/([^/]+)/textures/(.+)$"),
    "assets_sounds": re.compile(r"^assets/([^/]+)/sounds/(.+)$"),
    "assets_particles": re.compile(r"^assets/([^/]+)/particles/(.+)\.json$"),
}


class JarReadError(RuntimeError):
    pass


def normalize_entry_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def read_bytes(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> bytes:
    with zf.open(name) as fp:
        return fp.read() if limit is None else fp.read(limit)


def read_text(zf: zipfile.ZipFile, name: str, limit: int | None = None) -> str:
    data = read_bytes(zf, name, limit)
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_json(zf: zipfile.ZipFile, name: str) -> Any | None:
    try:
        return json.loads(read_text(zf, name))
    except Exception:
        return None


def safe_sample(items: list[Any], limit: int = 30) -> list[Any]:
    return items[:limit]


def compact_json_value(value: Any, max_length: int = 180) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value)
        if isinstance(value, str) and len(text) > max_length:
            return text[: max_length - 3] + "..."
        return value
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    if len(text) > max_length:
        return text[: max_length - 3] + "..."
    return value


def detect_loader(entries: set[str]) -> list[str]:
    loaders: list[str] = []
    if "fabric.mod.json" in entries:
        loaders.append("fabric")
    if "quilt.mod.json" in entries:
        loaders.append("quilt")
    if "META-INF/neoforge.mods.toml" in entries:
        loaders.append("neoforge")
    if "META-INF/mods.toml" in entries:
        loaders.append("forge")
    return loaders or ["unknown"]


def parse_fabric_metadata(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "loader": "fabric",
        "id": data.get("id"),
        "name": data.get("name"),
        "version": data.get("version"),
        "description": data.get("description"),
        "environment": data.get("environment"),
        "depends": data.get("depends", {}),
        "recommends": data.get("recommends", {}),
        "suggests": data.get("suggests", {}),
        "breaks": data.get("breaks", {}),
        "entrypoints": data.get("entrypoints", {}),
        "mixins": data.get("mixins", []),
        "access_widener": data.get("accessWidener"),
        "custom": data.get("custom", {}),
    }


def parse_quilt_metadata(data: dict[str, Any]) -> dict[str, Any]:
    loader = data.get("quilt_loader", {}) if isinstance(data.get("quilt_loader"), dict) else {}
    metadata = loader.get("metadata", {}) if isinstance(loader.get("metadata"), dict) else {}
    return {
        "loader": "quilt",
        "id": loader.get("id") or data.get("id"),
        "group": loader.get("group"),
        "name": metadata.get("name") or data.get("name"),
        "version": loader.get("version") or data.get("version"),
        "description": metadata.get("description") or data.get("description"),
        "depends": loader.get("depends", []),
        "entrypoints": data.get("entrypoints", {}),
        "mixins": data.get("mixin", []) or data.get("mixins", []),
        "access_widener": data.get("access_widener") or data.get("accessWidener"),
    }


def parse_toml_fallback(text: str) -> dict[str, Any]:
    """Very small fallback parser for the fields we care about in mods.toml."""
    result: dict[str, Any] = {"mods": [], "dependencies": {}}
    current_mod: dict[str, Any] | None = None
    current_dep_modid: str | None = None
    current_dep: dict[str, Any] | None = None

    assignment = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+)$")
    dep_header = re.compile(r"^\[\[dependencies\.([^\]]+)\]\]$")

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "[[mods]]":
            current_mod = {}
            result["mods"].append(current_mod)
            current_dep_modid = None
            current_dep = None
            continue
        dep_match = dep_header.match(line)
        if dep_match:
            current_dep_modid = dep_match.group(1)
            current_dep = {}
            result["dependencies"].setdefault(current_dep_modid, []).append(current_dep)
            current_mod = None
            continue
        match = assignment.match(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip().strip('"').strip("'")
        target = current_mod if current_mod is not None else current_dep
        if target is not None:
            target[key] = value
        else:
            result[key] = value
    return result


def parse_toml_metadata(text: str, loader: str) -> dict[str, Any]:
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except Exception:
            data = parse_toml_fallback(text)
    else:
        data = parse_toml_fallback(text)

    mods = data.get("mods", []) if isinstance(data, dict) else []
    dependencies = data.get("dependencies", {}) if isinstance(data, dict) else {}
    parsed_mods = []
    for mod in mods if isinstance(mods, list) else []:
        if not isinstance(mod, dict):
            continue
        parsed_mods.append(
            {
                "loader": loader,
                "id": mod.get("modId") or mod.get("modid") or mod.get("id"),
                "name": mod.get("displayName") or mod.get("name"),
                "version": mod.get("version"),
                "description": mod.get("description"),
                "display_url": mod.get("displayURL") or mod.get("displayUrl"),
                "authors": mod.get("authors"),
                "credits": mod.get("credits"),
            }
        )
    return {
        "loader": loader,
        "mods": parsed_mods,
        "dependencies": dependencies,
        "loader_version": data.get("loaderVersion") if isinstance(data, dict) else None,
        "license": data.get("license") if isinstance(data, dict) else None,
        "issue_tracker_url": data.get("issueTrackerURL") if isinstance(data, dict) else None,
    }


def collect_metadata(zf: zipfile.ZipFile, entries: set[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if "fabric.mod.json" in entries:
        data = read_json(zf, "fabric.mod.json")
        metadata["fabric.mod.json"] = parse_fabric_metadata(data) if isinstance(data, dict) else {"parse_error": True}
    if "quilt.mod.json" in entries:
        data = read_json(zf, "quilt.mod.json")
        metadata["quilt.mod.json"] = parse_quilt_metadata(data) if isinstance(data, dict) else {"parse_error": True}
    if "META-INF/mods.toml" in entries:
        metadata["META-INF/mods.toml"] = parse_toml_metadata(read_text(zf, "META-INF/mods.toml"), "forge")
    if "META-INF/neoforge.mods.toml" in entries:
        metadata["META-INF/neoforge.mods.toml"] = parse_toml_metadata(read_text(zf, "META-INF/neoforge.mods.toml"), "neoforge")
    if "META-INF/MANIFEST.MF" in entries:
        manifest = read_text(zf, "META-INF/MANIFEST.MF", limit=20_000)
        metadata["META-INF/MANIFEST.MF"] = {
            "interesting_lines": [
                line for line in manifest.splitlines() if any(key in line.lower() for key in ("mod", "mixin", "forge", "fabric", "tweak"))
            ][:40]
        }
    return metadata


def collect_mods(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    mods: list[dict[str, Any]] = []
    for value in metadata.values():
        if not isinstance(value, dict):
            continue
        if "mods" in value and isinstance(value["mods"], list):
            mods.extend([mod for mod in value["mods"] if isinstance(mod, dict)])
        elif value.get("id") or value.get("name"):
            mods.append({k: value.get(k) for k in ("loader", "id", "name", "version", "description", "environment") if value.get(k) is not None})
    return mods


def collect_dependencies(metadata: dict[str, Any]) -> dict[str, Any]:
    dependencies: dict[str, Any] = {}
    for filename, value in metadata.items():
        if isinstance(value, dict):
            for key in ("depends", "recommends", "suggests", "breaks", "dependencies"):
                if key in value and value[key]:
                    dependencies.setdefault(filename, {})[key] = value[key]
    return dependencies


def collect_entrypoints(metadata: dict[str, Any]) -> dict[str, Any]:
    entrypoints: dict[str, Any] = {}
    for filename, value in metadata.items():
        if isinstance(value, dict) and value.get("entrypoints"):
            entrypoints[filename] = value["entrypoints"]
    return entrypoints


def class_to_package(entry: str) -> str | None:
    if not entry.endswith(".class"):
        return None
    if entry.startswith("META-INF/"):
        return None
    if entry.endswith("module-info.class") or entry.endswith("package-info.class"):
        return None
    parts = entry[:-6].split("/")
    if len(parts) <= 1:
        return "(default)"
    return ".".join(parts[:-1])


def collect_classes(entries: list[str]) -> dict[str, Any]:
    class_entries = [entry for entry in entries if entry.endswith(".class") and not entry.startswith("META-INF/")]
    packages = [pkg for entry in class_entries if (pkg := class_to_package(entry))]
    top3 = []
    for package in packages:
        pieces = package.split(".")
        top3.append(".".join(pieces[: min(3, len(pieces))]))
    suspicious = [
        entry for entry in class_entries
        if any(token in entry.lower() for token in ("registry", "register", "init", "event", "handler", "mixin", "recipe", "screen", "menu", "blockentity", "entity", "config", "network", "packet"))
    ]
    return {
        "count": len(class_entries),
        "top_packages": Counter(packages).most_common(40),
        "top_package_roots": Counter(top3).most_common(40),
        "samples": safe_sample(class_entries, 80),
        "suspicious_name_samples": safe_sample(suspicious, 120),
    }


def collect_mixins(zf: zipfile.ZipFile, entries: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    mixin_files = set()
    for entry in entries:
        lower = entry.lower()
        if lower.endswith(".json") and ("mixin" in lower or lower.endswith(".mixins.json")):
            mixin_files.add(entry)
    for value in metadata.values():
        if not isinstance(value, dict):
            continue
        raw = value.get("mixins") or value.get("mixin")
        if isinstance(raw, str):
            mixin_files.add(raw)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    mixin_files.add(item)
                elif isinstance(item, dict) and isinstance(item.get("config"), str):
                    mixin_files.add(item["config"])

    configs = []
    for filename in sorted(mixin_files):
        if filename not in set(entries):
            configs.append({"file": filename, "missing_in_jar": True})
            continue
        data = read_json(zf, filename)
        if not isinstance(data, dict):
            configs.append({"file": filename, "parse_error": True})
            continue
        package = data.get("package")
        mixins = []
        for section in ("mixins", "client", "server"):
            raw = data.get(section, [])
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        mixins.append({"side": section, "class": f"{package}.{item}" if package else item})
        configs.append(
            {
                "file": filename,
                "package": package,
                "plugin": data.get("plugin"),
                "refmap": data.get("refmap"),
                "mixin_count": len(mixins),
                "mixins": safe_sample(mixins, 120),
            }
        )
    return {"count": len(configs), "configs": configs}


def summarize_result_field(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("id", "item", "tag", "count"):
            if key in value:
                return {k: value.get(k) for k in ("id", "item", "tag", "count") if k in value}
        return compact_json_value(value)
    if isinstance(value, list):
        return safe_sample(value, 5)
    return value


def summarize_recipe(path: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"path": path, "parse_error": True}
    ingredients = data.get("ingredients") or data.get("ingredient") or data.get("key") or data.get("pattern")
    ingredient_count = len(ingredients) if isinstance(ingredients, (list, dict)) else (1 if ingredients else 0)
    return {
        "path": path,
        "type": data.get("type"),
        "category": data.get("category"),
        "group": data.get("group"),
        "ingredient_count": ingredient_count,
        "result": summarize_result_field(data.get("result") or data.get("output") or data.get("results")),
    }


def summarize_advancement(path: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"path": path, "parse_error": True}
    display = data.get("display", {}) if isinstance(data.get("display"), dict) else {}
    criteria = data.get("criteria", {}) if isinstance(data.get("criteria"), dict) else {}
    return {
        "path": path,
        "parent": data.get("parent"),
        "title": compact_json_value(display.get("title")),
        "description": compact_json_value(display.get("description")),
        "criteria": safe_sample(list(criteria.keys()), 20),
        "rewards": compact_json_value(data.get("rewards")),
    }


def collect_resources(zf: zipfile.ZipFile, entries: list[str]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    bucket_matches: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for entry in entries:
        for bucket, pattern in RESOURCE_BUCKETS.items():
            match = pattern.match(entry)
            if match:
                namespace, rest = match.groups()
                bucket_matches[bucket].append((entry, namespace, rest))

    for bucket, matches in bucket_matches.items():
        resources[bucket] = {
            "count": len(matches),
            "namespaces": Counter(namespace for _, namespace, _ in matches).most_common(),
            "samples": safe_sample([entry for entry, _, _ in matches], 80),
        }

    # Lang files and keys
    lang_files = [entry for entry in entries if re.match(r"^assets/[^/]+/lang/[^/]+\.json$", entry)]
    lang_summary = []
    interesting_lang_keys = []
    for entry in lang_files:
        data = read_json(zf, entry)
        if isinstance(data, dict):
            keys = list(data.keys())
            interesting = [key for key in keys if key.startswith(("item.", "block.", "entity.", "effect.", "gui.", "tooltip.", "text.", "message.", "advancement."))]
            lang_summary.append({"path": entry, "key_count": len(keys), "key_samples": safe_sample(interesting or keys, 40)})
            for key in interesting[:40]:
                interesting_lang_keys.append({"file": entry, "key": key, "value": compact_json_value(data.get(key), 120)})
        else:
            lang_summary.append({"path": entry, "parse_error": True})
    resources["lang"] = {
        "count": len(lang_files),
        "files": lang_summary,
        "interesting_key_samples": safe_sample(interesting_lang_keys, 120),
    }

    # Recipe / advancement targeted summaries
    recipe_entries = [entry for entry, _, _ in bucket_matches.get("recipes", [])]
    resources.setdefault("recipes", {})["detail_samples"] = [summarize_recipe(path, read_json(zf, path)) for path in recipe_entries[:80]]

    advancement_entries = [entry for entry, _, _ in bucket_matches.get("advancements", [])]
    resources.setdefault("advancements", {})["detail_samples"] = [summarize_advancement(path, read_json(zf, path)) for path in advancement_entries[:80]]

    # Tags by registry category
    tag_categories = []
    for entry, namespace, rest in bucket_matches.get("tags", []):
        category = rest.split("/", 1)[0]
        tag_categories.append(f"{namespace}:{category}")
    resources.setdefault("tags", {})["category_counts"] = Counter(tag_categories).most_common(40)

    # Top-level namespaces under assets and data
    asset_namespaces = [match.group(1) for entry in entries if (match := re.match(r"^assets/([^/]+)/", entry))]
    data_namespaces = [match.group(1) for entry in entries if (match := re.match(r"^data/([^/]+)/", entry))]
    resources["namespaces"] = {
        "assets": Counter(asset_namespaces).most_common(),
        "data": Counter(data_namespaces).most_common(),
    }
    return resources


def collect_access_transformers(entries: list[str]) -> dict[str, Any]:
    access_wideners = [entry for entry in entries if entry.endswith(".accesswidener")]
    access_transformers = [entry for entry in entries if "accesstransformer" in entry.lower() or entry.lower().endswith("_at.cfg")]
    return {
        "access_wideners": access_wideners,
        "access_transformers": access_transformers,
    }


def collect_file_overview(entries: list[str]) -> dict[str, Any]:
    suffix_counter = Counter(Path(entry).suffix.lower() or "(no suffix)" for entry in entries if not entry.endswith("/"))
    top_dirs = Counter(entry.split("/", 1)[0] for entry in entries if entry)
    text_files = [entry for entry in entries if Path(entry).suffix.lower() in TEXT_EXTENSIONS]
    return {
        "entry_count": len(entries),
        "extension_counts": suffix_counter.most_common(50),
        "top_level_dirs": top_dirs.most_common(30),
        "text_file_samples": safe_sample(text_files, 120),
    }


def inspect_jar(jar_path: Path) -> dict[str, Any]:
    if not jar_path.exists():
        raise JarReadError(f"Jar does not exist: {jar_path}")
    if not zipfile.is_zipfile(jar_path):
        raise JarReadError(f"Not a valid jar/zip file: {jar_path}")

    with zipfile.ZipFile(jar_path) as zf:
        entries = sorted(normalize_entry_name(info.filename) for info in zf.infolist() if not info.is_dir())
        entry_set = set(entries)
        metadata = collect_metadata(zf, entry_set)
        inventory = {
            "jar": {
                "path": str(jar_path),
                "file_name": jar_path.name,
                "size_bytes": jar_path.stat().st_size,
            },
            "detected_loaders": detect_loader(entry_set),
            "metadata": metadata,
            "mods": collect_mods(metadata),
            "dependencies": collect_dependencies(metadata),
            "entrypoints": collect_entrypoints(metadata),
            "classes": collect_classes(entries),
            "mixins": collect_mixins(zf, entries, metadata),
            "access": collect_access_transformers(entries),
            "resources": collect_resources(zf, entries),
            "file_overview": collect_file_overview(entries),
            "analysis_hints": build_analysis_hints(metadata, entries),
        }
        return inventory


def build_analysis_hints(metadata: dict[str, Any], entries: list[str]) -> list[str]:
    hints: list[str] = []
    entrypoints = collect_entrypoints(metadata)
    if entrypoints:
        hints.append("优先从 metadata.entrypoints 指向的初始化类开始读。")
    if any("mods.toml" in key for key in metadata):
        hints.append("Forge/NeoForge 项目：反编译后搜索 @Mod、DeferredRegister、RegistryObject、@SubscribeEvent。")
    if any(entry.endswith(".mixins.json") or "mixin" in entry.lower() for entry in entries):
        hints.append("存在 mixin：需要单独追踪注入目标，判断对原版行为的玩家可见影响。")
    if any(entry.startswith("data/") and "/recipes/" in entry for entry in entries):
        hints.append("存在数据包配方：核心循环不要只看代码，必须读 recipes 和自定义 recipe type。")
    if any(entry.startswith("data/") and "/worldgen/" in entry for entry in entries):
        hints.append("存在 worldgen：资源获取/探索流程可能从矿物、特征、结构或维度开始。")
    if any("patchouli_books" in entry or "guideme" in entry.lower() for entry in entries):
        hints.append("存在内置手册：先读手册可还原作者预期的新手流程。")
    if any("screen" in entry.lower() or "menu" in entry.lower() for entry in entries):
        hints.append("存在 GUI/Menu 相关类：需要追踪玩家交互、槽位、按钮、网络包。")
    if not hints:
        hints.append("未发现明显入口提示；从 metadata、lang key、recipes、class suspicious samples 逐步定位。")
    return hints


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Create a static inventory for a Minecraft mod jar without executing it.")
    parser.add_argument("jar", help="Path to the mod .jar file")
    parser.add_argument("-o", "--out", help="Output JSON path. If omitted, prints JSON to stdout.")
    args = parser.parse_args(argv)

    jar_path = Path(args.jar).expanduser().resolve()
    try:
        inventory = inspect_jar(jar_path)
    except JarReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    text = json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=False)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        mod_names = ", ".join(filter(None, [str(mod.get("id") or mod.get("name")) for mod in inventory.get("mods", [])])) or "unknown"
        print(f"Wrote inventory: {out_path}")
        print(f"Detected loaders: {', '.join(inventory.get('detected_loaders', []))}")
        print(f"Detected mods: {mod_names}")
        print(f"Classes: {inventory['classes']['count']}; recipes: {inventory['resources'].get('recipes', {}).get('count', 0)}; lang files: {inventory['resources'].get('lang', {}).get('count', 0)}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
