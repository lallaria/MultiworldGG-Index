# MultiworldGG-Index

Canonical game index for [MultiworldGG](https://github.com/MultiworldGG/MultiworldGG).

This repo is the source of truth for *which worlds exist*, *who wrote them*,
*where to fetch them from*, and *what age rating they carry*. The
MultiworldGG launcher, generator, web host, and tracker all read it
indirectly via the `mwgg_igdb` package, which is built from this repo.

## Repo layout

| Branch | Purpose |
|---|---|
| `main` | Per-world manifests live at `worlds/<apworld>.json`. Schema + workflows + scripts live here too. PRs land here. |
| `game_index_nr` | Orphan release branch — No Rating variant. |
| `game_index_ao` | Orphan release branch — Adults Only variant. |
| `game_index_twelve` | Orphan release branch — 12+ variant. |
| `game_index_sixteen` | Orphan release branch — 16+ variant (canonical default). |

The four orphan branches are rebuilt from `main` by
`.github/workflows/daily-release.yml`. They contain no shared history with
`main` and are force-pushed on each release. Each orphan branch is tagged
`<variant>-YYYY.MM.DD` per release.

## Consuming the index (downstream)

Install the variant matching the audience your installation is intended for:

```bash
pip install git+https://github.com/lallaria/MultiworldGG-Index.git@game_index_sixteen
```

Substitute `game_index_nr`, `game_index_ao`, `game_index_twelve`, or
`game_index_sixteen` for the desired variant. The MultiworldGG monorepo's
build wires the variant choice through Inno Setup (`WorldList` → one of
`mwgg_igdb` / `mwgg_igdb_twelve` / `mwgg_igdb_sixteen`) and through the
launcher's content-rating setting at runtime.

**Variant choice is a parental gate, not a build flag.** The MultiworldGG
launcher exposes the rating selection in settings; do not surface adult
game names or metadata to under-18 users.

## Per-world manifest schema

Source of truth: [`schema/world_manifest.schema.json`](schema/world_manifest.schema.json)
(JSON Schema Draft 2020-12, strict `additionalProperties: false`).

Required: `game`, `module_location`.
Optional: `world_version`, `world_version_full`, `minimum_ap_version`,
`authors`, `contributors`, `igdb_id`, `compatible_version`, `version`,
`build_version`, `repo_url`, `tracker`, `flags`, `changelog`.

Inline-comment fields matching `^_comment(_|$)` are ignored.

IGDB-derived metadata never lives on `main`. It is attached only at
release time, on the four orphan branches.

## Contributing a manifest update

Manifest updates do **not** start as hand-written PRs against this repo.
They are auto-opened by each per-world repo's
`.github/workflows/publish-to-index.yml` Action when a release is cut
upstream.

Flow:

1. Per-world repo cuts a release. Its `publish-to-index` Action opens a PR
   here that updates exactly one `worlds/<apworld>.json`.
2. `karen-pr-review.yml` runs Karen's 7-check security suite (manifest
   schema, lockfile sanity, sandboxed clone, bandit, pip-audit, size,
   AST surface). She posts a sticky review comment with the result.
3. On all-green, Karen requests review from a human CODEOWNER. The
   `KAREN_HUMAN_REVIEWERS` org-secret list governs who that is.
4. CODEOWNER approves and merges.
5. `igdb-game-details.yml` keys off the merged `igdb_id`, fetches current IGDB
   metadata, and commits the result back to `main` under
   `output/igdb_enrichment.json` with `[skip ci]`.
6. The next release run (`daily-release.yml`, currently
   `workflow_dispatch`-only) rebuilds the four orphan branches and tags.

Hand-written PRs (schema changes, script changes, workflow changes) use
`.github/PULL_REQUEST_TEMPLATE.md`. Manifest-update PRs from per-world
repos use `.github/PULL_REQUEST_TEMPLATE/manifest_update.md`.

## Repo contents

- `worlds/` — one JSON manifest per world.
- `schema/` — JSON Schema for `worlds/<apworld>.json`.
- `scripts/` — manifest validation, IGDB lookup, Karen's review logic,
  variant builder, backfill.
- `output/` — IGDB enrichment cache (`igdb_enrichment.json`).
- `.github/workflows/` — Karen review, IGDB game details, daily release,
  IGDB PR-time tag flow.
- `dist/` — variant-build output (gitignored; produced by
  `daily-release.yml`).

## License

GPL-3.0. See [LICENSE](LICENSE).
