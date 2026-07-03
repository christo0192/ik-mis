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

Usage:
    python3 build_campaign_registry.py --from-google
    python3 build_campaign_registry.py --from-linkedin
    python3 build_campaign_registry.py --from-google --from-linkedin
    python3 build_campaign_registry.py --from-fixture fixtures/registry_campaigns_raw.json
                                                     # skip both APIs entirely;
                                                     # load raw campaign dicts
                                                     # from a JSON fixture

DRY_RUN (default true, see config.py) always applies on the BQ write side,
regardless of --from-fixture -- set DRY_RUN=false for a live BigQuery load.
"""
import argparse
import http.client
import json
import logging
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import bq_writer
import collect_kw_perf
import config

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("build_campaign_registry")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MD_PATH = REPO_ROOT / "docs" / "campaign-registry-backfill.md"

LINKEDIN_API_VERSION = "202605"  # matches /mnt/c/mcp-servers/linkedin-ads/server.py's API_VERSION

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


def _linkedin_get(access_token: str, path: str) -> tuple[int, dict]:
    conn = http.client.HTTPSConnection("api.linkedin.com", timeout=30)
    try:
        conn.request("GET", path, headers={
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": LINKEDIN_API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        })
        resp = conn.getresponse()
        body = resp.read().decode()
        parsed = json.loads(body) if body else {}
        return resp.status, parsed
    finally:
        conn.close()


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
    """
    env = collect_kw_perf.load_env(_linkedin_env_path())
    access_token = env["LINKEDIN_ACCESS_TOKEN"]
    ad_account_id = env["LINKEDIN_AD_ACCOUNT_ID"]

    rows = []
    page_size = 200
    refreshed_once = False
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
        status, data = _linkedin_get(access_token, path)
        if status == 401 and not refreshed_once:
            log.warning("LinkedIn API 401 -- attempting token refresh...")
            new_token = _linkedin_refresh_access_token(env)
            if not new_token:
                raise RuntimeError(
                    "LinkedIn API returned 401 and token refresh failed/unconfigured. "
                    "Run: cd /mnt/c/mcp-servers/linkedin-ads && python get_token.py")
            access_token = new_token
            refreshed_once = True
            page_num -= 1  # retry the same page; doesn't count against the cap
            continue
        if status != 200:
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
    return "\n".join(lines)


def write_backfill_markdown(classified: list[dict], snapshot_date: date,
                             out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_backfill_markdown(classified, snapshot_date), encoding="utf-8")
    return out_path


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
    p.add_argument("--out-md", type=str, default=str(DEFAULT_MD_PATH),
                    help=f"Markdown report output path (default: {DEFAULT_MD_PATH}).")
    return p.parse_args(argv)


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
