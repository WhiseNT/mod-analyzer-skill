#!/usr/bin/env python3
"""Small wrapper for decompiling Minecraft mod jars.

The script does not execute the mod jar. It invokes a user-provided, locally
found, or explicitly downloaded decompiler jar and writes decompiled sources to
an output directory.

Downloader notes:
- Downloads are opt-in via --download-decompiler.
- Artifacts come from Maven Central.
- SHA-1 sidecar files are checked when available.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


COMMON_SEARCH_DIRS = [
    Path.cwd(),
    Path.cwd() / "tools",
    Path.cwd() / "lib",
    Path.cwd() / "decompilers",
    Path.cwd() / "tools" / "decompilers",
    Path.home() / ".local" / "share" / "decompilers",
]

DECOMPILER_ENV_VARS = {
    "cfr": "CFR_JAR",
    "vineflower": "VINEFLOWER_JAR",
    "fernflower": "FERNFLOWER_JAR",
}

MAVEN_CENTRAL_BASE = "https://repo.maven.apache.org/maven2"
MAVEN_ARTIFACTS = {
    "vineflower": {
        "group": "org.vineflower",
        "artifact": "vineflower",
        "classifier": None,
        "extension": "jar",
    },
    "cfr": {
        "group": "org.benf",
        "artifact": "cfr",
        "classifier": None,
        "extension": "jar",
    },
}


class DownloadError(RuntimeError):
    pass


def configure_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def infer_decompiler(path: Path) -> str:
    name = path.name.lower()
    if "cfr" in name:
        return "cfr"
    if "vineflower" in name:
        return "vineflower"
    if "fernflower" in name or "forgeflower" in name:
        return "fernflower"
    return "unknown"


def find_decompiler(requested: str, extra_search_dir: Path | None = None) -> Path | None:
    candidates: list[Path] = []

    names = [requested] if requested != "auto" else ["vineflower", "cfr", "fernflower"]
    for name in names:
        env_var = DECOMPILER_ENV_VARS.get(name)
        if env_var and os.environ.get(env_var):
            candidates.append(Path(os.environ[env_var]).expanduser())

    patterns = {
        "cfr": ["*cfr*.jar"],
        "vineflower": ["*vineflower*.jar"],
        "fernflower": ["*fernflower*.jar", "*forgeflower*.jar"],
    }
    search_dirs = list(COMMON_SEARCH_DIRS)
    if extra_search_dir is not None:
        search_dirs.insert(0, extra_search_dir)

    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for name in names:
            for pattern in patterns.get(name, []):
                candidates.extend(directory.glob(pattern))

    # Prefer newer-looking file names within the same directory/pattern by mtime.
    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".jar":
            return candidate.resolve()
    return None


def maven_path(group: str, artifact: str, version: str, extension: str = "jar", classifier: str | None = None) -> str:
    group_path = group.replace(".", "/")
    suffix = f"-{classifier}" if classifier else ""
    return f"{group_path}/{artifact}/{version}/{artifact}-{version}{suffix}.{extension}"


def fetch_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "mod-analyzer-skill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_bytes(url: str, timeout: int = 180) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "mod-analyzer-skill/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_maven_version(group: str, artifact: str) -> str:
    metadata_url = f"{MAVEN_CENTRAL_BASE}/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
    try:
        metadata = fetch_text(metadata_url)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DownloadError(f"failed to fetch Maven metadata: {metadata_url}: {exc}") from exc

    try:
        root = ET.fromstring(metadata)
    except ET.ParseError as exc:
        raise DownloadError(f"failed to parse Maven metadata for {group}:{artifact}: {exc}") from exc

    versioning = root.find("versioning")
    if versioning is not None:
        for tag in ("release", "latest"):
            elem = versioning.find(tag)
            if elem is not None and elem.text and elem.text.strip():
                return elem.text.strip()
        versions = versioning.find("versions")
        if versions is not None:
            all_versions = [node.text.strip() for node in versions.findall("version") if node.text and node.text.strip()]
            if all_versions:
                return all_versions[-1]
    raise DownloadError(f"no version found in Maven metadata for {group}:{artifact}")


def verify_sha1(path: Path, expected: str) -> bool:
    actual = hashlib.sha1(path.read_bytes()).hexdigest()
    return actual.lower() == expected.strip().split()[0].lower()


def download_maven_artifact(name: str, tools_dir: Path, version: str | None = None) -> Path:
    if name == "auto":
        name = "vineflower"
    if name not in MAVEN_ARTIFACTS:
        raise DownloadError(f"download is supported only for: {', '.join(MAVEN_ARTIFACTS)}")

    artifact = MAVEN_ARTIFACTS[name]
    group = artifact["group"]
    artifact_id = artifact["artifact"]
    extension = artifact["extension"]
    classifier = artifact["classifier"]
    selected_version = version or latest_maven_version(group, artifact_id)

    relative_path = maven_path(group, artifact_id, selected_version, extension, classifier)
    jar_url = f"{MAVEN_CENTRAL_BASE}/{relative_path}"
    sha1_url = jar_url + ".sha1"
    tools_dir.mkdir(parents=True, exist_ok=True)
    out_path = tools_dir / Path(relative_path).name

    if out_path.exists() and out_path.stat().st_size > 0:
        print(f"Using cached {name} decompiler: {out_path}")
        return out_path.resolve()

    print(f"Downloading {name} {selected_version} from Maven Central...")
    print(f"URL: {jar_url}")
    try:
        data = fetch_bytes(jar_url)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise DownloadError(f"failed to download decompiler jar: {exc}") from exc

    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_bytes(data)

    try:
        expected_sha1 = fetch_text(sha1_url).strip()
    except Exception as exc:
        print(f"warning: could not fetch SHA-1 sidecar; keeping downloaded jar without checksum verification: {exc}", file=sys.stderr)
    else:
        if not verify_sha1(tmp_path, expected_sha1):
            tmp_path.unlink(missing_ok=True)
            raise DownloadError(f"SHA-1 verification failed for {jar_url}")
        print("SHA-1 verification passed.")

    tmp_path.replace(out_path)
    return out_path.resolve()


def build_command(decompiler: str, decompiler_jar: Path, input_jar: Path, out_dir: Path) -> list[str]:
    if decompiler == "cfr":
        return [
            "java",
            "-jar",
            str(decompiler_jar),
            str(input_jar),
            "--outputdir",
            str(out_dir),
            "--caseinsensitivefs",
            "true",
        ]
    if decompiler in {"vineflower", "fernflower", "unknown"}:
        return ["java", "-jar", str(decompiler_jar), str(input_jar), str(out_dir)]
    raise ValueError(f"Unsupported decompiler: {decompiler}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Decompile a Minecraft mod jar using a Java decompiler jar.")
    parser.add_argument("jar", help="Path to the Minecraft mod .jar")
    parser.add_argument("-o", "--out-dir", required=True, help="Output directory for decompiled sources")
    parser.add_argument("--decompiler", choices=["auto", "cfr", "vineflower", "fernflower"], default="auto", help="Decompiler type. auto prefers Vineflower, then CFR, then FernFlower.")
    parser.add_argument("--decompiler-jar", help="Path to cfr/vineflower/fernflower jar. If omitted, searches env vars and common local tool dirs.")
    parser.add_argument("--download-decompiler", choices=["auto", "vineflower", "cfr"], help="If no local decompiler is found, download this decompiler from Maven Central. auto downloads Vineflower.")
    parser.add_argument("--decompiler-version", help="Specific Maven artifact version to download. If omitted, uses Maven metadata release/latest.")
    parser.add_argument("--tools-dir", default="tools/decompilers", help="Directory used to search/cache downloaded decompiler jars")
    parser.add_argument("--dry-run", action="store_true", help="Print the command without running it. Downloads may still occur if --download-decompiler is used and no local jar exists.")
    args = parser.parse_args(argv)

    input_jar = Path(args.jar).expanduser().resolve()
    if not input_jar.exists():
        print(f"error: input jar does not exist: {input_jar}", file=sys.stderr)
        return 2

    tools_dir = Path(args.tools_dir).expanduser().resolve()

    if args.decompiler_jar:
        decompiler_jar = Path(args.decompiler_jar).expanduser().resolve()
    else:
        decompiler_jar = find_decompiler(args.decompiler, tools_dir)
        if decompiler_jar is None and args.download_decompiler:
            try:
                decompiler_jar = download_maven_artifact(args.download_decompiler, tools_dir, args.decompiler_version)
            except DownloadError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        if decompiler_jar is None:
            print(
                "error: no local decompiler jar found. Provide --decompiler-jar, set CFR_JAR / VINEFLOWER_JAR / FERNFLOWER_JAR, "
                "or add --download-decompiler vineflower.",
                file=sys.stderr,
            )
            return 2

    if not decompiler_jar.exists():
        print(f"error: decompiler jar does not exist: {decompiler_jar}", file=sys.stderr)
        return 2

    decompiler = args.decompiler if args.decompiler != "auto" else infer_decompiler(decompiler_jar)
    if decompiler == "unknown" and args.download_decompiler in {"vineflower", "cfr"}:
        decompiler = args.download_decompiler
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    command = build_command(decompiler, decompiler_jar, input_jar, out_dir)
    print("Decompiler:", decompiler)
    print("Decompiler jar:", decompiler_jar)
    print("Command:", " ".join(f'\"{part}\"' if " " in part else part for part in command))

    if args.dry_run:
        return 0

    result = subprocess.run(command)
    if result.returncode != 0:
        print(f"error: decompiler exited with code {result.returncode}", file=sys.stderr)
        return result.returncode

    print(f"Decompiled sources written to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
