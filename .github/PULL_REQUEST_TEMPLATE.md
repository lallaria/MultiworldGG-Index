<!--
For per-world manifest updates from a per-world repo's publish-to-index Action,
use the dedicated template:
?template=manifest_update.md

For human-written PRs (schema, scripts, workflows, infra), fill in below.
-->

## Summary

<!-- 1-3 bullet points on what this PR changes. -->

## Why

<!-- The motivation. Link the upstream issue, ticket, or discussion if any. -->

## Affected areas

<!-- Tick what applies. -->

- [ ] `worlds/*.json` (one or more manifests — Greg validates)
- [ ] `schema/world_manifest.schema.json` (changing the schema re-validates ALL manifests)
- [ ] `scripts/` (build / enrichment / Greg pipeline)
- [ ] `.github/workflows/` (CI)
- [ ] `output/igdb_enrichment.json` (manual edit — usually done by `igdb-enrich.yml`)
- [ ] Other / docs

## Test plan

<!-- Bulleted checklist. For schema or script changes, run the relevant validator
locally and paste the output. -->

- [ ]
- [ ]
