#!/usr/bin/env python3
"""
Campaign registry backfill builder -- PLAN.md Phase 0 / Step 2 (attribution
spine).

Pulls the live campaign list from Google Ads and/or LinkedIn Ads, classifies
each campaign with the INTERIM substring logic (same rules
collect_kw_perf.py / the anomaly-engine's build_dashboard.py already use --
see module docstring notes below), and:

  1. Writes one `campaign_registry` row per campaign via the guarded writer
     (bq_writer.write_rows -- DRY_RUN default, per config.py; never live
     unless DRY_RUN=false is set by the caller).
  2. Emits docs/campaign-registry-backfill.md -- a human-review markdown
     table for the perf team to confirm/correct (PLAN.md Step 2 "Backfill
     ... for all live Google + LinkedIn campaigns").

Existing campaigns are NOT renamed (docs/campaign-naming-convention.md
Section 4 migration policy) -- every row's `campaign_name` is byte-identical
to the live platform name; `utm_campaign` is set to the same string
(Section 2.3: "utm_campaign = the structured campaign name above, verbatim").

------------------------------------------------------------------------
Google auth: reused verbatim from collect_kw_perf.py -- same
`IK_MCP_DIR`/`C:\\mcp-servers`/`/mnt/c/mcp-servers` resolution
(`collect_kw_perf._resolve_mcp_base()`), same `google-ads/.env` keys
(`collect_kw_perf._client_and_customer_id()`), same interim segment
classifier (`collect_kw_perf.classify_google_campaign()`) -- all imported,
not copied, so a change to that resolution logic only needs to happen once.

LinkedIn auth: LinkedIn listing turned out NOT too involved to reuse -- one
REST call (`GET /rest/adAccounts/{id}/adCampaigns?q=search`) plus the same
Bearer-token + refresh-on-401 pattern documented in
/mnt/c/mcp-servers/linkedin-ads/server.py's `_fetch_campaigns()` /
`_raw_get()` / `_refresh_access_token()`. That module is an MCP stdio server
(importing it needs the `mcp` package and would wire up server state at
import time), so this file clones just the read-only campaign-list call
rather than importing server.py -- same "clone, cite the source" pattern
collect_kw_perf.py itself uses for collect_drilldown.py. Credentials: same
`.env` shape, resolved via the same `IK_MCP_DIR` mcp-servers base directory,
under `linkedin-ads/.env` (LINKEDIN_ACCESS_TOKEN / LINKEDIN_REFRESH_TOKEN /
LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET / LINKEDIN_AD_ACCOUNT_ID).

The ONLY non-GET call this whole module ever makes is the OAuth
token-refresh POST (`_linkedin_refresh_access_token`, to
`www.linkedin.com/oauth/v2/accessToken`) -- it exchanges the refresh token
for a new bearer access token and nothing else; it never touches ad account
state (no campaign, budget, or targeting create/update/delete is possible
from this file). Every other call this module makes, on both platforms, is
a read-only GET.

Pagination correction (2026-07-03, post code-review): the original clone
assumed LinkedIn paginates via `paging.links` (`rel=next`), mirroring the
GET-collection convention used elsewhere. A live inspection of this
account's actual `/rest/adAccounts/{id}/adCampaigns?q=search` response
showed `"paging": null` on the first page and cursor-style pagination via
`metadata.nextPageToken` instead -- so the old `has_next` check was always
False after page 1 and silently truncated the real account (175 campaigns)
down to whatever the first page happened to return (100). Cursor pagination
via `metadata.nextPageToken` is now the PRIMARY mechanism; the legacy
`paging.links` `rel=next` check is kept as a secondary/fallback signal since
a later page in that same live crawl returned a non-null (but link-less)
`paging` object -- the API's pagination-metadata shape was inconsistent
page-to-page in practice, so `fetch_linkedin_campaigns()` honors both
signals defensively and loudly warns if a full page comes back with
neither.

Program detection (`_detect_program`) is cloned verbatim (lowercased to
match the naming-convention doc's lowercase vocabulary) from
/mnt/c/mcp-servers/anomaly-engine/build_dashboard.py's `detect_program()`.

Audience token detection (`_detect_audience`) is a SEED controlled
vocabulary only -- docs/campaign-naming-convention.md Section 5 ANSWERS #1
explicitly defers the full list to "during backfill"; this starter list
(inmarket / technologyindustry / domainsales / conversation / examtakers /
jobfunction / lookalike / retarget / remarketing) is drawn from token
fragments visible in the naming-convention doc's own example names and
docs/campaign-registry-backfill.md's "notes" column, and is meant to be
extended by the perf team during the human review this script produces --
NOT treated as final. An unmatched name gets `funnel_stage=None`, which by
itself does not set `needs_attention` (only an unparseable segment or an
`other` program does -- see `classify_campaign()`).

Weekly carry-forward refresh (`--refresh`, added 2026-10-27): the original
backfill was a one-shot (one snapshot, 2026-07-04, never re-run), so the
registry -- and the dashboard's /registry tile, and generate_assets'
active-campaign targeting -- froze in July while the accounts drifted (16
new + 3 renamed Google ids, 45 new LinkedIn ids by October). Refresh mode
re-lists the live campaigns and MERGES them onto the prior registry
(`load_prior_rows()` + `merge_refresh()`), keyed on (platform,
platform_campaign_id) -- never on campaign_name, which has 3 real LinkedIn
duplicates:
  - new id            -> auto-classified (same rules as the backfill)
  - same id, same name -> classification copied verbatim (manual fixes
                         survive); only status/snapshot_date update
  - same id, renamed   -> name + utm_campaign follow the live name; an
                         untouched (still auto-equal) classification is
                         re-derived from the new name, a hand-edited one is
                         kept as a manual override
  - prior id absent from a FETCHED platform -> carried forward as archived
  - prior id on a platform whose fetch failed/was skipped -> carried
    forward unchanged (per-platform fail-soft)
Every refresh snapshot is therefore COMPLETE (a superset of the prior one),
which is what lets the dashboard and registry.py read only
MAX(snapshot_date). main() refuses to write (non-zero exit, no partial
snapshot) if the prior registry can't be read live, if no platform could be
fetched, or if the merged snapshot is somehow smaller than the prior one.
A refresh that DID write but had to carry a REQUESTED platform forward (its
fetch failed, or its listing tripped the partial-listing guard) exits 4 =
degraded: run_daily.sh then records a soft failure and the Slack digest
flags it, because the advanced snapshot_date would otherwise hide a frozen
platform from every freshness check. When a partial-listing trip is really
a mass removal, accept it once with --accept-partial <platform> (the missing
rows are then archived and the guard stops re-firing on them).
The markdown report goes to generation/staging/ in this mode -- the tracked
docs/campaign-registry-backfill.md is only overwritten by an explicit
--out-md.

Backfill mode (no --refresh) is the original one-shot for an EMPTY registry:
no carry-forward, every classification re-derived from the names, only the
requested platforms. Because registry.py and the dashboard read only
MAX(snapshot_date), a live (DRY_RUN=false) backfill over an existing
registry would replace the latest snapshot with that partial one, so main()
refuses it -- exit 3 if campaign_registry already has rows, exit 2 if that
can't be confirmed -- unless --force-backfill is passed. Use --refresh.

Usage:
    python3 build_campaign_registry.py --from-google    # backfill: DRY_RUN, or
    python3 build_campaign_registry.py --from-linkedin  # live only into an
    python3 build_campaign_registry.py --from-google --from-linkedin  # EMPTY table
    python3 build_campaign_registry.py --from-fixture fixtures/registry_campaigns_raw.json
                                                     # skip both APIs entirely;
                                                     # load raw campaign dicts
                                                     # from a JSON fixture
    python3 build_campaign_registry.py --refresh --from-google --from-linkedin
                                                     # weekly carry-forward
                                                     # refresh (run_daily.sh)
    python3 build_campaign_registry.py --refresh --from-google --only-if-stale-days 7
                                                     # no-op unless the latest
                                                     # snapshot is >= 7 days old
    python3 build_campaign_registry.py --refresh --from-google --from-linkedin \\
        --accept-partial linkedin                    # archive a real mass removal
                                                     # the partial guard flagged

DRY_RUN (default true, see config.py) always applies on the BQ write side,
regardless of --from-fixture -- set DRY_RUN=false for a live BigQuery load.
"""
import argparse
import http.client
import json
import logging
import math
import os
import re
import sys
import urllib.parse
from datetime import UTC, date, datetime
from pathlib import Path

