"""IGDB PR-time lookup for a single changed worlds/<slug>.json.

Decides one of these actions and emits a JSON plan:

- ``restore``       — base ref had igdb_id, head doesn't. Restore from base.
                      No IGDB call. Caller commits the corrected manifest.
- ``noop``          — head already has a non-zero igdb_id. Nothing to do.
- ``auto_resolve``  — head missing igdb_id; IGDB search returned exactly one
                      case-insensitive name match. Caller commits with the
                      resolved id added to the manifest.
- ``needs_input``   — head missing igdb_id; IGDB returned multiple candidates
                      or no exact name match. Caller comments + applies the
                      "Needs IGDB id" label.
- ``no_match``      — head missing igdb_id; IGDB returned zero results. Same
                      handling as needs_input on the workflow side.
- ``no_game_field`` — head manifest has no usable ``game`` field. Caller
                      surfaces the error in the comment but does not label
                      (Karen's schema check already catches this).
- ``skipped_no_creds`` — IGDB credentials missing in env. Caller no-ops; this
                         shouldn't happen in CI but is harmless if it does.

When the action mutates the head manifest (restore, auto_resolve), the head
file is rewritten in place using ``json.dumps(..., indent=4)`` plus a trailing
newline — matching the existing manifest formatting on this repo.

Per ``project_manifest_merge_semantics`` memory: igdb_id is one of the two
Index-controlled fields. Writing it from this script is appropriate; do NOT
extend this script to write any author-controlled fields (game, authors,
tracker, etc.).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
USER_AGENT = "MultiworldGG-Index-IGDB-PR-Lookup/1.0"
MAX_CANDIDATES = 5


def _http_post(url: str, data: bytes, headers: dict) -> Any:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def get_access_token(client_id: str, client_secret: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode("utf-8")
    payload = _http_post(
        IGDB_TOKEN_URL,
        params,
        {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    if not isinstance(payload, dict):
        raise SystemExit(f"IGDB OAuth response was not an object: {payload!r}")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise SystemExit(f"IGDB OAuth response missing access_token: {payload}")
    return token


def igdb_search(client_id: str, token: str, name: str, limit: int) -> list[dict]:
    """APICalypse search on /v4/games. Returns list of {id, name, first_release_date}."""
    # Escape embedded double-quotes in the game name for the APICalypse search
    # clause; APICalypse strings are double-quote delimited.
    safe_name = name.replace('"', '\\"')
    body = (
        f'fields id, name, first_release_date; '
        f'search "{safe_name}"; '
        f'limit {int(limit)};'
    )
    req = urllib.request.Request(
        IGDB_GAMES_URL,
        data=body.encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
        if not text:
            return []
        result = json.loads(text)
        return result if isinstance(result, list) else []


def has_igdb_id(manifest: dict) -> bool:
    val = manifest.get("igdb_id")
    return isinstance(val, int) and val != 0


def write_manifest_in_place(path: str, manifest: dict) -> None:
    """Rewrite the manifest with 4-space indent + trailing newline (repo style)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)
        f.write("\n")


def write_plan(path: str, action: str, slug: str, **extra: Any) -> None:
    plan = {"action": action, "slug": slug, **extra}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)


def _year_from_unix(ts: Any) -> int | None:
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).year
    except (OSError, OverflowError, ValueError):
        return None


def _load_manifest(path: str) -> dict:
    """Load a manifest from disk; return {} on empty/missing/non-object."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        parsed = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--base", required=True, help="path to the base-ref manifest")
    ap.add_argument("--head", required=True, help="path to the head-ref manifest (modified in place on restore/auto_resolve)")
    ap.add_argument("--output", required=True, help="path to write the action plan JSON")
    args = ap.parse_args()

    base_manifest = _load_manifest(args.base)
    head_manifest = _load_manifest(args.head)

    if not head_manifest:
        # Should never happen on a real PR (the diff named this file), but guard
        # for the test/local case anyway.
        write_plan(args.output, "no_game_field", args.slug,
                   reason="head manifest is empty or unreadable")
        return 0

    # 1. Removal protection — runs before any IGDB call.
    if has_igdb_id(base_manifest) and not has_igdb_id(head_manifest):
        head_manifest["igdb_id"] = base_manifest["igdb_id"]
        write_manifest_in_place(args.head, head_manifest)
        write_plan(
            args.output, "restore", args.slug,
            igdb_id=base_manifest["igdb_id"],
            reason="igdb_id was present on base but absent on head; restored.",
        )
        return 0

    # 2. Already resolved.
    if has_igdb_id(head_manifest):
        write_plan(
            args.output, "noop", args.slug,
            igdb_id=head_manifest["igdb_id"],
            reason=f"head manifest already has igdb_id={head_manifest['igdb_id']}",
        )
        return 0

    # 3. Need a lookup. Validate inputs first.
    game = head_manifest.get("game")
    if not isinstance(game, str) or not game.strip():
        write_plan(
            args.output, "no_game_field", args.slug,
            reason="head manifest has no `game` field; cannot search IGDB.",
        )
        return 0

    client_id = os.environ.get("IGDB_CLIENT_ID")
    client_secret = os.environ.get("IGDB_CLIENT_SECRET")
    if not client_id or not client_secret:
        write_plan(
            args.output, "skipped_no_creds", args.slug,
            reason="IGDB_CLIENT_ID/IGDB_CLIENT_SECRET not set in env; skipped.",
        )
        return 0

    try:
        token = get_access_token(client_id, client_secret)
        candidates = igdb_search(client_id, token, game, limit=MAX_CANDIDATES * 2)
    except urllib.error.HTTPError as err:
        write_plan(
            args.output, "skipped_no_creds", args.slug,
            reason=f"IGDB HTTP error: {err.code} {err.reason}",
        )
        return 0
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as err:
        write_plan(
            args.output, "skipped_no_creds", args.slug,
            reason=f"IGDB request failed: {err.__class__.__name__}: {err}",
        )
        return 0

    if not candidates:
        write_plan(
            args.output, "no_match", args.slug, game=game,
            reason=f"IGDB returned zero matches for game={game!r}.",
        )
        return 0

    target = game.strip().casefold()
    exact = [
        c for c in candidates
        if isinstance(c.get("name"), str)
        and c["name"].strip().casefold() == target
    ]

    if len(exact) == 1:
        igdb_id = int(exact[0]["id"])
        head_manifest["igdb_id"] = igdb_id
        write_manifest_in_place(args.head, head_manifest)
        write_plan(
            args.output, "auto_resolve", args.slug,
            igdb_id=igdb_id,
            matched_name=exact[0].get("name", ""),
            reason=f"single exact name match: {exact[0].get('name', '')}",
        )
        return 0

    candidates_short = [
        {
            "id": c.get("id"),
            "name": c.get("name", ""),
            "year": _year_from_unix(c.get("first_release_date")),
        }
        for c in candidates[:MAX_CANDIDATES]
    ]
    write_plan(
        args.output, "needs_input", args.slug, game=game,
        candidates=candidates_short,
        exact_match_count=len(exact),
        reason=("multiple exact name matches"
                if len(exact) > 1
                else "no exact name match among candidates"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
