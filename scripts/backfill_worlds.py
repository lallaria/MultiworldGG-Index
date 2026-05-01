"""Phase-1 backfill of worlds/*.json from a checked-out per-world archipelago.json snapshot.

Reads every `<source>/worlds/<slug>/archipelago.json`, attaches the IGDB id from the
historical game_details.json (when present), and writes a normalized `worlds/<slug>.json`
in the Index repo via `scripts.manifest.write_world_manifest`.

Skips infrastructure worlds (`_*` prefix and `generic`) and worlds that don't have an
`archipelago.json` (those need to be handled out-of-band).

Run from the Index repo root:

    python scripts/backfill_worlds.py \\
        --source C:/Users/Lindsay/source/repos/MultiworldGG-gui-changes \\
        --igdb-data C:/Users/Lindsay/source/repos/MultiworldGG/src/tools/game_indexing/output/game_details.json \\
        [--dry-run]

`--module-location-template` can override the default URL pattern.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# scripts/ is on the path when run as a module from the repo root; otherwise add the parent.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from manifest import (  # noqa: E402
    DEFAULT_MODULE_LOCATION_TEMPLATE,
    add_world_metadata,
    read_archipelago_json,
    write_world_manifest,
)


INFRA_PREFIX = "_"
SKIP_SLUGS = {"generic"}


def discover_slugs(source_root: Path) -> list[str]:
    """Return slugs that have a `worlds/<slug>/archipelago.json` and are not infra."""
    worlds_dir = source_root / "worlds"
    if not worlds_dir.is_dir():
        raise FileNotFoundError(f"{worlds_dir} does not exist")
    out: list[str] = []
    for child in sorted(worlds_dir.iterdir()):
        if not child.is_dir():
            continue
        slug = child.name
        if slug.startswith(INFRA_PREFIX) or slug in SKIP_SLUGS:
            continue
        if (child / "archipelago.json").is_file():
            out.append(slug)
    return out


def load_igdb_ids(igdb_data_path: Path) -> dict[str, int]:
    """Read the historical game_details.json and return a `slug -> igdb_id` mapping."""
    with open(igdb_data_path, encoding="utf-8") as f:
        d = json.load(f)
    out: dict[str, int] = {}
    for slug, meta in d.items():
        igdb_id = meta.get("igdb_id")
        if igdb_id is None or igdb_id == "":
            continue
        try:
            out[slug] = int(igdb_id)
        except (TypeError, ValueError):
            continue
    return out


def backfill(
    source_root: Path,
    output_dir: Path,
    *,
    igdb_data_path: Optional[Path] = None,
    module_location_template: str = DEFAULT_MODULE_LOCATION_TEMPLATE,
    dry_run: bool = False,
) -> dict[str, str]:
    """Run the backfill. Returns a mapping `slug -> status` where status is
    "wrote", "skipped:no-archipelago-json", or "would-write" (dry-run).
    """
    slugs = discover_slugs(source_root)
    igdb_ids = load_igdb_ids(igdb_data_path) if igdb_data_path else {}

    results: dict[str, str] = {}
    for slug in slugs:
        archipelago_json = source_root / "worlds" / slug / "archipelago.json"
        try:
            src = read_archipelago_json(archipelago_json)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            results[slug] = f"skipped:{type(exc).__name__}"
            continue
        igdb_id = igdb_ids.get(slug)
        manifest = add_world_metadata(
            src,
            slug=slug,
            igdb_id=igdb_id,
            module_location_template=module_location_template,
        )
        if dry_run:
            results[slug] = "would-write"
        else:
            write_world_manifest(manifest, slug, output_dir)
            results[slug] = "wrote"
    return results


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-1 backfill of worlds/*.json from archipelago.json files.")
    parser.add_argument(
        "--source",
        required=True,
        type=Path,
        help="Path to a checkout of MultiworldGG (with worlds/<slug>/archipelago.json files)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("worlds"),
        help="Output directory (default: worlds/)",
    )
    parser.add_argument(
        "--igdb-data",
        type=Path,
        default=None,
        help="Path to game_details.json containing slug->igdb_id mappings",
    )
    parser.add_argument(
        "--module-location-template",
        default=DEFAULT_MODULE_LOCATION_TEMPLATE,
        help="URL template for module_location field; default: %(default)s",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write; just print what would happen")
    args = parser.parse_args(argv)

    results = backfill(
        args.source,
        args.output_dir,
        igdb_data_path=args.igdb_data,
        module_location_template=args.module_location_template,
        dry_run=args.dry_run,
    )

    wrote = sum(1 for s in results.values() if s in ("wrote", "would-write"))
    skipped = sum(1 for s in results.values() if s.startswith("skipped"))
    print(f"{'(dry-run) ' if args.dry_run else ''}wrote {wrote}, skipped {skipped}")
    if skipped:
        print("skipped slugs:")
        for slug, status in sorted(results.items()):
            if status.startswith("skipped"):
                print(f"  {slug}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