import bq_writer
import collect_kw_perf
import config
import schemas

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("build_campaign_registry")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MD_PATH = REPO_ROOT / "docs" / "campaign-registry-backfill.md"

# LinkedIn-Version header (YYYYMM). Was a hardcoded "202605" (matching
# /mnt/c/mcp-servers/linkedin-ads/server.py's API_VERSION). Bumped to "202610"
# on 2026-10-27, the day the anomaly engine's hardcoded 202503 was found
# failing with HTTP 426 ("version not active") -- which silently stopped
# pmis_daily_segment_facts and zeroed LinkedIn spend on the dashboard. Live
# probe that day: every version <= 202512 returns 426, 202601..202610 return
# 200. LinkedIn sunsets each monthly version about a year after release, so
# any pinned value WILL go stale again; two defenses instead of a silent
# outage: (a) the LINKEDIN_API_VERSION env var overrides this default
# without a code change, and (b) a 426 triggers the bounded auto-fallback in
# _linkedin_negotiate_version(), which logs "bump LINKEDIN_API_VERSION".
_LINKEDIN_API_VERSION_DEFAULT = "202610"
_LINKEDIN_API_VERSION_RE = re.compile(r"^20\d{4}$")
# 426 fallback window: the current UTC month plus this many months back
# (12 -> at most 13 probes). Bounded on purpose -- a run must never spin
# through an open-ended version search.
_LINKEDIN_VERSION_FALLBACK_MONTHS = 12


def _resolve_linkedin_api_version(raw: str | None) -> str:
    """LINKEDIN_API_VERSION env override, validated as YYYYMM (^20\\d{4}$).
    Unset/blank -> the default. A malformed value is ignored with a warning
    (falling back to the default) rather than sent as a header LinkedIn
    would reject anyway."""
    v = (raw or "").strip()
    if not v:
        return _LINKEDIN_API_VERSION_DEFAULT
    if not _LINKEDIN_API_VERSION_RE.match(v):
        log.warning("Ignoring malformed LINKEDIN_API_VERSION=%r (expected YYYYMM, "
                    "e.g. %s) -- using the default %s",
                    v, _LINKEDIN_API_VERSION_DEFAULT, _LINKEDIN_API_VERSION_DEFAULT)
        return _LINKEDIN_API_VERSION_DEFAULT
    return v


LINKEDIN_API_VERSION = _resolve_linkedin_api_version(os.environ.get("LINKEDIN_API_VERSION"))

# Safety cap on pagination loop iterations (pages, not rows) -- guards
# against an infinite loop from a bug in cursor/offset handling, not a real
# business limit (both accounts today are two orders of magnitude smaller
# than this). Hitting it is always worth a loud log.error, never a silent
# truncation.
_MAX_PAGINATION_ITERATIONS = 500


# ---------------------------------------------------------------------------
# Google Ads: live campaign list (reuses collect_kw_perf's auth resolution)
# ---------------------------------------------------------------------------
def fetch_google_campaigns() -> list[dict]:
    """GAQL against the `campaign` resource -- id, name, status, channel type.
    No date filter: this is a live entity listing, not a stats report, so
    every campaign (any status, including REMOVED) comes back.

    `ga.search()` is itself a paginating generator (the google-ads client
    library fetches successive result pages under the hood, transparently to
    this loop) -- the iteration cap below guards this loop the same way the
    explicit `while True` in fetch_linkedin_campaigns() is capped: a runaway
    generator (bad server-side cursor, client library bug) should hit a loud
    error, not silently iterate forever or hand back a truncated set without
    any signal."""
    client, customer_id = collect_kw_perf._client_and_customer_id()
    ga = client.get_service("GoogleAdsService")
    query = """
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type
        FROM campaign
    """
    rows = []
    for i, r in enumerate(ga.search(customer_id=customer_id, query=query)):
        if i >= _MAX_PAGINATION_ITERATIONS:
            log.error(
                "Google Ads campaign listing exceeded %d row(s) without "
                "terminating -- aborting to avoid an unbounded fetch. "
                "Collected %d campaign(s) so far; if this account genuinely "
                "has more than %d campaigns, raise _MAX_PAGINATION_ITERATIONS "
                "deliberately rather than silently truncating.",
                _MAX_PAGINATION_ITERATIONS, len(rows), _MAX_PAGINATION_ITERATIONS)
            break
        rows.append({
            "platform": "google",
            "campaign_id": str(r.campaign.id),
            "campaign_name": r.campaign.name,
            "status": r.campaign.status.name,               # ENABLED | PAUSED | REMOVED
            "channel_type": r.campaign.advertising_channel_type.name,
        })
    return rows


# ---------------------------------------------------------------------------
# LinkedIn Ads: live campaign list (minimal clone of server.py's read path --
# see module docstring for why this isn't a direct import)
# ---------------------------------------------------------------------------
def _linkedin_env_path() -> Path:
    return collect_kw_perf._resolve_mcp_base() / "linkedin-ads" / ".env"


