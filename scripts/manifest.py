"""Helper for assembling per-world JSON manifests in the MultiworldGG-Index repo.

Source `archipelago.json` files in per-world repos are valid JSON but unformatted —
varying whitespace, key ordering, presence of optional fields. This module reads
them through `json.load` (so formatting differences are erased) and writes a
canonical `worlds/<slug>.json` with two fields appended:

    `igdb_id`         — IGDB game id (int) used by the Greg/IGDB-enrich Action
    `module_location` — pip-installable URL or path where the world can be found

Both Phase-1 backfill (mass import from the worlds-mirror branch) and the
per-world repo's `publish-to-index.yml` Action call this module.

CLI usage:

    python -m scripts.manifest \\
        --slug oot \\
        --archipelago-json /path/to/archipelago.json \\
        --output-dir worlds/ \\
        [--module-location URL] \\
        [--igdb-id INT]

When `--module-location` is omitted, the default template is filled in with the
slug. When `--igdb-id` is omitted and the source manifest already contains one,
it is preserved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


DEFAULT_MODULE_LOCATION_TEMPLATE = (
    "https://github.com/MultiworldGG/MultiworldGG/tree/worlds-mirror/worlds/{slug}"
)


def add_world_metadata(
    manifest: dict,
    *,
    slug: str,
    module_location: Optional[str] = None,
    igdb_id: Optional[int] = None,
    module_location_template: str = DEFAULT_MODULE_LOCATION_TEMPLATE,
) -> dict:
    """Return a new manifest dict with `module_location` and (optionally) `igdb_id` added.

    `module_location` is always written; if not supplied, the template is filled
    with the slug. `igdb_id` is written only when supplied AND not already present
    in the source manifest — this preserves human-edited values.

    The source manifest is not mutated.
    """
    out = dict(manifest)
    if module_location is None:
        module_location = module_location_template.format(slug=slug)
    out["module_location"] = module_location
    if igdb_id is not None and "igdb_id" not in out:
        out["igdb_id"] = igdb_id
    return out


def read_archipelago_json(path: Path) -> dict:
    """Parse an archipelago.json from disk. Raises on missing file or invalid JSON."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_world_manifest(manifest: dict, slug: str, output_dir: Path) -> Path:
    """Write `<output_dir>/<slug>.json` with canonical formatting (4-space indent, trailing newline).

    Key order is preserved from the input dict (Python preserves insertion order
    in 3.7+), so callers control field ordering by how they construct the dict.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{slug}.json"
    with open(target, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        f.write("\n")
    return target


def build_world_manifest(
    archipelago_json_path: Path,
    *,
    slug: str,
    module_location: Optional[str] = None,
    igdb_id: Optional[int] = None,
) -> dict:
    """Convenience: read an archipelago.json and add the metadata fields. Returns the dict."""
    src = read_archipelago_json(archipelago_json_path)
    return add_world_metadata(
        src,
        slug=slug,
        module_location=module_location,
        igdb_id=igdb_id,
    )


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a worlds/<slug>.json manifest from a per-world archipelago.json.",
    )
    parser.add_argument("--slug", required=True, help="World slug (e.g. 'oot')")
    parser.add_argument(
        "--archipelago-json",
        required=True,
        type=Path,
        help="Path to the source archipelago.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("worlds"),
        help="Output directory (default: worlds/)",
    )
    parser.add_argument(
        "--module-location",
        default=None,
        help=(
            "Override module_location URL. Default: "
            f"{DEFAULT_MODULE_LOCATION_TEMPLATE}"
        ),
    )
    parser.add_argument(
        "--igdb-id",
        type=int,
        default=None,
        help="IGDB game id; omitted if the source manifest already has one",
    )
    args = parser.parse_args(argv)

    manifest = build_world_manifest(
        args.archipelago_json,
        slug=args.slug,
        module_location=args.module_location,
        igdb_id=args.igdb_id,
    )
    target = write_world_manifest(manifest, args.slug, args.output_dir)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
