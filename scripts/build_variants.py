"""Variant build for the MultiworldGG game-index orphan branches.

Reads the per-world manifests (`worlds/*.json`) and the staged IGDB metadata
(`output/igdb_game_details.json`), merges them per-apworld, applies the four age
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
                                     [--game_details output/igdb_game_details.json] \\
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
import pprint
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

# All known age-rating tokens. Keep in lockstep with how upstream source data
# tags games; the daily-release workflow flags apworlds with unrecognized values.
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

# IGDB-derived fields that come from the game details file. Anything not listed
# here that the consumer expects to be on a GAMES_DATA entry must be sourced
# from worlds/<apworld>.json instead.
GAME_DETAILS_FIELDS = (
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

# Defaults used when a apworld is in worlds/ but missing from game details.
GAME_DETAILS_DEFAULTS = {
    "cover_url": "",
    "artwork_url": "",
    "key_art_url": "",
    "igdb_name": "",
    "age_rating": "NR", # TODO: add 'MW' for original / hint worlds
    "rating": [],
    "player_perspectives": [],
    "genres": [],
    "themes": [],
    "platforms": [],
    "storyline": "",
    "keywords": [],
    "release_date": "",
}

# Fields indexed for substring search. Mirrors the monorepo behavior.
SEARCHABLE_FIELDS = {"igdb_name", "platforms", "genres", "themes", "keywords", "player_perspectives"}

# APWorlds always exposed as "popular".
POPULAR_APWORLDS = {"alttp", "sc2", "oot", "kh2", "hk", "sm64ex"}


def load_world_manifests(worlds_dir: Path) -> dict[str, dict]:
    """Read every `worlds/*.json` and return a `apworld -> manifest` dict.
    APWorld = filename stem; the JSON content is the manifest."""
    out: dict[str, dict] = {}
    for path in sorted(worlds_dir.glob("*.json")):
        apworld = path.stem
        with open(path, encoding="utf-8") as f:
            out[apworld] = json.load(f)
    return out


def load_game_details(path: Path) -> dict[str, dict]:
    """Read the staged IGDB game details file. Returns `apworld -> metadata`."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assemble_games_data(
    manifests: dict[str, dict],
    game_details: dict[str, dict],
) -> dict[str, dict]:
    """Merge per-world manifests with IGDB game details into the GAMES_DATA shape.

    Each output entry contains: igdb_id, cover_url, artwork_url, key_art_url,
    game_name, igdb_name, age_rating, rating, player_perspectives, genres,
    themes, platforms, storyline, keywords, release_date, module_location.
    """
    out: dict[str, dict] = {}
    for apworld, manifest in manifests.items():
        e = game_details.get(apworld, {})
        entry: dict = {}
        # IGDB id: prefer manifest (authoritative), fall back to game details.
        igdb_id = manifest.get("igdb_id", e.get("igdb_id", ""))
        # Coerce to string for backward compat with existing GAMES_DATA shape.
        entry["igdb_id"] = str(igdb_id) if igdb_id not in (None, "") else ""
        # IGDB-derived fields.
        for field in GAME_DETAILS_FIELDS:
            entry[field] = e.get(field, GAME_DETAILS_DEFAULTS[field])
        # Display name: from the per-world manifest's `game` field.
        entry["game_name"] = manifest.get("game", e.get("game_name", apworld))
        # Source location for the world (new field, not in legacy GAMES_DATA).
        if "module_location" in manifest:
            entry["module_location"] = manifest["module_location"]
        out[apworld] = entry
    return out


def filter_for_variant(games_data: dict[str, dict], variant: str) -> dict[str, dict]:
    """Return games_data restricted to the variant's allowed age ratings."""
    allowed = VARIANT_FILTERS[variant]
    return {
        apworld: data
        for apworld, data in games_data.items()
        if data.get("age_rating", "NR") in allowed
    }


def _add_to_index(index: dict[str, set[str]], term: str, apworld: str) -> None:
    term = "" if term is None else str(term).lower()
    if not term:
        return
    index.setdefault(term, set()).add(apworld)


def build_search_index(games_data: dict[str, dict]) -> dict[str, set[str]]:
    """Generate the search index: term -> set of apworlds.
    Mirrors the monorepo logic, with a `popular` curated set seeded first."""
    index: dict[str, set[str]] = {"popular": {s for s in POPULAR_APWORLDS if s in games_data}}
    skip_pattern = re.compile(r".*[():].*")
    for apworld, data in games_data.items():
        _add_to_index(index, data.get("game_name", ""), apworld)
        for field, value in data.items():
            if field not in SEARCHABLE_FIELDS:
                continue
            if isinstance(value, list):
                for item in set(value or ()):
                    if not item or skip_pattern.match(str(item)):
                        continue
                    cleaned = str(item).lower()
                    _add_to_index(index, cleaned, apworld)
                    for word in cleaned.split():
                        _add_to_index(index, word, apworld)
            elif isinstance(value, (str, int, float, bool)):
                if value:
                    cleaned = str(value).lower()
                    _add_to_index(index, cleaned, apworld)
                    for word in cleaned.split():
                        _add_to_index(index, word, apworld)
    return index


def build_game_names(games_data: dict[str, dict]) -> dict[str, str]:
    """Return `display_name -> apworld` for every game."""
    return {data["game_name"]: apworld for apworld, data in games_data.items() if data.get("game_name")}


def _python_literal(value: object) -> str:
    return pprint.pformat(value, indent=4, width=120, sort_dicts=False)


def _python_set_literal(values: Iterable[str]) -> str:
    items = sorted(values)
    if not items:
        return "set()"
    return "{" + ", ".join(_python_literal(item) for item in items) + "}"


def _format_search_index(search_index: dict[str, set[str]]) -> str:
    lines = ["{"]
    for term, apworlds in search_index.items():
        lines.append(f"    {_python_literal(term)}: {_python_set_literal(apworlds)},")
    lines.append("}")
    return "\n".join(lines)


def render_template(
    template_path: Path,
    games_data: dict[str, dict],
    game_names: dict[str, str],
    search_index: dict[str, set[str]],
) -> str:
    template = template_path.read_text(encoding="utf-8")
    games_data_str = _python_literal(games_data)
    game_names_str = _python_literal(game_names)
    search_index_str = _format_search_index(search_index)
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
pip install git+ssh://git@github.com/MultiworldGG/MultiworldGG-Index@game_index_{variant}
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
    game_details_path: Path,
    template_path: Path,
    out_dir: Path,
    version: str,
    variants: Optional[Iterable[str]] = None,
) -> list[dict]:
    manifests = load_world_manifests(worlds_dir)
    game_details = load_game_details(game_details_path)
    games_data = assemble_games_data(manifests, game_details)

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
    parser.add_argument("--game-details", type=Path, default=Path("output/igdb_game_details.json"))
    parser.add_argument("--template", type=Path, default=Path("scripts/game_index_template.py"))
    parser.add_argument("--out-dir", type=Path, default=Path("dist"))
    parser.add_argument("--version", default=None, help="Package version, default: today's UTC date YYYY.MM.DD")
    parser.add_argument("--variant", default=None, help="Build only this variant (default: all four)")
    args = parser.parse_args(argv)

    version = args.version or _today_version()
    variants = [args.variant] if args.variant else None
    results = build_all(
        worlds_dir=args.worlds_dir,
        game_details_path=args.game_details,
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
