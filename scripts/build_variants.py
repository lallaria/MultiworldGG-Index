"""Variant build for the MultiworldGG game-index orphan branches.

Reads the per-world manifests (`worlds/*.json`) and the staged IGDB metadata
(`output/igdb_enrichment.json`), merges them per-slug, applies the four age
filters, and emits a complete pip-installable package per variant under
`dist/<variant>/`. The daily-release workflow takes each `dist/<variant>/`
directory and force-pushes its contents to the corresponding orphan branch
(`game_index_<variant>`).

All four variants publish a package named `mwgg_igdb` regardless of which
orphan branch is the source. Variant choice is reflected in the orphan-branch
name and the package version, not in the import path — so consumer code keeps
working with `from mwgg_igdb import GameIndex` no matter which variant is
installed.

Run from the Index repo root:

    python scripts/build_variants.py [--worlds-dir worlds] \\
                                     [--enrichment output/igdb_enrichment.json] \\
                                     [--template scripts/game_index_template.py] \\
                                     [--out-dir dist] \\
                                     [--version YYYY.MM.DD] \\
                                     [--variant nr|ao|twelve|sixteen]

If `--version` is omitted, today's date is used (UTC).
If `--variant` is omitted, all four are built.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

# All known age-rating tokens. Keep in lockstep with how upstream source data
# tags games; the daily-release workflow flags slugs with unrecognized values.
KNOWN_RATINGS = {"MW", "3", "7", "12", "16", "18", "E", "T", "M", "NR", "AO"}

VARIANT_FILTERS: dict[str, set[str]] = {
    # No-rating: everything except Adults Only
    "nr": {"MW", "3", "7", "12", "16", "18", "E", "T", "M", "NR"},
    # Adults-Only: everything including AO
    "ao": {"MW", "3", "7", "12", "16", "18", "E", "T", "M", "NR", "AO"},
    # 16+: caps at "T"/16
    "sixteen": {"MW", "3", "7", "12", "16", "E", "T"},
    # 12+: caps at "12"
    "twelve": {"MW", "3", "7", "12", "E"},
}

VARIANT_DESCRIPTIONS: dict[str, str] = {
    "nr": "MultiworldGG game index — No-Rating variant (excludes Adults Only)",
    "ao": "MultiworldGG game index — Adults-Only variant (full data)",
    "sixteen": "MultiworldGG game index — 16+ variant",
    "twelve": "MultiworldGG game index — 12+ variant",
}

# IGDB-derived fields that come from the enrichment file. Anything not listed
# here that the consumer expects to be on a GAMES_DATA entry must be sourced
# from worlds/<slug>.json instead.
ENRICHMENT_FIELDS = (
    "cover_url",
    "artwork_url",
    "key_art_url",
    "igdb_name",
    "age_rating",
    "rating",
    "player_perspectives",
    "genres",
    "themes",
    "platforms",
    "storyline",
    "keywords",
    "release_date",
)

# Defaults used when a slug is in worlds/ but missing from enrichment.
ENRICHMENT_DEFAULTS = {
    "cover_url": "",
    "artwork_url": "",
    "key_art_url": "",
    "igdb_name": "",
    "age_rating": "NR",
    "rating": [],
    "player_perspectives": [],
    "genres": [],
    "themes": [],
    "platforms": [],
    "storyline": "",
    "keywords": [],
    "release_date": None,
}

# Fields indexed for substring search. Mirrors the monorepo behavior.
SEARCHABLE_FIELDS = {"igdb_name", "platforms", "genres", "themes", "keywords", "player_perspectives"}

# Slugs always exposed as "popular".
POPULAR_SLUGS = {"alttp", "sc2", "oot", "kh2", "hk", "sm64ex"}


def load_world_manifests(worlds_dir: Path) -> dict[str, dict]:
    """Read every `worlds/*.json` and return a `slug -> manifest` dict.
    Slug = filename stem; the JSON content is the manifest."""
    out: dict[str, dict] = {}
    for path in sorted(worlds_dir.glob("*.json")):
        slug = path.stem
        with open(path, encoding="utf-8") as f:
            out[slug] = json.load(f)
    return out


def load_enrichment(path: Path) -> dict[str, dict]:
    """Read the staged IGDB enrichment file. Returns `slug -> metadata`."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assemble_games_data(
    manifests: dict[str, dict],
    enrichment: dict[str, dict],
) -> dict[str, dict]:
    """Merge per-world manifests with IGDB enrichment into the GAMES_DATA shape.

    Each output entry contains: igdb_id, cover_url, artwork_url, key_art_url,
    game_name, igdb_name, age_rating, rating, player_perspectives, genres,
    themes, platforms, storyline, keywords, release_date, module_location.
    """
    out: dict[str, dict] = {}
    for slug, manifest in manifests.items():
        e = enrichment.get(slug, {})
        entry: dict = {}
        # IGDB id: prefer manifest (authoritative), fall back to enrichment.
        igdb_id = manifest.get("igdb_id", e.get("igdb_id", ""))
        # Coerce to string for backward compat with existing GAMES_DATA shape.
        entry["igdb_id"] = str(igdb_id) if igdb_id not in (None, "") else ""
        # IGDB-derived fields.
        for field in ENRICHMENT_FIELDS:
            entry[field] = e.get(field, ENRICHMENT_DEFAULTS[field])
        # Display name: from the per-world manifest's `game` field.
        entry["game_name"] = manifest.get("game", e.get("game_name", slug))
        # Source location for the world (new field, not in legacy GAMES_DATA).
        if "module_location" in manifest:
            entry["module_location"] = manifest["module_location"]
        out[slug] = entry
    return out