def _linkedin_refresh_access_token(env: dict) -> str | None:
    """POST /oauth/v2/accessToken with the refresh token. Returns the new
    access token, or None if refresh isn't configured or fails -- mirrors
    server.py's _refresh_access_token() (in-memory only, .env untouched)."""
    refresh_token = env.get("LINKEDIN_REFRESH_TOKEN")
    client_id = env.get("LINKEDIN_CLIENT_ID")
    client_secret = env.get("LINKEDIN_CLIENT_SECRET")
    if not (refresh_token and client_id and client_secret):
        return None
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    })
    conn = http.client.HTTPSConnection("www.linkedin.com", timeout=30)
    try:
        conn.request("POST", "/oauth/v2/accessToken", body=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp_body = resp.read().decode()
        if resp.status != 200:
            log.error("LinkedIn token refresh failed (HTTP %d)", resp.status)
            return None
        data = json.loads(resp_body)
        return data.get("access_token")
    finally:
        conn.close()


def _linkedin_get(access_token: str, path: str,
                  version: str | None = None) -> tuple[int, dict]:
    """`version` defaults to the module-level LINKEDIN_API_VERSION (read at
    call time, not bound at def time); the 426 fallback passes each probe
    version explicitly."""
    conn = http.client.HTTPSConnection("api.linkedin.com", timeout=30)
    try:
        conn.request("GET", path, headers={
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": version or LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        })
        resp = conn.getresponse()
        body = resp.read().decode()
        parsed = json.loads(body) if body else {}
        return resp.status, parsed
    finally:
        conn.close()


def _utc_now() -> datetime:
    """Seam for tests (the 426 fallback's candidate window is relative to
    the current UTC month)."""
    return datetime.now(UTC)


def _linkedin_version_candidates(now: datetime | None = None) -> list[str]:
    """YYYYMM strings from the current UTC month back
    _LINKEDIN_VERSION_FALLBACK_MONTHS months, newest first (13 by default),
    crossing year boundaries correctly (2027-01 -> 202701, 202612, ...)."""
    now = now or _utc_now()
    y, m = now.year, now.month
    out = []
    for _ in range(_LINKEDIN_VERSION_FALLBACK_MONTHS + 1):
        out.append(f"{y:04d}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _linkedin_negotiate_version(get, rejected_version: str,
                                now: datetime | None = None) -> tuple[str, int, dict]:
    """Bounded auto-fallback after the configured LinkedIn-Version got a 426.

    `get(version) -> (status, data)` re-issues the SAME request with that
    version header (so the winning probe's response IS the page -- no extra
    call). Probes newest-first from the current UTC month back
    _LINKEDIN_VERSION_FALLBACK_MONTHS months (at most 13 attempts; the
    already-rejected version is skipped), returning the first 2xx as
    (version, status, data) with a loud "bump LINKEDIN_API_VERSION" warning.

    Stops early on 401/403: an auth failure (after `get`'s own one-shot
    token refresh) is not a version problem, and another 12 authenticated
    failures would only add noise. If nothing works, returns the last
    non-2xx response with `rejected_version`, and the caller fails the
    LinkedIn fetch as before (in --refresh mode that platform is then
    carried forward, per fail-soft)."""
    status, data = 426, {}
    attempts = 0
    for cand in _linkedin_version_candidates(now):
        if cand == rejected_version:
            continue
        attempts += 1
        status, data = get(cand)
        if 200 <= status < 300:
            log.warning(
                "LOUD WARNING: LinkedIn-Version %s was rejected (HTTP 426, version "
                "not active); auto-fell back to %s after %d probe(s). bump "
                "LINKEDIN_API_VERSION to %s (env var, or the default in "
                "build_campaign_registry.py) so runs stop paying for this probe.",
                rejected_version, cand, attempts, cand)
            return cand, status, data
        if status in (401, 403):
            log.error("LinkedIn-Version fallback stopped at %s: HTTP %d is an auth "
                      "failure, not a version problem.", cand, status)
            return rejected_version, status, data
    log.error("LinkedIn-Version fallback exhausted: %s got 426 and none of %d "
              "probed version(s) returned 2xx (last HTTP %d) -- failing the "
              "LinkedIn fetch.", rejected_version, attempts, status)
    return rejected_version, status, data


def fetch_linkedin_campaigns() -> list[dict]:
    """GET /rest/adAccounts/{id}/adCampaigns?q=search, paginated, with a
    single refresh-and-retry on 401 -- see module docstring for the source
    this clones (server.py's _fetch_campaigns/_raw_get/_refresh_access_token)
    and for the "Pagination correction" note explaining why cursor pagination
    (`metadata.nextPageToken`) is now primary, with the original
    `paging.links` `rel=next` check kept as a fallback signal.

    Truncation detection (post code-review -- the original clone's
    `paging.links`-only check silently truncated a real 175-campaign account
    down to 100 with zero signal): after collecting each page,
      - if the page returned exactly `page_size` elements but NEITHER a
        `metadata.nextPageToken` NOR a legacy `paging.links` `rel=next` is
        present, this is loudly logged as a possible silent pagination cap
        (a full page with no continuation signal is exactly what a
        server-side truncation looks like) before the loop stops -- there is
        no known way to ask for more.
      - if any page's `paging.total` is present, it is compared against the
        final collected count once the loop ends, with a loud warning on any
        mismatch.

    LinkedIn-Version (2026-10-27): every request sends LINKEDIN_API_VERSION;
    the first 426 ("version not active") hands the same request to
    _linkedin_negotiate_version()'s bounded fallback, and the version it
    finds is then used for every later page of this fetch. A second 426
    after that (or a fallback that finds nothing) raises like any other
    non-2xx.
    """
    env = collect_kw_perf.load_env(_linkedin_env_path())
    access_token = env["LINKEDIN_ACCESS_TOKEN"]
    ad_account_id = env["LINKEDIN_AD_ACCOUNT_ID"]

    refreshed_once = False

    def _get(path: str, version: str) -> tuple[int, dict]:
        """One GET plus the single refresh-and-retry on 401 -- the same
        semantics the inline loop had before the 426 fallback needed to
        share it: at most one refresh per fetch, the same request retried
        immediately, a refresh failure raises."""
        nonlocal access_token, refreshed_once
        status, data = _linkedin_get(access_token, path, version)
        if status == 401 and not refreshed_once:
            log.warning("LinkedIn API 401 -- attempting token refresh...")
            new_token = _linkedin_refresh_access_token(env)
            if not new_token:
                raise RuntimeError(
                    "LinkedIn API returned 401 and token refresh failed/unconfigured. "
                    "Run: cd /mnt/c/mcp-servers/linkedin-ads && python get_token.py")
            access_token = new_token
            refreshed_once = True
            status, data = _linkedin_get(access_token, path, version)
        return status, data

    api_version = LINKEDIN_API_VERSION
    version_negotiated = False

    rows = []
    page_size = 200
    page_token: str | None = None      # primary: cursor pagination
    page_start = 0                     # fallback: legacy offset pagination
    reported_total: int | None = None
    page_num = 0
    while True:
        page_num += 1
        if page_num > _MAX_PAGINATION_ITERATIONS:
            log.error(
                "LinkedIn campaign pagination exceeded %d page(s) without "
                "terminating -- aborting to avoid an infinite loop. Collected "
                "%d campaign(s) so far; this is almost certainly a cursor-"
                "handling bug rather than a real account this large. Do NOT "
                "trust this count -- investigate before using it.",
                _MAX_PAGINATION_ITERATIONS, len(rows))
            break

        if page_token:
            path = (f"/rest/adAccounts/{ad_account_id}/adCampaigns"
                    f"?q=search&count={page_size}"
                    f"&pageToken={urllib.parse.quote(page_token)}")
        else:
            path = (f"/rest/adAccounts/{ad_account_id}/adCampaigns"
                    f"?q=search&count={page_size}&start={page_start}")
        status, data = _get(path, api_version)
        if status == 426 and not version_negotiated:
            version_negotiated = True
            api_version, status, data = _linkedin_negotiate_version(
                lambda v, _path=path: _get(_path, v), api_version)
        if not 200 <= status < 300:
            raise RuntimeError(f"LinkedIn API error ({status}): {str(data)[:300]}")

        elements = data.get("elements", [])
        for c in elements:
            rows.append({
                "platform": "linkedin",
                "campaign_id": str(c.get("id")),
                "campaign_name": c.get("name") or "",
                "status": c.get("status") or "UNKNOWN",
                "channel_type": c.get("type") or "",
            })

        paging = data.get("paging") or {}
        if "total" in paging:
            reported_total = paging.get("total")
        has_legacy_next = any(link.get("rel") == "next" for link in paging.get("links", []))
        next_token = (data.get("metadata") or {}).get("nextPageToken")
        page_start += len(elements)   # kept current even in cursor mode, in
                                       # case a later page drops back to the
                                       # legacy offset scheme (observed live)

        if not elements:
            break

        if next_token:
            page_token = next_token
            continue

        if has_legacy_next:
            # Fallback path: no cursor token on this page, but the legacy
            # paging shape says there's more -- resume via the offset scheme
            # (page_start already advanced above).
            page_token = None
            continue

        if len(elements) == page_size:
            log.warning(
                "LOUD WARNING: LinkedIn campaign page %d returned exactly "
                "the requested page size (%d) with NO continuation signal "
                "(no metadata.nextPageToken, no paging.links rel=next). "
                "This is exactly what a silent pagination cap looks like -- "
                "%d campaign(s) collected so far may be a TRUNCATED count, "
                "not the true total. Cross-check against the LinkedIn "
                "Campaign Manager UI before trusting this backfill.",
                page_num, page_size, len(rows))
        break

    if reported_total is not None and reported_total != len(rows):
        log.warning(
            "LOUD WARNING: LinkedIn paging.total=%d does not match the %d "
            "campaign(s) actually collected -- possible truncation or "
            "duplicate/dropped rows during pagination. Cross-check against "
            "the LinkedIn Campaign Manager UI before trusting this count.",
            reported_total, len(rows))

    return rows


# ---------------------------------------------------------------------------
# Fixture loading (--from-fixture; no API/credentials needed)
# ---------------------------------------------------------------------------
def load_fixture(path) -> list[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = obj["rows"] if isinstance(obj, dict) and "rows" in obj else obj
    if not isinstance(rows, list):
        raise ValueError(f"Fixture {path} must contain a JSON list of raw campaign "
                          f"dicts (or an object with a 'rows' list)")
    return rows


# ---------------------------------------------------------------------------
# Status mapping -> schemas.CampaignRegistryRow's status Literal
# ---------------------------------------------------------------------------
_GOOGLE_STATUS_MAP = {"ENABLED": "active", "PAUSED": "paused", "REMOVED": "archived"}
_LINKEDIN_STATUS_MAP = {"ACTIVE": "active", "PAUSED": "paused"}


def _map_status(platform: str, raw_status: str) -> str:
    """Map a platform's raw status string to schemas.CampaignRegistryRow's
    status Literal.

    Fails CLOSED for an unrecognized `platform` (raises ValueError) rather
    than silently defaulting to "archived" -- an unmapped platform means
    this function has no status vocabulary for it at all, which is a
    programming/config gap (a new platform wired in without its status map),
    not a real "this campaign isn't serving" signal. Silently writing
    "archived" for that case would corrupt the registry with a status that
    looks deliberate but isn't.

    This is distinct from an unrecognized `raw_status` on a KNOWN platform
    (e.g. a Google status this map hasn't seen before) -- that case still
    defaults to "archived" on purpose (see the LinkedIn branch's comment):
    an unfamiliar status is far more likely to be a non-serving state than a
    config bug, so it fails open there, not closed.
    """
    raw = (raw_status or "").upper()
    if platform == "google":
        return _GOOGLE_STATUS_MAP.get(raw, "archived")
    if platform == "linkedin":
        # Anything besides ACTIVE/PAUSED (ARCHIVED, CANCELED, COMPLETED,
        # DRAFT, PENDING_DELETION) -> archived: none of those are a
        # currently-serving campaign.
        return _LINKEDIN_STATUS_MAP.get(raw, "archived")
    raise ValueError(
        f"_map_status: unrecognized platform {platform!r} (expected 'google' "
        f"or 'linkedin') -- refusing to silently default an unmapped "
        f"platform's status to 'archived'. Add an explicit status map for "
        f"this platform before backfilling its campaigns."
    )


# ---------------------------------------------------------------------------
# Classification -- interim substring logic (see module docstring)
# ---------------------------------------------------------------------------
def _detect_program(name: str) -> str:
    """Cloned verbatim (lowercased) from
    /mnt/c/mcp-servers/anomaly-engine/build_dashboard.py's detect_program()."""
    n = (name or "").lower()
    if "flagship" in n:
        return "flagship"
    if "edgeup" in n or "edge_up" in n:
        return "edgeup"
    if "pwc" in n:
        return "pwc"
    if "switchup" in n:
        return "switchup"
    if "levelup" in n:
        return "levelup"
    return "other"


# SEED vocabulary only -- see module docstring's "Audience token detection" note.
_AUDIENCE_VOCAB = [
    "inmarket", "technologyindustry", "domainsales", "conversation",
    "examtakers", "jobfunction", "lookalike", "retarget", "remarketing",
]


def _detect_audience(name: str) -> str | None:
    n = (name or "").lower().replace("_", "").replace("-", "")
    for token in _AUDIENCE_VOCAB:
        if token in n:
            return token
    return None


def classify_campaign(platform: str, campaign_name: str) -> dict:
    """Returns {segment, program, audience, needs_attention}.

    needs_attention is True exactly when the campaign couldn't be cleanly
    classified: an unrecognized program (bucketed "other") or an
    unrecognized Google segment (bucketed "Other" -- PMax/YouTube/anything
    without brand|discovery|_nb_ in the name). An unmatched *audience* token
    alone does NOT set needs_attention -- see module docstring; the
    controlled audience vocabulary is explicitly a work in progress."""
    if platform == "google":
        segment = collect_kw_perf.classify_google_campaign(campaign_name) or "Other"
    elif platform == "linkedin":
        # The whole LinkedIn ad account maps 1:1 to the "LinkedIn" segment
        # today (no sub-parsing) -- same rule build_segment_tables.py uses.
        segment = "LinkedIn"
    else:
        segment = "Other"

    program = _detect_program(campaign_name)
    audience = _detect_audience(campaign_name)
    needs_attention = (program == "other") or (segment == "Other")

    return {
        "segment": segment,
        "program": program,
        "audience": audience,
        "needs_attention": needs_attention,
    }


def build_registry_rows(raw_campaigns: list[dict], snapshot_date: date) -> list[dict]:
    """raw_campaigns: [{platform, campaign_id, campaign_name, status, channel_type}, ...]
    (the common shape fetch_google_campaigns/fetch_linkedin_campaigns/fixtures
    all produce). Returns a list of dicts, each carrying every classification
    field (for the markdown report) plus a nested "registry_row" dict that
    conforms field-for-field to schemas.CampaignRegistryRow (validated by
    bq_writer.write_rows() at write time -- not re-validated here)."""
    out = []
    for raw in raw_campaigns:
        platform = raw.get("platform") or ""
        campaign_name = raw.get("campaign_name") or ""
        classified = classify_campaign(platform, campaign_name)
        status = _map_status(platform, raw.get("status"))
        registry_row = {
            "campaign_name": campaign_name,
            "platform": platform,
            "segment": classified["segment"],
            "program": classified["program"],
            "geo": "in",
            "funnel_stage": classified["audience"],
            "utm_campaign": campaign_name,
            "utm_source": platform,
            "utm_medium": "cpc",
            "platform_campaign_id": raw.get("campaign_id"),
            "status": status,
            "snapshot_date": str(snapshot_date),
        }
        out.append({
            "campaign_name": campaign_name,
            "platform_campaign_id": raw.get("campaign_id"),
            "platform": platform,
            "status": status,
            "segment": classified["segment"],
            "program": classified["program"],
            "audience": classified["audience"],
            "needs_attention": classified["needs_attention"],
            "registry_row": registry_row,
        })
    return out


# ---------------------------------------------------------------------------
# Markdown backfill report
# ---------------------------------------------------------------------------
def _md_cell(value) -> str:
    """Escape a free-text value for safe embedding inside a single markdown
    table cell.

    BLOCKER fix (code review): live campaign names are not guaranteed
    pipe-free -- e.g. the real `Google | Display | Targeted Audiences | July
    2025` campaign -- and an unescaped `|` inside a cell is parsed by every
    markdown table renderer as an extra column delimiter, shattering the
    whole row (and every column after it) rather than just looking odd.
    Backtick is also escaped since every cell that carries a campaign name is
    manually wrapped in a pair of literal backticks (`` `{...}` ``) by the
    caller -- an unescaped backtick inside the value could prematurely close
    that code span. Backslash is escaped FIRST, before the other two, so the
    backslashes this function itself inserts are not re-escaped by the later
    replacements.
    """
    s = "" if value is None else str(value)
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def render_backfill_markdown(classified: list[dict], snapshot_date: date) -> str:
    total = len(classified)
    flagged = [c for c in classified if c["needs_attention"]]
    by_platform: dict = {}
    for c in classified:
        by_platform.setdefault(c["platform"], 0)
        by_platform[c["platform"]] += 1

    lines = [
        "# Campaign Registry Backfill -- Perf Team Review",
        "",
        f"Generated by `generation/build_campaign_registry.py` for snapshot_date "
        f"`{snapshot_date}`. Status: **draft for perf-team confirmation** "
        f"(PLAN.md Phase 0 Step 2). Rows already validated against "
        f"`schemas.CampaignRegistryRow` and written via the guarded writer "
        f"(DRY_RUN default -- see config.py).",
        "",
        f"- Total campaigns: **{total}**",
        f"- Needs attention (`other` program or unparseable segment): **{len(flagged)}**",
        "- By platform: " + ", ".join(f"{p}={n}" for p, n in sorted(by_platform.items())),
        "",
        "Convention reference: `docs/campaign-naming-convention.md` (Sections 2-4). "
        "Existing campaigns are NOT renamed -- this table only assigns registry "
        "metadata to the live, as-is campaign name.",
        "",
    ]
    lines += _registry_table_lines(classified)
    return "\n".join(lines)


def _registry_table_lines(classified: list[dict]) -> list[str]:
    """The "Campaigns needing attention" + "All campaigns" tables, shared by
    the backfill report and the --refresh report (split out 2026-10-27; the
    backfill report's bytes are unchanged)."""
    flagged = [c for c in classified if c["needs_attention"]]
    lines = [
        "## Campaigns needing attention",
        "",
    ]
    if flagged:
        lines += [
            "| campaign_name | platform_campaign_id | platform | proposed program | "
            "proposed segment | proposed audience | status | why flagged |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for c in flagged:
            why = []
            if c["program"] == "other":
                why.append("program=other")
            if c["segment"] == "Other":
                why.append("segment unparseable")
            lines.append(
                f"| `{_md_cell(c['campaign_name'])}` | {_md_cell(c['platform_campaign_id'])} | "
                f"{_md_cell(c['platform'])} | {_md_cell(c['program'])} | {_md_cell(c['segment'])} | "
                f"{_md_cell(c['audience']) or '_(none)_'} | {_md_cell(c['status'])} | "
                f"{_md_cell(', '.join(why))} |"
            )
    else:
        lines.append("_(none -- every campaign classified cleanly)_")
    lines += ["", "## All campaigns", ""]
    lines += [
        "| campaign_name | platform_campaign_id | platform | proposed program | "
        "proposed segment | proposed audience | status | needs_attention |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in sorted(classified, key=lambda c: (c["platform"], c["campaign_name"])):
        lines.append(
            f"| `{_md_cell(c['campaign_name'])}` | {_md_cell(c['platform_campaign_id'])} | "
            f"{_md_cell(c['platform'])} | {_md_cell(c['program'])} | {_md_cell(c['segment'])} | "
            f"{_md_cell(c['audience']) or '_(none)_'} | {_md_cell(c['status'])} | "
            f"{'YES' if c['needs_attention'] else 'no'} |"
        )
    lines.append("")
    return lines


def write_backfill_markdown(classified: list[dict], snapshot_date: date,
                             out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_backfill_markdown(classified, snapshot_date), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Weekly carry-forward refresh (--refresh) -- see module docstring
# ---------------------------------------------------------------------------
# The exact write-time contract -- prior rows (a live SELECT *, a preview
# file, a fixture) are stripped to these keys so a read-layer-only column
# can't trip schemas._Row's extra="forbid" at write time.
_REGISTRY_ROW_FIELDS = tuple(schemas.CampaignRegistryRow.model_fields)

_PREVIEW_PATH = config.STAGING_DIR / "bq_preview_campaign_registry.json"

# Event kinds merge_refresh() reports, in markdown/summary order.
_REFRESH_EVENTS = ("new", "renamed", "status_changed", "disappeared",
                   "overrides_kept", "carried_unfetched")


def _registry_key(platform, platform_campaign_id, campaign_name) -> tuple[str, str]:
    """(platform, platform_campaign_id), or (platform, 'name:'+campaign_name)
    when the id is null/blank. Never the name alone when an id exists: the
    live registry has 3 LinkedIn names shared by 2 distinct ids each."""
    pid = "" if platform_campaign_id is None else str(platform_campaign_id).strip()
    if pid:
        return (platform or "", pid)
    return (platform or "", "name:" + (campaign_name or ""))


def _as_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _strip_to_model_fields(row: dict) -> dict:
    out = {k: row.get(k) for k in _REGISTRY_ROW_FIELDS if k in row}
    if out.get("platform_campaign_id") is not None:
        out["platform_campaign_id"] = str(out["platform_campaign_id"])
    return out


def _load_prior_rows_from_bq() -> list[dict]:
    """Latest row per registry key across ALL snapshots (same client/allowlist
    pattern as registry._load_from_bq). Across-all rather than MAX-only so a
    key some earlier snapshot had is never lost even if a later snapshot
    were incomplete. Raises on any failure -- the caller refuses to write."""
    table_id = bq_writer.validate_target("campaign_registry")
    client = bq_writer.get_client()          # lazy bigquery import inside bq_writer
    query = f"""
        SELECT * EXCEPT(rn) FROM (
          SELECT *, ROW_NUMBER() OVER (
            PARTITION BY platform,
                         COALESCE(platform_campaign_id, CONCAT('name:', campaign_name))
            ORDER BY snapshot_date DESC
          ) AS rn
          FROM `{table_id}`
        )
        WHERE rn = 1
    """
    result = client.query(query).result(timeout=120)
    return [dict(r.items()) for r in result]


def load_prior_rows(prior_fixture: str | None) -> tuple[list[dict], date | None]:
    """The registry a --refresh merges onto, plus its max snapshot_date.

    - `prior_fixture` given (tests): that JSON file (bare list or {rows: [...]}).
    - live (not config.DRY_RUN): BigQuery, `_load_prior_rows_from_bq()`.
      Raises on failure -- never swallowed (main() then exits 2, no write).
    - DRY_RUN: the local staging preview a previous DRY_RUN write left
      (the same file registry.py's DRY_RUN fallback reads), else [].
    Rows come back stripped to schemas.CampaignRegistryRow's fields."""
    if prior_fixture:
        rows = load_fixture(prior_fixture)
    elif not config.DRY_RUN:
        rows = _load_prior_rows_from_bq()
    elif _PREVIEW_PATH.exists():
        rows = load_fixture(_PREVIEW_PATH)
    else:
        rows = []
    rows = [_strip_to_model_fields(r) for r in rows]
    dates = [d for d in (_as_date(r.get("snapshot_date")) for r in rows) if d]
    return rows, (max(dates) if dates else None)


def _dedupe_prior(prior_rows: list[dict]) -> list[dict]:
    """One prior row per key (latest snapshot_date wins; ties -> later row),
    then drop a null-id row whose (platform, campaign_name) also exists as
    an id'd row -- after a refresh fills in a missing id, the across-all-
    snapshots read returns BOTH the old name-keyed row and the new id-keyed
    one, and the stale one must not resurface as a 'disappeared' ghost."""
    by_key: dict = {}
    for r in prior_rows:
        k = _registry_key(r.get("platform"), r.get("platform_campaign_id"),
                          r.get("campaign_name"))
        cur = by_key.get(k)
        if cur is None or (_as_date(r.get("snapshot_date")) or date.min) >= \
                (_as_date(cur.get("snapshot_date")) or date.min):
            by_key[k] = r
    id_names = {(k[0], r.get("campaign_name")) for k, r in by_key.items()
                if not k[1].startswith("name:")}
    return [r for k, r in by_key.items()
            if not (k[1].startswith("name:") and (k[0], r.get("campaign_name")) in id_names)]


def _empty_refresh_report() -> dict:
    report = {kind: [] for kind in _REFRESH_EVENTS}
    report["by_platform"] = {}
    report["prior_count"] = 0
    return report


def _bump(report: dict, platform: str, key: str) -> None:
    counts = report["by_platform"].setdefault(
        platform, {"prior": 0, "prior_not_archived": 0,
                   **{kind: 0 for kind in _REFRESH_EVENTS}})
    counts[key] += 1


def merge_refresh(prior_rows: list[dict], live_raw: list[dict],
                  fetched_platforms: set, snapshot_date: date,
                  now: datetime) -> tuple[list[dict], dict]:
    """Pure merge: (prior registry rows, live raw campaigns) -> (complete new
    snapshot rows, change report). See the module docstring for the rules.

    `fetched_platforms` = the platforms whose live listing actually came
    back this run; a prior row on any other platform is carried forward
    unchanged (status intact). Every output row carries snapshot_date=new,
    holds only schemas.CampaignRegistryRow keys, and keeps registered_at as
    the datetime/ISO string it had (now, for new campaigns).

    'disappeared' counts only a TRANSITION into archived (a prior row that
    wasn't archived, absent from its fetched platform's listing); an
    already-archived absent row is carried silently, so one deleted campaign
    isn't re-reported every week forever. 'overrides_kept' counts renamed
    rows whose hand-edited classification was kept; a same-name row's
    classification is always copied verbatim, edited or not."""
    prior = _dedupe_prior(prior_rows)
    report = _empty_refresh_report()
    report["prior_count"] = len(prior)
    prior_by_key: dict = {}
    for r in prior:
        prior_by_key[_registry_key(r.get("platform"), r.get("platform_campaign_id"),
                                   r.get("campaign_name"))] = r
        _bump(report, r.get("platform") or "", "prior")
        if (r.get("status") or "active") != "archived":
            _bump(report, r.get("platform") or "", "prior_not_archived")

    merged: list[dict] = []
    consumed: set = set()
    seen_live: set = set()
    for raw in live_raw:
        platform = raw.get("platform") or ""
        name = raw.get("campaign_name") or ""
        cid = raw.get("campaign_id")
        live_key = _registry_key(platform, cid, name)
        if live_key in seen_live:
            log.warning("Duplicate live campaign %s in this listing -- keeping the "
                        "first occurrence", live_key)
            continue
        seen_live.add(live_key)

        match_key = None
        name_key = (platform, "name:" + name)
        if live_key in prior_by_key and live_key not in consumed:
            match_key = live_key
        elif name_key in prior_by_key and name_key not in consumed:
            match_key = name_key       # prior row had no id -- matched by name

        if match_key is None:                                   # (a) new
            row = build_registry_rows([raw], snapshot_date)[0]["registry_row"]
            row["registered_at"] = now
            merged.append(row)
            _bump(report, platform, "new")
            report["new"].append(row)
            continue

        consumed.add(match_key)
        old = prior_by_key[match_key]
        old_status = old.get("status") or "active"
        new_status = _map_status(platform, raw.get("status"))
        row = dict(old)
        row["platform_campaign_id"] = (str(cid) if cid not in (None, "")
                                       else old.get("platform_campaign_id"))
        row["status"] = new_status
        row["snapshot_date"] = str(snapshot_date)
        if not row.get("registered_at"):
            row["registered_at"] = now

        old_name = old.get("campaign_name") or ""
        if name != old_name:                                    # (c) renamed
            row["campaign_name"] = name
            row["utm_campaign"] = name
            auto_old = classify_campaign(platform, old_name)
            untouched = ((old.get("segment"), old.get("program"), old.get("funnel_stage"))
                         == (auto_old["segment"], auto_old["program"], auto_old["audience"]))
            if untouched:
                auto_new = classify_campaign(platform, name)
                row["segment"] = auto_new["segment"]
                row["program"] = auto_new["program"]
                row["funnel_stage"] = auto_new["audience"]
            else:
                log.info("Renamed %s %r -> %r: keeping its hand-edited classification "
                         "(segment=%r program=%r funnel_stage=%r) as a manual override",
                         platform, old_name, name, old.get("segment"),
                         old.get("program"), old.get("funnel_stage"))
                _bump(report, platform, "overrides_kept")
                report["overrides_kept"].append(
                    {**row, "auto": classify_campaign(platform, name)})
            _bump(report, platform, "renamed")
            report["renamed"].append({**row, "old_name": old_name,
                                      "reclassified": untouched})
        if new_status != old_status:                            # (b)/(c) status
            _bump(report, platform, "status_changed")
            report["status_changed"].append({**row, "old_status": old_status})
        merged.append(row)

    for key, old in prior_by_key.items():                       # absent from live
        if key in consumed:
            continue
        platform = old.get("platform") or ""
        row = dict(old)
        row["snapshot_date"] = str(snapshot_date)
        row["status"] = old.get("status") or "active"
        if not row.get("registered_at"):
            row["registered_at"] = now
        if platform in fetched_platforms:
            if row["status"] != "archived":
                _bump(report, platform, "disappeared")
                report["disappeared"].append({**row, "old_status": row["status"]})
            row["status"] = "archived"
        else:
            _bump(report, platform, "carried_unfetched")
            report["carried_unfetched"].append(row)
        merged.append(row)

    merged = [{k: r.get(k) for k in _REGISTRY_ROW_FIELDS} for r in merged]
    return merged, report


def _suspect_partial_platforms(report: dict, fetched_platforms: set,
                               min_retain_ratio: float) -> set:
    """Fetched platforms whose listing looks truncated: fewer than
    ceil(min_retain_ratio * N) of the N prior not-archived campaigns were
    still in it. Ad platforms keep removed/archived campaigns in their
    listings (Google returns REMOVED, LinkedIn ARCHIVED), so a mass
    disappearance is far more likely a partial fetch -- e.g. the July
    LinkedIn pagination bug that returned 100 of 175 -- than reality.
    Archiving those rows would silently drop live campaigns out of
    generate_assets' active set, so main() carries such a platform forward
    unchanged instead (added 2026-10-27)."""
    suspect = set()
    for platform in fetched_platforms:
        counts = report["by_platform"].get(platform)
        if not counts or not counts["prior_not_archived"]:
            continue
        base = counts["prior_not_archived"]
        if base - counts["disappeared"] < math.ceil(min_retain_ratio * base):
            suspect.add(platform)
    return suspect


def _classified_view(row: dict) -> dict:
    """A merged registry row in the shape _registry_table_lines() renders."""
    return {
        "campaign_name": row.get("campaign_name") or "",
        "platform_campaign_id": row.get("platform_campaign_id"),
        "platform": row.get("platform") or "",
        "status": row.get("status"),
        "segment": row.get("segment"),
        "program": row.get("program"),
        "audience": row.get("funnel_stage"),
        "needs_attention": (row.get("program") == "other") or (row.get("segment") == "Other"),
    }


def _refresh_totals(report: dict) -> dict:
    return {kind: len(report[kind]) for kind in _REFRESH_EVENTS}


def render_refresh_markdown(merged: list[dict], report: dict, snapshot_date: date,
                            prior_max: date | None, sources: dict) -> str:
    view = [_classified_view(r) for r in merged]
    totals = _refresh_totals(report)
    by_platform: dict = {}
    for c in view:
        by_platform[c["platform"]] = by_platform.get(c["platform"], 0) + 1
    flagged = sum(1 for c in view if c["needs_attention"])

    def cell(v):
        return _md_cell(v)

    lines = [
        f"# Campaign Registry Refresh -- {snapshot_date}",
        "",
        (f"Generated by `generation/build_campaign_registry.py --refresh` for "
         f"snapshot_date `{snapshot_date}`, carried forward from the prior "
         f"registry (latest snapshot `{prior_max or 'none'}`, "
         f"{report['prior_count']} campaign(s)). Rows validated against "
         f"`schemas.CampaignRegistryRow` and written via the guarded writer "
         f"(DRY_RUN default -- see config.py)."),
        "",
        f"- Total campaigns: **{len(merged)}**",
        "- Sources: " + ", ".join(f"{p}={s}" for p, s in sorted(sources.items())),
        "- Changes: " + ", ".join(f"{k}={v}" for k, v in totals.items()),
        f"- Needs attention (`other` program or unparseable segment): **{flagged}**",
        "- By platform: " + ", ".join(f"{p}={n}" for p, n in sorted(by_platform.items())),
        "",
        "## New since last snapshot",
        "",
    ]
    if report["new"]:
        lines += [("| campaign_name | platform_campaign_id | platform | program | segment | "
                   "audience | status |"), "|---|---|---|---|---|---|---|"]
        for r in report["new"]:
            lines.append(f"| `{cell(r['campaign_name'])}` | {cell(r['platform_campaign_id'])} | "
                         f"{cell(r['platform'])} | {cell(r['program'])} | {cell(r['segment'])} | "
                         f"{cell(r['funnel_stage']) or '_(none)_'} | {cell(r['status'])} |")
    else:
        lines.append("_(none)_")

    lines += ["", "## Renamed", ""]
    if report["renamed"]:
        lines += ["| platform | platform_campaign_id | old name | new name | classification |",
                  "|---|---|---|---|---|"]
        for r in report["renamed"]:
            how = ("re-derived from new name" if r["reclassified"]
                   else "manual override kept")
            lines.append(f"| {cell(r['platform'])} | {cell(r['platform_campaign_id'])} | "
                         f"`{cell(r['old_name'])}` | `{cell(r['campaign_name'])}` | {how} |")
    else:
        lines.append("_(none)_")

    lines += ["", "## Status changes", ""]
    if report["status_changed"]:
        lines += ["| campaign_name | platform_campaign_id | platform | was | now |",
                  "|---|---|---|---|---|"]
        for r in report["status_changed"]:
            lines.append(f"| `{cell(r['campaign_name'])}` | {cell(r['platform_campaign_id'])} | "
                         f"{cell(r['platform'])} | {cell(r['old_status'])} | {cell(r['status'])} |")
    else:
        lines.append("_(none)_")

    lines += ["", "## Disappeared (archived)", ""]
    if report["disappeared"]:
        lines += ["| campaign_name | platform_campaign_id | platform | was |",
                  "|---|---|---|---|"]
        for r in report["disappeared"]:
            lines.append(f"| `{cell(r['campaign_name'])}` | {cell(r['platform_campaign_id'])} | "
                         f"{cell(r['platform'])} | {cell(r['old_status'])} |")
    else:
        lines.append("_(none)_")

    lines += ["", "## Manual overrides kept", ""]
    if report["overrides_kept"]:
        lines += [("| campaign_name | platform_campaign_id | kept program / segment / audience | "
                   "auto would be |"), "|---|---|---|---|"]
        for r in report["overrides_kept"]:
            auto = r["auto"]
            lines.append(f"| `{cell(r['campaign_name'])}` | {cell(r['platform_campaign_id'])} | "
                         f"{cell(r['program'])} / {cell(r['segment'])} / "
                         f"{cell(r['funnel_stage']) or '_(none)_'} | {cell(auto['program'])} / "
                         f"{cell(auto['segment'])} / {cell(auto['audience']) or '_(none)_'} |")
    else:
        lines.append("_(none)_")

    if report["carried_unfetched"]:
        lines += ["", (f"_{len(report['carried_unfetched'])} campaign(s) carried forward "
                       f"unchanged because their platform was not fetched this run._")]
    lines.append("")
    lines += _registry_table_lines(view)
    return "\n".join(lines)


def _main_refresh(args, snapshot_date: date) -> int:
    """--refresh path of main(); see the module docstring. Exit codes:
    0 ok (or skipped as fresh), 1 nothing fetched / write failed,
    2 prior registry unreadable (no write), 3 shrink guard (no write),
    4 DEGRADED -- the complete carry-forward snapshot WAS written, but a
    requested platform was carried forward (fetch failed or listing looked
    partial), so its new/changed campaigns are missing from it."""
    ratio = 0.9 if args.min_retain_ratio is None else args.min_retain_ratio
    if not 0 < ratio <= 1:
        log.error("--min-retain-ratio must be in (0, 1] (got %r)", ratio)
        return 1

    # (i) Prior registry. A snapshot written without it would have no
    # carry-forward -- and the dashboard reads only MAX(snapshot_date), so
    # an incomplete one blanks every campaign missing from it.
    try:
        prior_rows, prior_max = load_prior_rows(args.prior_fixture)
    except Exception as e:  # noqa: BLE001 -- any prior-read failure means "do not write"
        log.error("REGISTRY_REFRESH aborted: could not load the prior registry (%s) "
                  "-- refusing to write a snapshot without carry-forward.", e)
        return 2
    if not prior_rows:
        log.warning("No prior registry rows (%s) -- every campaign registers as new.",
                    "DRY_RUN: no local preview yet, preview only" if config.DRY_RUN
                    else "the live table is empty")

    # (ii) Staleness gate -- before any ad-platform API call.
    if args.only_if_stale_days is not None and prior_max is not None:
        age = (snapshot_date - prior_max).days
        if age < args.only_if_stale_days:
            log.info("registry fresh (latest=%s, %d day(s) old < %d) -- skipping",
                     prior_max, age, args.only_if_stale_days)
            return 0

    # (iii) Per-platform fetch, each fail-soft: a platform that fails is
    # simply carried forward unchanged.
    live_raw: list[dict] = []
    fetched: set = set()
    if args.from_fixture:
        live_raw = load_fixture(args.from_fixture)
        fetched = {r.get("platform") for r in live_raw if r.get("platform")}
        requested = set(fetched)
        log.info("Loaded %d raw campaign(s) from fixture %s (APIs skipped; fetched=%s)",
                 len(live_raw), args.from_fixture, sorted(fetched))
    else:
        requested = ({"google"} if args.from_google else set()) | \
                    ({"linkedin"} if args.from_linkedin else set())
        if args.from_google:
            try:
                google_rows = fetch_google_campaigns()
                log.info("Pulled %d live Google Ads campaign(s)", len(google_rows))
                live_raw += google_rows
                fetched.add("google")
            except FileNotFoundError as e:
                log.warning("Google Ads credentials not found (%s) -- carrying Google forward", e)
            except KeyError as e:
                log.warning("Google Ads .env missing a required key (%s) -- carrying Google forward", e)
            except ImportError as e:
                log.warning("google-ads package not importable (%s) -- carrying Google forward", e)
            except Exception as e:  # noqa: BLE001 -- per-platform fail-soft, by design
                log.warning("Google Ads campaign pull failed (%s) -- carrying Google forward", e)
        if args.from_linkedin:
            try:
                linkedin_rows = fetch_linkedin_campaigns()
                log.info("Pulled %d live LinkedIn Ads campaign(s)", len(linkedin_rows))
                live_raw += linkedin_rows
                fetched.add("linkedin")
            except FileNotFoundError as e:
                log.warning("LinkedIn Ads credentials not found (%s) -- carrying LinkedIn forward", e)
            except KeyError as e:
                log.warning("LinkedIn Ads .env missing a required key (%s) -- carrying LinkedIn forward", e)
            except Exception as e:  # noqa: BLE001 -- per-platform fail-soft, by design
                log.warning("LinkedIn Ads campaign pull failed (%s) -- carrying LinkedIn forward", e)

    # (iv) Nothing fetched -> nothing new to say; the prior snapshot stands.
    if not fetched:
        log.error("REGISTRY_REFRESH: no platform could be fetched -- no snapshot written.")
        return 1

    now = datetime.now(UTC)
    merged, report = merge_refresh(prior_rows, live_raw, fetched, snapshot_date, now)
    suspect = _suspect_partial_platforms(report, fetched, ratio)
    accepted = suspect & set(args.accept_partial or ())
    for p in sorted(accepted):
        c = report["by_platform"][p]
        log.warning("%s listing is missing %d of its %d prior not-archived campaign(s) -- "
                    "accepted via --accept-partial %s: archiving them.",
                    p, c["disappeared"], c["prior_not_archived"], p)
    suspect -= accepted
    if suspect:
        for p in sorted(suspect):
            c = report["by_platform"][p]
            log.warning("LOUD WARNING: %s listing looks PARTIAL -- %d of its %d prior "
                        "not-archived campaign(s) are missing (min-retain-ratio %.2f). "
                        "Carrying %s forward unchanged instead of archiving them. If they "
                        "really were removed, accept it once with --accept-partial %s "
                        "(run_daily.sh: REGISTRY_ACCEPT_PARTIAL=%s).",
                        p, c["disappeared"], c["prior_not_archived"], ratio, p, p, p)
        fetched -= suspect
        live_raw = [r for r in live_raw if r.get("platform") not in suspect]
        if not fetched:
            log.error("REGISTRY_REFRESH: every fetched platform looked partial -- "
                      "no snapshot written.")
            return 1
        merged, report = merge_refresh(prior_rows, live_raw, fetched, snapshot_date, now)

    # (v) Shrink guard. Carry-forward makes len(merged) >= prior by
    # construction, so this only fires on a bug -- which is exactly when a
    # write must not happen.
    if report["prior_count"] and len(merged) < math.ceil(ratio * report["prior_count"]):
        log.error("REGISTRY_REFRESH shrink guard: merged snapshot has %d row(s) vs %d "
                  "prior (min-retain-ratio %.2f) -- refusing to write.",
                  len(merged), report["prior_count"], ratio)
        return 3

    # Source per platform: fetched; partial / failed = REQUESTED but carried
    # forward (-> exit 4 below); carried = not requested this run.
    platforms = sorted({r.get("platform") for r in prior_rows + live_raw if r.get("platform")}
                       | set(fetched) | requested)
    sources = {p: ("fetched" if p in fetched else "partial" if p in suspect
                   else "failed" if p in requested else "carried") for p in platforms}
    degraded = sorted(requested - fetched)
    out_md = (Path(args.out_md) if args.out_md
              else config.STAGING_DIR / f"campaign_registry_refresh_{snapshot_date}.md")

    # (vi) One complete snapshot -> today's partition (WRITE_TRUNCATE, so a
    # same-day re-run replaces rather than duplicates).
    try:
        written = bq_writer.write_rows("campaign_registry", merged)
    except Exception as e:  # noqa: BLE001 -- same contract as the backfill write below
        log.error("Write to campaign_registry failed: %s", e)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_refresh_markdown(merged, report, snapshot_date,
                                                  prior_max, sources), encoding="utf-8")
        log.info("Refresh report (failed write) written to %s", out_md)
        return 1

    # (vii) Change report (staging/ by default -- never the tracked backfill doc).
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_refresh_markdown(merged, report, snapshot_date,
                                              prior_max, sources), encoding="utf-8")
    if config.DRY_RUN:
        log.info("DRY_RUN: preview written to %s (%d row(s))", _PREVIEW_PATH, len(merged))
    else:
        log.info("Wrote %d row(s) to BigQuery (campaign_registry).", written)
    log.info("Refresh report written to %s", out_md)

    # (viii) One grep-able summary line.
    totals = _refresh_totals(report)
    per_platform = " ".join(
        f"{p}={sum(1 for r in merged if r.get('platform') == p)}" for p in platforms)
    needs_attention = sum(1 for r in merged if _classified_view(r)["needs_attention"])
    log.info("REGISTRY_REFRESH snapshot=%s total=%d %s %s needs_attention=%d sources=%s",
             snapshot_date, len(merged), per_platform,
             " ".join(f"{k}={v}" for k, v in totals.items()), needs_attention,
             ",".join(f"{p}:{s}" for p, s in sources.items()))

    # (ix) Degraded -> non-zero, so it is never silent. The snapshot above is
    # still complete and valid (the chain can run on it), but its advanced
    # snapshot_date would otherwise hide a platform frozen at its prior state
    # from every MAX(snapshot_date) freshness check.
    if degraded:
        log.error("REGISTRY_REFRESH DEGRADED: requested platform(s) %s carried forward "
                  "unchanged (%s) -- the snapshot was written, but their new, renamed and "
                  "status-changed campaigns are missing from it. Exit 4.",
                  ",".join(degraded), ", ".join(f"{p}:{sources[p]}" for p in degraded))
        return 4
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Campaign registry backfill builder (PLAN.md Phase 0 Step 2).")
    p.add_argument("--from-google", action="store_true",
                    help="Pull the live Google Ads campaign list via GAQL.")
    p.add_argument("--from-linkedin", action="store_true",
                    help="Pull the live LinkedIn Ads campaign list via REST.")
    p.add_argument("--from-fixture", type=str, default=None,
                    help="Path to a JSON fixture of raw campaign dicts -- skips "
                         "both APIs entirely (for testing without credentials).")
    p.add_argument("--snapshot-date", type=str, default=None,
                    help="Snapshot date YYYY-MM-DD (default: today IST).")
    # Default resolved in main(): DEFAULT_MD_PATH for a backfill, a staging/
    # file for --refresh (so a weekly run never dirties the tracked doc).
    p.add_argument("--out-md", type=str, default=None,
                    help=f"Markdown report output path (default: {DEFAULT_MD_PATH}; "
                         f"with --refresh: staging/campaign_registry_refresh_<date>.md).")
    p.add_argument("--refresh", action="store_true",
                    help="Weekly carry-forward refresh: merge the live listing onto the "
                         "prior registry instead of a one-shot backfill.")
    p.add_argument("--prior-fixture", type=str, default=None,
                    help="--refresh only (tests): load the prior registry rows from this "
                         "JSON file (bare list or {rows: [...]}) instead of BQ/staging.")
    p.add_argument("--only-if-stale-days", type=int, default=None,
                    help="--refresh only: exit 0 without any API call unless the latest "
                         "prior snapshot is at least N days older than --snapshot-date.")
    p.add_argument("--min-retain-ratio", type=float, default=None,
                    help="--refresh only (default 0.9): refuse to write a snapshot smaller "
                         "than this fraction of the prior one, and carry forward a platform "
                         "whose listing lost more than (1 - ratio) of its prior campaigns.")
    p.add_argument("--accept-partial", action="append", choices=("google", "linkedin"),
                    default=None, metavar="PLATFORM",
                    help="--refresh only, repeatable: this platform's listing really did "
                         "lose that many campaigns -- archive them instead of tripping the "
                         "partial-listing guard (and exiting 4). Use for one run, not "
                         "permanently.")
    p.add_argument("--force-backfill", action="store_true",
                    help="Backfill only (no --refresh): allow a LIVE backfill even though "
                         "campaign_registry already has rows. It replaces today's partition "
                         "with ONLY the requested platforms, every classification re-derived "
                         "from the names -- and registry.py/the dashboard read only the "
                         "latest snapshot. Prefer --refresh.")
    return p.parse_args(argv)


def _live_registry_state() -> tuple[int, date | None]:
    """(row count, MAX(snapshot_date)) of the live campaign_registry. Same
    client/allowlist as _load_prior_rows_from_bq(). Raises on any failure."""
    table_id = bq_writer.validate_target("campaign_registry")
    client = bq_writer.get_client()          # lazy bigquery import inside bq_writer
    query = f"SELECT COUNT(*) AS n, MAX(snapshot_date) AS latest FROM `{table_id}`"
    rows = [dict(r.items()) for r in client.query(query).result(timeout=120)]
    if not rows:
        return 0, None
    return int(rows[0].get("n") or 0), _as_date(rows[0].get("latest"))


def _live_backfill_guard(force: bool) -> int:
    """0 if a LIVE backfill may write, else the exit code. A backfill has no
    carry-forward, so over an existing registry it would make a partial,
    re-classified snapshot the latest one (see the module docstring)."""
    if force:
        log.warning("--force-backfill: skipping the existing-registry check. Today's "
                    "campaign_registry partition will hold ONLY the platforms requested "
                    "here, with every classification re-derived from the names.")
        return 0
    try:
        n, latest = _live_registry_state()
    except Exception as e:  # noqa: BLE001 -- any read failure means "do not write"
        log.error("BACKFILL aborted: could not confirm campaign_registry is empty (%s) -- "
                  "refusing a live backfill. If the table really does not exist yet, "
                  "pass --force-backfill.", e)
        return 2
    if n:
        log.error("BACKFILL refused: campaign_registry already holds %d row(s) (latest "
                  "snapshot %s). A backfill has no carry-forward and re-derives every "
                  "classification, and registry.py + the dashboard read only the latest "
                  "snapshot -- use --refresh instead (or --force-backfill to overwrite "
                  "deliberately).", n, latest)
        return 3
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if not (args.from_google or args.from_linkedin or args.from_fixture):
        log.error("Pass at least one of --from-google, --from-linkedin, --from-fixture.")
        return 1

    if args.snapshot_date:
        try:
            snapshot_date = date.fromisoformat(args.snapshot_date)
        except ValueError:
            log.error("--snapshot-date must be YYYY-MM-DD (got %r)", args.snapshot_date)
            return 1
    else:
        snapshot_date = date.fromisoformat(bq_writer.run_date_today_ist())

    if args.refresh:
        if args.force_backfill:
            log.error("--force-backfill only applies without --refresh.")
            return 1
        return _main_refresh(args, snapshot_date)
    if (args.prior_fixture or args.only_if_stale_days is not None
            or args.min_retain_ratio is not None or args.accept_partial):
        log.error("--prior-fixture/--only-if-stale-days/--min-retain-ratio/--accept-partial "
                  "only apply with --refresh.")
        return 1
    if args.out_md is None:
        args.out_md = str(DEFAULT_MD_PATH)

    # A live backfill may only seed an EMPTY registry -- checked before any
    # ad-platform API call. DRY_RUN only writes the local preview.
    if not config.DRY_RUN:
        rc = _live_backfill_guard(args.force_backfill)
        if rc:
            return rc

    raw_campaigns: list[dict] = []

    if args.from_fixture:
        raw_campaigns = load_fixture(args.from_fixture)
        log.info("Loaded %d raw campaign(s) from fixture %s (APIs skipped)",
                  len(raw_campaigns), args.from_fixture)
    else:
        if args.from_google:
            try:
                google_rows = fetch_google_campaigns()
                log.info("Pulled %d live Google Ads campaign(s)", len(google_rows))
                raw_campaigns += google_rows
            except FileNotFoundError as e:
                log.error("Google Ads credentials not found: %s", e)
                return 1
            except KeyError as e:
                log.error("Google Ads .env missing a required key: %s", e)
                return 1
            except ImportError as e:
                log.error("google-ads package not importable: %s", e)
                return 1
            except Exception as e:
                log.error("Google Ads campaign pull failed: %s", e)
                return 1

        if args.from_linkedin:
            try:
                linkedin_rows = fetch_linkedin_campaigns()
                log.info("Pulled %d live LinkedIn Ads campaign(s)", len(linkedin_rows))
                raw_campaigns += linkedin_rows
            except FileNotFoundError as e:
                log.error("LinkedIn Ads credentials not found: %s", e)
                return 1
            except KeyError as e:
                log.error("LinkedIn Ads .env missing a required key: %s", e)
                return 1
            except Exception as e:
                log.error("LinkedIn Ads campaign pull failed: %s", e)
                return 1

    if not raw_campaigns:
        log.info("No campaigns pulled -- nothing to register.")
        return 0

    classified = build_registry_rows(raw_campaigns, snapshot_date)
    flagged = [c for c in classified if c["needs_attention"]]
    log.info("Classified %d campaign(s), %d flagged needs_attention",
              len(classified), len(flagged))

    registry_rows = [c["registry_row"] for c in classified]
    try:
        written = bq_writer.write_rows("campaign_registry", registry_rows)
    except Exception as e:
        log.error("Write to campaign_registry failed: %s", e)
        # Still emit the markdown report -- useful for debugging even if the
        # write failed (e.g. a pydantic.ValidationError on one bad row).
        out_path = write_backfill_markdown(classified, snapshot_date, Path(args.out_md))
        log.info("Backfill report (partial run) written to %s", out_path)
        return 1

    out_path = write_backfill_markdown(classified, snapshot_date, Path(args.out_md))
    if config.DRY_RUN:
        log.info("DRY_RUN: preview written to %s (%d row(s))",
                  config.STAGING_DIR / "bq_preview_campaign_registry.json", len(registry_rows))
    else:
        log.info("Wrote %d row(s) to BigQuery (campaign_registry).", written)
    log.info("Backfill report written to %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
