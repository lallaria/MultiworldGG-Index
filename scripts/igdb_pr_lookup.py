"""IGDB PR-time lookup for a single apworld.

Runs only when Oliver has applied the "Needs IGDB id" label (the workflow
trigger gates this). Reads the head manifest, searches IGDB for the `game`
field, and writes a markdown chunk listing candidates for the codeowner to
pick from. Never writes the manifest, never removes the label.

Per project_manifest_merge_semantics: igdb_id is one of two Index-controlled
fields, but selecting it is the codeowner's call. The author can override by
including their own igdb_id in archipelago.json — in that case Oliver doesn't
apply the label and this script doesn't run.
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
IGDB_SEARCH_URL = "https://api.igdb.com/v4/search"
USER_AGENT = "MultiworldGG-Index-IGDB-PR-Lookup/2.0"
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
    """APICalypse search on /v4/search.

    The search endpoint searches across multiple IGDB record types
    (games, characters, themes, etc.). We filter to game-type rows by
    requiring `game != null`, then read the search-row's `game` field
    (which is the canonical IGDB game id we want for `igdb_id`).
    """
    safe_name = name.replace('"', '\\"')
    body = (
        f'fields game, name, description, published_at; '
        f'search "{safe_name}"; '
        f'where game != null; '
        f'limit {int(limit)};'
    )
    req = urllib.request.Request(
        IGDB_SEARCH_URL,
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


def _year_from_unix(ts: Any) -> int | None:
    if not isinstance(ts, (int, float)):
        return None
    try:
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).year
    except (OSError, OverflowError, ValueError):
        return None


def _load_manifest(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return {}
        parsed = json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_candidate(c: dict) -> str | None:
    game_id = c.get("game")
    if not isinstance(game_id, int):
        return None
    name = c.get("name", "") or ""
    year = _year_from_unix(c.get("published_at"))
    desc = c.get("description")
    if isinstance(desc, str) and len(desc) > 200:
        desc = desc[:197].rstrip() + "…"
    line = f"  - **{name}** — id `{game_id}`"
    if year:
        line += f", {year}"
    if isinstance(desc, str) and desc:
        line += f"  \n    _{desc}_"
    return line


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apworld", required=True)
    ap.add_argument("--head", required=True, help="path to the head-ref manifest")
    ap.add_argument("--output", required=True, help="path to write the markdown chunk")
    args = ap.parse_args()

    head_manifest = _load_manifest(args.head)

    if not head_manifest:
        _write(args.output,
               f"- ⚠️ **{args.apworld}**: head manifest is empty or unreadable.\n")
        return 0

    game = head_manifest.get("game")
    if not isinstance(game, str) or not game.strip():
        _write(args.output,
               f"- ⚠️ **{args.apworld}**: manifest has no `game` field — fix that first, then re-add the label.\n")
        return 0

    client_id = os.environ.get("IGDB_CLIENT_ID")
    client_secret = os.environ.get("IGDB_CLIENT_SECRET")
    if not client_id or not client_secret:
        # Hard error: the workflow is gated on the igdb_gather environment, so
        # missing creds means the environment is misconfigured.
        raise SystemExit("IGDB_CLIENT_ID/IGDB_CLIENT_SECRET not set in env.")

    try:
        token = get_access_token(client_id, client_secret)
        candidates = igdb_search(client_id, token, game, limit=MAX_CANDIDATES * 2)
    except urllib.error.HTTPError as err:
        _write(args.output,
               f"- ⚠️ **{args.apworld}**: IGDB request failed ({err.code} {err.reason}). Retry by removing and re-applying the label.\n")
        return 0
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as err:
        _write(args.output,
               f"- ⚠️ **{args.apworld}**: IGDB request failed ({err.__class__.__name__}: {err}). Retry by removing and re-applying the label.\n")
        return 0

    if not candidates:
        _write(args.output,
               f"- ❓ **{args.apworld}**: no IGDB matches for `{game}`. Search [IGDB](https://www.igdb.com/search) manually.\n")
        return 0

    lines = [f"- ❓ **{args.apworld}** (`{game}`):"]
    shown = 0
    for c in candidates:
        if shown >= MAX_CANDIDATES:
            break
        formatted = _format_candidate(c)
        if formatted is None:
            continue
        lines.append(formatted)
        shown += 1

    if shown == 0:
        _write(args.output,
               f"- ❓ **{args.apworld}**: IGDB returned results for `{game}` but none had a usable game id. Search [IGDB](https://www.igdb.com/search) manually.\n")
        return 0

    _write(args.output, "\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