def filter_for_variant(games_data: dict[str, dict], variant: str) -> dict[str, dict]:
    """Return games_data restricted to the variant's allowed age ratings."""
    allowed = VARIANT_FILTERS[variant]
    return {
        slug: data
        for slug, data in games_data.items()
        if data.get("age_rating", "NR") in allowed
    }


def _add_to_index(index: dict[str, set[str]], term: str, slug: str) -> None:
    term = "" if term is None else str(term).lower()
    if not term:
        return
    index.setdefault(term, set()).add(slug)


def build_search_index(games_data: dict[str, dict]) -> dict[str, set[str]]:
    """Generate the search index: term -> set of slugs.
    Mirrors the monorepo logic, with a `popular` curated set seeded first."""
    index: dict[str, set[str]] = {"popular": {s for s in POPULAR_SLUGS if s in games_data}}
    skip_pattern = re.compile(r".*[():].*")
    for slug, data in games_data.items():
        _add_to_index(index, data.get("game_name", ""), slug)
        for field, value in data.items():
            if field not in SEARCHABLE_FIELDS:
                continue
            if isinstance(value, list):
                for item in set(value or ()):
                    if not item or skip_pattern.match(str(item)):
                        continue
                    cleaned = str(item).lower()
                    _add_to_index(index, cleaned, slug)
                    for word in cleaned.split():
                        _add_to_index(index, word, slug)
            elif isinstance(value, (str, int, float, bool)):
                if value:
                    cleaned = str(value).lower()
                    _add_to_index(index, cleaned, slug)
                    for word in cleaned.split():
                        _add_to_index(index, word, slug)
    return index


def build_game_names(games_data: dict[str, dict]) -> dict[str, str]:
    """Return `display_name -> slug` for every game."""
    return {data["game_name"]: slug for slug, data in games_data.items() if data.get("game_name")}


def render_template(
    template_path: Path,
    games_data: dict[str, dict],
    game_names: dict[str, str],
    search_index: dict[str, set[str]],
) -> str:
    template = template_path.read_text(encoding="utf-8")
    games_data_str = json.dumps(games_data, indent=4, ensure_ascii=False)
    game_names_str = json.dumps(game_names, indent=4, ensure_ascii=False)
    # The template represents search_index as Python sets; emit JSON list
    # syntax then convert [] -> {} so python eval-time parses as set literals.
    search_index_serializable = {term: sorted(slugs) for term, slugs in search_index.items()}
    search_index_str = json.dumps(search_index_serializable, indent=4, ensure_ascii=False).replace("[", "{").replace("]", "}")
    return (
        template
        .replace("GAMES_DATA_PLACEHOLDER", games_data_str)
        .replace("GAMES_NAMES_PLACEHOLDER", game_names_str)
        .replace("SEARCH_INDEX_PLACEHOLDER", search_index_str)
    )


