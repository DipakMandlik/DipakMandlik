#!/usr/bin/env python3
"""
Generates the local, dependency-free GitHub stats SVG cards used by the
profile README: assets/stats-*.svg, assets/languages-*.svg, assets/trophies-*.svg.

Data comes straight from the GitHub REST + GraphQL APIs for the account named
by --user (real numbers only, never fabricated). When no token is available
(e.g. a local dry run, or the very first commit before the workflow has run),
every metric renders in its honest "pending sync" state instead of a made-up
number, and the next scheduled Action run replaces it with real data.

Usage:
    GITHUB_TOKEN=xxxx python3 generate_profile_assets.py --user DipakMandlik
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

FONT = "'Segoe UI', ui-sans-serif, system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
MONO = "'SFMono-Regular', 'Cascadia Code', Consolas, 'Courier New', monospace"

THEME = {
    "dark": dict(
        bg1="#0F172A", bg2="#111C33", chip_fill="#16253F", chip_stroke="#2C4265",
        hairline="#1E2C46", text_primary="#FFFFFF", text_secondary="#9FB0C6",
        text_muted="#6C7C93", accent1="#2563EB", accent2="#38BDF8", track="#20304F",
    ),
    "light": dict(
        bg1="#FFFFFF", bg2="#F6F9FD", chip_fill="#F2F6FC", chip_stroke="#DCE6F5",
        hairline="#E4EBF5", text_primary="#111827", text_secondary="#64748B",
        text_muted="#8592A6", accent1="#2563EB", accent2="#0EA5E9", track="#E4ECF8",
    ),
}

API = "https://api.github.com"
UA = "profile-asset-generator"


def gh_get(url, token):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": UA,
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def gh_graphql(query, variables, token):
    if not token:
        return None
    req = urllib.request.Request(
        f"{API}/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": UA,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def fetch_data(user, token):
    """Best-effort real fetch. Returns a dict; any missing metric stays None."""
    data = dict(
        public_repos=None, followers=None, total_stars=None, total_forks=None,
        contributions_year=None, total_prs=None, total_issues=None,
        years_on_github=None, languages=[], synced=False,
    )
    try:
        u = gh_get(f"{API}/users/{user}", token)
        data["public_repos"] = u.get("public_repos")
        data["followers"] = u.get("followers")
        created = u.get("created_at")
        if created:
            start = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            data["years_on_github"] = round((datetime.now(timezone.utc) - start).days / 365.25, 1)

        repos, page = [], 1
        while True:
            batch = gh_get(f"{API}/users/{user}/repos?per_page=100&page={page}&type=owner", token)
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1

        data["total_stars"] = sum(r.get("stargazers_count", 0) for r in repos)
        data["total_forks"] = sum(r.get("forks_count", 0) for r in repos)

        lang_bytes = {}
        for r in repos:
            if r.get("fork"):
                continue
            try:
                langs = gh_get(r["languages_url"], token)
            except Exception:
                continue
            for lang, n in langs.items():
                lang_bytes[lang] = lang_bytes.get(lang, 0) + n
        total = sum(lang_bytes.values()) or 1
        data["languages"] = sorted(
            [(lang, round(n / total * 100, 1)) for lang, n in lang_bytes.items()],
            key=lambda x: -x[1],
        )[:6]

        gql = gh_graphql(
            """
            query($login: String!) {
              user(login: $login) {
                contributionsCollection {
                  contributionCalendar { totalContributions }
                  totalPullRequestContributions
                  totalIssueContributions
                }
              }
            }""",
            {"login": user},
            token,
        )
        if gql and gql.get("data", {}).get("user"):
            cc = gql["data"]["user"]["contributionsCollection"]
            data["contributions_year"] = cc["contributionCalendar"]["totalContributions"]
            data["total_prs"] = cc["totalPullRequestContributions"]
            data["total_issues"] = cc["totalIssueContributions"]

        data["synced"] = True
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, OSError) as e:
        print(f"[generate_profile_assets] live fetch unavailable ({e}); rendering pending state", file=sys.stderr)
    return data


def fmt(n):
    if n is None:
        return "—"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def ring(cx, cy, r, pct, color, track):
    import math
    circ = 2 * math.pi * r
    offset = circ * (1 - pct)
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{track}" stroke-width="7"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="7" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}" '
        f'transform="rotate(-90 {cx} {cy})">'
        f'<animate attributeName="stroke-dashoffset" from="{circ:.1f}" to="{offset:.1f}" '
        f'dur="1.4s" begin="0.2s" fill="freeze" calcMode="spline" keySplines="0.2 0 0.2 1"/>'
        f"</circle>"
    )


def synced_badge(t, synced):
    color = "#22C55E" if synced else t["text_muted"]
    label = "LIVE" if synced else "SYNCING"
    return (
        f'<circle cx="0" cy="-4" r="3.4" fill="{color}">'
        f'<animate attributeName="opacity" values="0.5;1;0.5" dur="1.8s" repeatCount="indefinite"/></circle>'
        f'<text x="10" y="0" font-family="{FONT}" font-size="11" font-weight="700" '
        f'letter-spacing="1.2" fill="{color}">{label}</text>'
    )


def render_stats(data, theme_name, out_path):
    t = THEME[theme_name]
    W, H = 560, 300
    metrics = [
        ("Public Repos", data["public_repos"], 40, t["accent1"]),
        ("Total Stars", data["total_stars"], 200, t["accent2"]),
        ("Contributions (yr)", data["contributions_year"], 2000, t["accent1"]),
        ("Followers", data["followers"], 150, t["accent2"]),
    ]
    tiles = []
    for i, (label, val, cap, color) in enumerate(metrics):
        col, row = i % 2, i // 2
        x = 30 + col * 260
        y = 74 + row * 108
        pct = 0.06 if val is None else max(0.06, min(1, val / cap))
        tiles.append(f'''
    <g transform="translate({x},{y})">
      <g transform="translate(38,38)">{ring(0, 0, 30, pct, color, t['track'])}</g>
      <text x="38" y="33" text-anchor="middle" font-family="{MONO}" font-size="15" font-weight="700" fill="{t['text_primary']}">{fmt(val)}</text>
      <text x="88" y="30" font-family="{FONT}" font-size="13" fill="{t['text_secondary']}">{label}</text>
      <text x="88" y="48" font-family="{MONO}" font-size="11" fill="{t['text_muted']}">{'real-time via GitHub API' if val is not None else 'awaiting first sync'}</text>
    </g>''')

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if data["synced"] else "pending first run"
    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub stats">
  <rect x="1" y="1" width="{W-2}" height="{H-2}" rx="22" fill="{t['bg1']}" stroke="{t['chip_stroke']}" stroke-width="1.3"/>
  <text x="28" y="38" font-family="{FONT}" font-size="18" font-weight="700" fill="{t['text_primary']}">GitHub Stats</text>
  <g transform="translate({W-118},32)">{synced_badge(t, data['synced'])}</g>
  <line x1="28" y1="52" x2="{W-28}" y2="52" stroke="{t['hairline']}" stroke-width="1"/>
  {''.join(tiles)}
  <line x1="28" y1="{H-34}" x2="{W-28}" y2="{H-34}" stroke="{t['hairline']}" stroke-width="1"/>
  <text x="28" y="{H-14}" font-family="{MONO}" font-size="10.5" fill="{t['text_muted']}">last synced: {synced_at}</text>
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)
    print("wrote", out_path)


def render_languages(data, theme_name, out_path):
    t = THEME[theme_name]
    W, H = 560, 300
    langs = data["languages"] or [("Awaiting sync", 100.0)]
    shades = [t["accent1"], t["accent2"], "#60A5FA", "#7DD3FC", "#93C5FD", "#BAE6FD"]
    rows = []
    y = 70
    for i, (name, pct) in enumerate(langs[:6]):
        color = shades[i % len(shades)]
        bar_w = 380 * (pct / 100 if data["languages"] else 0.0)
        rows.append(f'''
    <g transform="translate(28,{y})">
      <text font-family="{FONT}" font-size="13" fill="{t['text_primary']}">{name}</text>
      <text x="{W-56}" y="0" text-anchor="end" font-family="{MONO}" font-size="12" fill="{t['text_secondary']}">{pct if data['languages'] else 0}%</text>
      <rect x="0" y="10" width="380" height="8" rx="4" fill="{t['track']}"/>
      <rect x="0" y="10" width="0" height="8" rx="4" fill="{color}">
        <animate attributeName="width" from="0" to="{bar_w:.1f}" dur="1.2s" begin="{0.15*i:.2f}s" fill="freeze" calcMode="spline" keySplines="0.2 0 0.2 1"/>
      </rect>
    </g>''')
        y += 36

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Most used languages">
  <rect x="1" y="1" width="{W-2}" height="{H-2}" rx="22" fill="{t['bg1']}" stroke="{t['chip_stroke']}" stroke-width="1.3"/>
  <text x="28" y="38" font-family="{FONT}" font-size="18" font-weight="700" fill="{t['text_primary']}">Most Used Languages</text>
  <g transform="translate({W-118},32)">{synced_badge(t, data['synced'])}</g>
  <line x1="28" y1="52" x2="{W-28}" y2="52" stroke="{t['hairline']}" stroke-width="1"/>
  {''.join(rows)}
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)
    print("wrote", out_path)


TROPHY_DEFS = [
    ("Repo Builder", "public_repos", [(1, "bronze"), (10, "silver"), (25, "gold")], "repo"),
    ("Star Collector", "total_stars", [(1, "bronze"), (25, "silver"), (100, "gold")], "star"),
    ("Community", "followers", [(5, "bronze"), (25, "silver"), (100, "gold")], "people"),
    ("Contributor", "contributions_year", [(100, "bronze"), (500, "silver"), (1500, "gold")], "commit"),
    ("PR Champion", "total_prs", [(5, "bronze"), (25, "silver"), (75, "gold")], "pr"),
    ("Veteran", "years_on_github", [(1, "bronze"), (3, "silver"), (5, "gold")], "clock"),
]

TIER_COLOR = {"locked": None, "bronze": "#C08A4E", "silver": "#9AA5B1", "gold": "#F0B429"}


def tier_icon(kind, color):
    if kind == "repo":
        return f'<rect x="-7" y="-9" width="14" height="18" rx="2" fill="none" stroke="{color}" stroke-width="1.8"/><line x1="-4" y1="-4" x2="4" y2="-4" stroke="{color}" stroke-width="1.4"/><line x1="-4" y1="0" x2="4" y2="0" stroke="{color}" stroke-width="1.4"/>'
    if kind == "star":
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in _star_points())
        return f'<polygon points="{pts}" fill="{color}"/>'
    if kind == "people":
        return f'<circle cx="-5" cy="-6" r="4" fill="{color}"/><circle cx="5" cy="-6" r="4" fill="{color}" opacity="0.7"/><path d="M-11,10 C-11,2 -1,2 -1,10" fill="{color}"/><path d="M11,10 C11,3 3,3 3,9" fill="{color}" opacity="0.7"/>'
    if kind == "commit":
        return f'<circle cx="0" cy="0" r="5" fill="none" stroke="{color}" stroke-width="2"/><line x1="-14" y1="0" x2="-5" y2="0" stroke="{color}" stroke-width="2"/><line x1="5" y1="0" x2="14" y2="0" stroke="{color}" stroke-width="2"/>'
    if kind == "pr":
        return f'<circle cx="-7" cy="9" r="3" fill="none" stroke="{color}" stroke-width="1.8"/><circle cx="-7" cy="-9" r="3" fill="none" stroke="{color}" stroke-width="1.8"/><circle cx="7" cy="9" r="3" fill="none" stroke="{color}" stroke-width="1.8"/><path d="M-7,-6 V3 C-7,7 -1,7 4,7" fill="none" stroke="{color}" stroke-width="1.8"/>'
    if kind == "clock":
        return f'<circle cx="0" cy="0" r="11" fill="none" stroke="{color}" stroke-width="1.8"/><line x1="0" y1="0" x2="0" y2="-6" stroke="{color}" stroke-width="1.8"/><line x1="0" y1="0" x2="5" y2="2" stroke="{color}" stroke-width="1.8"/>'
    return ""


def _star_points():
    import math
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        r = 9 if i % 2 == 0 else 4
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def tier_for(value, thresholds):
    if value is None:
        return "locked"
    tier = "locked"
    for min_val, name in thresholds:
        if value >= min_val:
            tier = name
    return tier


def render_trophies(data, theme_name, out_path):
    t = THEME[theme_name]
    cols = 3
    tile = 176
    gap = 16
    pad = 10
    rows = -(-len(TROPHY_DEFS) // cols)
    W = cols * tile + (cols - 1) * gap + pad * 2
    H = rows * (tile - 10) + (rows - 1) * gap + pad * 2 + 46

    badges = []
    for i, (name, key, thresholds, icon_kind) in enumerate(TROPHY_DEFS):
        col, row = i % cols, i // cols
        x = pad + col * (tile + gap)
        y = pad + 46 + row * (tile - 10 + gap)
        tier = tier_for(data.get(key), thresholds)
        locked = tier == "locked"
        color = TIER_COLOR.get(tier) or t["text_muted"]
        ring_color = color if not locked else t["hairline"]
        badges.append(f'''
    <g transform="translate({x},{y})">
      <rect width="{tile}" height="{tile-10}" rx="18" fill="{t['chip_fill']}" stroke="{t['chip_stroke']}" stroke-width="1.2"/>
      <g transform="translate({tile/2},50)">
        <circle r="28" fill="none" stroke="{ring_color}" stroke-width="2" stroke-dasharray="{'4 5' if locked else 'none'}" opacity="{'0.5' if locked else '0.9'}">
          {'<animate attributeName="opacity" values="0.5;0.9;0.5" dur="2.6s" begin="'+str(i*0.3)+'s" repeatCount="indefinite"/>' if not locked else ''}
        </circle>
        <g opacity="{'0.35' if locked else '1'}">{tier_icon(icon_kind, color if not locked else t['text_muted'])}</g>
      </g>
      <text x="{tile/2}" y="{tile-32}" text-anchor="middle" font-family="{FONT}" font-size="13" font-weight="700" fill="{t['text_primary'] if not locked else t['text_muted']}">{name}</text>
      <text x="{tile/2}" y="{tile-16}" text-anchor="middle" font-family="{MONO}" font-size="10.5" fill="{color if not locked else t['text_muted']}">{tier.upper() if not locked else 'LOCKED'}</text>
    </g>''')

    svg = f'''<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Achievements">
  <rect x="1" y="1" width="{W-2}" height="{H-2}" rx="22" fill="{t['bg1']}" stroke="{t['chip_stroke']}" stroke-width="1.3"/>
  <text x="{pad+4}" y="30" font-family="{FONT}" font-size="18" font-weight="700" fill="{t['text_primary']}">Achievements</text>
  <g transform="translate({W-pad-104},10)">{synced_badge(t, data['synced'])}</g>
  {''.join(badges)}
</svg>'''
    with open(out_path, "w") as f:
        f.write(svg)
    print("wrote", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="DipakMandlik")
    ap.add_argument("--output-dir", default="assets")
    args = ap.parse_args()

    token = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    data = fetch_data(args.user, token)

    os.makedirs(args.output_dir, exist_ok=True)
    for theme in ("dark", "light"):
        render_stats(data, theme, os.path.join(args.output_dir, f"stats-{theme}.svg"))
        render_languages(data, theme, os.path.join(args.output_dir, f"languages-{theme}.svg"))
        render_trophies(data, theme, os.path.join(args.output_dir, f"trophies-{theme}.svg"))


if __name__ == "__main__":
    main()
