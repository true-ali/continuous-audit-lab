"""Download the Southlake payments-to-suppliers CSV exports with a checksum manifest for re-runs."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

CKAN_URL = (
    "https://ckan.publishing.service.gov.uk/api/3/action/package_show"
    "?id=payments-to-suppliers-over-250"
)
DEFAULT_DEST_DIR = "data/raw/southlakeland"
DEFAULT_MANIFEST_PATH = "data/raw/southlakeland/checksums.json"
MAX_TOTAL_BYTES = 500 * 1024 * 1024
TIMEOUT_SECONDS = 120
CHUNK_BYTES = 1024 * 1024


def list_csv_resources(session: requests.Session | None = None) -> list[dict[str, str]]:
    """Read the CSV list from CKAN metadata so the file set follows the portal, not a fixed list."""
    if session is None:
        session = requests.Session()
    response = session.get(CKAN_URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    resources = response.json()["result"]["resources"]
    return [
        {"name": resource.get("name", ""), "url": resource["url"]}
        for resource in resources
        if urlparse(resource.get("url", "")).path.lower().endswith(".csv")
    ]


def sha256_of(path: str | Path) -> str:
    """Hash in chunks so large exports are never loaded fully into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filename_for(url: str) -> str:
    """Prefix the basename with its media id so same-named exports from different uploads differ."""
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    basename = unquote(segments[-1])
    if len(segments) < 2:
        return basename
    return f"{segments[-2]}_{basename}"


def load_manifest(manifest_path: Path) -> dict[str, dict[str, Any]]:
    """Return the previous run's manifest, or an empty one on the first run."""
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def is_current(path: Path, entry: dict[str, Any] | None) -> bool:
    """Reuse a local file only if its hash matches the manifest, so corrupt files are refetched."""
    return entry is not None and path.exists() and sha256_of(path) == entry["sha256"]


def planned_size(session: requests.Session, resources: list[dict[str, str]]) -> int:
    """Sum Content-Length via HEAD so the size guard runs before any file body is downloaded."""
    total = 0
    for resource in resources:
        response = session.head(resource["url"], allow_redirects=True, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        total += int(response.headers.get("Content-Length", 0))
    return total


def download_file(session: requests.Session, url: str, path: Path) -> None:
    """Write to a .part file then rename, so an interrupted download never looks finished."""
    partial = path.with_name(path.name + ".part")
    response = session.get(url, stream=True, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    with partial.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
            handle.write(chunk)
    partial.replace(path)


def download_all(
    dest_dir: str = DEFAULT_DEST_DIR,
    manifest_path: str = DEFAULT_MANIFEST_PATH,
    dry_run: bool = False,
    session: requests.Session | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch missing or stale CSVs and record name, URL, sha256 and size in the manifest."""
    if session is None:
        session = requests.Session()
    resources = list_csv_resources(session)
    total_bytes = planned_size(session, resources)
    print(f"{len(resources)} files, {total_bytes / 1_000_000:.1f} MB total")
    if total_bytes > MAX_TOTAL_BYTES and not force:
        print(
            "Warning: total size exceeds 500 MB; rerun with --force to download anyway.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if dry_run:
        return {
            "files": len(resources),
            "total_bytes": total_bytes,
            "downloaded": [],
            "skipped": [],
        }

    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = Path(manifest_path)
    manifest = load_manifest(manifest_file)
    downloaded: list[str] = []
    skipped: list[str] = []
    for resource in resources:
        name = filename_for(resource["url"])
        path = destination / name
        if is_current(path, manifest.get(name)):
            skipped.append(name)
            continue
        download_file(session, resource["url"], path)
        downloaded.append(name)
        manifest[name] = {
            "url": resource["url"],
            "sha256": sha256_of(path),
            "bytes": path.stat().st_size,
        }
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "files": len(resources),
        "total_bytes": total_bytes,
        "downloaded": downloaded,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> None:
    """Parse the CLI flags and run the download, printing a short summary."""
    parser = argparse.ArgumentParser(description="Download Southlake payments-to-suppliers CSVs.")
    parser.add_argument("--dry-run", action="store_true", help="only report file count and size")
    parser.add_argument(
        "--force", action="store_true", help="download even if total exceeds 500 MB"
    )
    args = parser.parse_args(argv)
    summary = download_all(dry_run=args.dry_run, force=args.force)
    if not args.dry_run:
        downloaded_count = len(summary["downloaded"])
        skipped_count = len(summary["skipped"])
        print(f"Done: {downloaded_count} downloaded, {skipped_count} already current.")


if __name__ == "__main__":
    main()