def write_pyproject(out_dir: Path, version: str, description: str) -> None:
    pyproject = f'''[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "mwgg_igdb"
version = "{version}"
description = "{description}"
authors = [{{name = "MultiworldGG"}}]
requires-python = ">=3.8"
classifiers = [
    "Private :: Do Not Upload"
]

[tool.setuptools]
py-modules = ["mwgg_igdb"]
'''
    (out_dir / "pyproject.toml").write_text(pyproject, encoding="utf-8")


def write_readme(out_dir: Path, variant: str, version: str) -> None:
    readme = f'''# mwgg_igdb — {variant} variant ({version})

This branch is the published `{variant}` variant of the MultiworldGG game index.
It is force-pushed by the daily-release workflow in the Index repo and tagged
on each push as `{variant}-YYYY-MM-DD`.

Install with:

```
pip install git+ssh://git@github.com/lallaria/MultiworldGG-Index@game_index_{variant}
```

The package exports a `_GameIndexClass` singleton at `mwgg_igdb.GameIndex`.
'''
    (out_dir / "README.md").write_text(readme, encoding="utf-8")


def build_one_variant(
    variant: str,
    *,
    games_data: dict[str, dict],
    template_path: Path,
    out_dir: Path,
    version: str,
) -> dict:
    filtered = filter_for_variant(games_data, variant)
    search_index = build_search_index(filtered)
    game_names = build_game_names(filtered)
    rendered = render_template(template_path, filtered, game_names, search_index)

    variant_dir = out_dir / variant
    if variant_dir.exists():
        shutil.rmtree(variant_dir)
    variant_dir.mkdir(parents=True)

    (variant_dir / "mwgg_igdb.py").write_text(rendered, encoding="utf-8")
    write_pyproject(variant_dir, version, VARIANT_DESCRIPTIONS[variant])
    write_readme(variant_dir, variant, version)

    return {
        "variant": variant,
        "games_count": len(filtered),
        "search_terms": len(search_index),
        "out_dir": str(variant_dir),
    }


def build_all(
    *,
    worlds_dir: Path,
    enrichment_path: Path,
    template_path: Path,
    out_dir: Path,
    version: str,
    variants: Optional[Iterable[str]] = None,
) -> list[dict]:
    manifests = load_world_manifests(worlds_dir)
    enrichment = load_enrichment(enrichment_path)
    games_data = assemble_games_data(manifests, enrichment)

    selected = list(variants) if variants else list(VARIANT_FILTERS.keys())
    out: list[dict] = []
    for variant in selected:
        if variant not in VARIANT_FILTERS:
            raise SystemExit(f"unknown variant: {variant}")
        result = build_one_variant(
            variant,
            games_data=games_data,
            template_path=template_path,
            out_dir=out_dir,
            version=version,
        )
        out.append(result)
    return out


def _today_version() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y.%m.%d")


def _cli(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build all four variant orphan-branch packages from worlds/*.json.")
    parser.add_argument("--worlds-dir", type=Path, default=Path("worlds"))
    parser.add_argument("--enrichment", type=Path, default=Path("output/igdb_enrichment.json"))
    parser.add_argument("--template", type=Path, default=Path("scripts/game_index_template.py"))
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    parser.add_argument("--version", default=None, help="Package version, default: today's UTC date YYYY.MM.DD")
    parser.add_argument("--variant", default=None, help="Build only this variant (default: all four)")
    args = parser.parse_args(argv)

    version = args.version or _today_version()
    variants = [args.variant] if args.variant else None
    results = build_all(
        worlds_dir=args.worlds_dir,
        enrichment_path=args.enrichment,
        template_path=args.template,
        out_dir=args.out_dir,
        version=version,
        variants=variants,
    )
    for r in results:
        print(f"{r['variant']:>8s}: {r['games_count']:4d} games, {r['search_terms']:5d} search terms -> {r['out_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
