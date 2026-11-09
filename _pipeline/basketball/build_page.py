"""Bake every saved game into the page, the way the Playoff Watch pages carry their scores.

    python -I build_page.py

Reads data/<league>/<season>/games/*.json and season.json and fills page/assist_network_template.html.
Writes the public team pages under --site-dir (basketball/index.html, womensbasketball/index.html), each
one self-contained file, plus page/assist_network.html (both teams, for local checks) and
page/metrics_by_game.csv. The staff and WCC pages come from build_staff.py and build_scout.py.
"""

import argparse
import csv
import json
import math
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
TEMPLATE = os.path.join(HERE, "page", "assist_network_template.html")
OUT = os.path.join(HERE, "page", "assist_network.html")
TEMPLATE_3D = os.path.join(HERE, "page", "assist_network_3d_template.html")
OUT_3D = os.path.join(HERE, "page", "assist_network_3d.html")
MARKER = "/*__DATA__*/null"
LABELS = {"mens": "Men's Basketball", "womens": "Women's Basketball"}

# One page per team for the public site in aochoa4/USF-Athletics, each at the top of the site:
# basketball/ is the men's page and womensbasketball/ the women's (Alex, 2026-11-06: "make the women
# their own page womensbasketball not under basektball"). Neither links to the other (Alex, 2026-11-06).
SITE_DIR = os.path.join(HERE, "site")
SITE_PAGES = {
    "mens": (os.path.join("basketball", "index.html"), "USF Men's Assist Network"),
    "womens": (os.path.join("womensbasketball", "index.html"), "USF Women's Assist Network"),
}
# the same document shell the soccer pages use on GitHub Pages
SHELL_HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
              '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
              '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);'
              'padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}'
              'img{max-width:100%}[hidden]{display:none!important}</style>\n</head>\n<body>\n')
SHELL_TAIL = "\n</body>\n</html>\n"


def as_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def hits(edges, ids, tol=1e-12, max_iter=10000):
    """Hub and authority scores (Kleinberg HITS) on the weighted passer -> scorer graph.
    Each vector is scaled to unit length within the network it is computed on; this is
    igraph::hits_scores(scale = FALSE), checked by check_metrics.R."""
    idx = {pid: i for i, pid in enumerate(ids)}
    hub = [1.0] * len(ids)
    for _ in range(max_iter):
        auth = [0.0] * len(ids)
        for (p, s), w in edges.items():
            auth[idx[s]] += w * hub[idx[p]]
        norm = math.sqrt(sum(x * x for x in auth)) or 1.0
        auth = [x / norm for x in auth]
        new = [0.0] * len(ids)
        for (p, s), w in edges.items():
            new[idx[p]] += w * auth[idx[s]]
        norm = math.sqrt(sum(x * x for x in new)) or 1.0
        new = [x / norm for x in new]
        done = max(abs(a - b) for a, b in zip(hub, new)) < tol
        hub = new
        if done:
            break
    auth = [0.0] * len(ids)  # authority that goes with the final hub vector
    for (p, s), w in edges.items():
        auth[idx[s]] += w * hub[idx[p]]
    norm = math.sqrt(sum(x * x for x in auth)) or 1.0
    return {pid: hub[i] for pid, i in idx.items()}, {pid: auth[i] / norm for pid, i in idx.items()}


def team_entropy(edges, ids):
    """Shannon entropy of each player's share of the team's assists, over every player in the
    map, divided by log2 of that count: 1 when everyone makes the same number of assists."""
    out = {pid: 0 for pid in ids}
    for (p, _), w in edges.items():
        out[p] += w
    total = sum(out.values())
    if len(ids) < 2 or not total:
        return 0.0
    h = -sum(v / total * math.log2(v / total) for v in out.values() if v)
    return h / math.log2(len(ids))


def add_metrics(games):
    """Scores on the cumulative network through each game, stored on that game as g['m']."""
    edges, rows = {}, []
    for k, g in enumerate(games, 1):
        for p, s, _, _ in g["a"]:
            edges[(p, s)] = edges.get((p, s), 0) + 1
        ids = sorted({x for pair in edges for x in pair})
        if not ids:
            g["m"] = {"h": {}, "au": {}, "te": None}
            continue
        hub, auth = hits(edges, ids)
        te = team_entropy(edges, ids)
        # unrounded, so the page rounds once, at display (rounding here first moved 19 shown scores by .001)
        g["m"] = {"h": {i: hub[i] for i in ids}, "au": {i: auth[i] for i in ids}, "te": te}
        for i in ids:
            rows.append((k, g["date"], i, hub[i], auth[i], te))
    return rows


def shot_code(a):
    """The shot an assist set up: 3 a three, r at the rim (layup, dunk, tip), j a two-point jumper.
    ESPN's shot type says LayUpShot, DunkShot or JumpShot; the play text backs it up."""
    if a.get("points") == 3:
        return "3"
    kind, text = (a.get("shot") or "").lower(), (a.get("text") or "").lower()
    return "r" if ("layup" in kind or "dunk" in kind or re.search(r"layup|dunk|tip|alley", text)) else "j"


def season_block(league, season, data_dir=DATA_DIR, shots=False):
    """One season for the page. shots=True adds g['sh'], one shot code per assisted basket (the WCC pages)."""
    sdir = os.path.join(data_dir, league, season)
    gdir = os.path.join(sdir, "games")
    games = []
    for fn in os.listdir(gdir):
        if fn.endswith(".json"):
            with open(os.path.join(gdir, fn), encoding="utf-8") as f:
                games.append(json.load(f))
    games.sort(key=lambda g: (g["date"], g["event_id"]))

    players, out = {}, []
    for g in games:  # later games overwrite names, so the newest spelling wins
        for pid, r in g["roster"].items():
            players[pid] = {"n": r["name"], "s": r["short"], "j": r.get("jersey"), "p": r.get("position")}
        c = g["check"]
        out.append({
            "id": g["event_id"],
            "date": g["date"],
            "opp": g.get("opponent_short") or g["opponent"],
            "site": g["site"],
            "conf": g["conference_game"],
            "post": g["season_type"] == 3,
            "us": g["usf_score"],
            "them": g["opp_score"],
            "res": g["result"],
            "box": c["box_assists"],
            # box-score assists by player, for the per-player check
            "pa": {pid: as_int(r.get("assists")) for pid, r in g["roster"].items()
                   if as_int(r.get("assists"))},
            # [passer, scorer, points, period] for every assisted basket, in play order
            "a": [[a["passer"], a["scorer"], a["points"], a["period"]] for a in g["assists"]],
        })
        if shots:
            out[-1]["sh"] = "".join(shot_code(a) for a in g["assists"])
            # made baskets with no assist, [scorer, shot code]; game files fetched before 2026-11-09 have none
            if "unassisted" in g:
                out[-1]["u"] = [[u["scorer"], shot_code(u)] for u in g["unassisted"]]

    upcoming = []
    spath = os.path.join(sdir, "season.json")
    if os.path.exists(spath):
        with open(spath, encoding="utf-8") as f:
            upcoming = [{"date": u["date"], "opp": u.get("opponent_short") or u["opponent"], "site": u["site"]}
                        for u in json.load(f).get("upcoming", [])]

    metric_rows = add_metrics(out)
    y = int(season)
    return {"label": f"{y - 1}-{str(y)[2:]}", "players": players, "games": out, "upcoming": upcoming}, metric_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-dir", default=SITE_DIR, help="the site root: basketball/index.html and womensbasketball/index.html go here")
    ap.add_argument("--combined", default=OUT, help="the two-team page, or 'none' to skip it")
    ap.add_argument("--metrics", default=os.path.join(HERE, "page", "metrics_by_game.csv"), help="score table, or 'none'")
    ap.add_argument("--built", default=time.strftime("%Y-%m-%d"), help="date shown as 'built' on the pages")
    args = ap.parse_args()

    data = {"built": args.built, "teams": {}}
    metrics = []
    for league in ("mens", "womens"):
        ldir = os.path.join(DATA_DIR, league)
        if not os.path.isdir(ldir):
            continue
        seasons = {}
        for s in sorted(os.listdir(ldir), reverse=True):
            if os.path.isdir(os.path.join(ldir, s, "games")):
                seasons[s], rows = season_block(league, s)
                names = seasons[s]["players"]
                metrics += [(league, s, k, d, pid, names.get(pid, {}).get("n"), h, a, te) for k, d, pid, h, a, te in rows]
        data["teams"][league] = {"label": LABELS[league], "seasons": seasons}

    # every score on the page, unrounded, for check_metrics.R and for review
    if args.metrics != "none":
        with open(args.metrics, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["league", "season", "game_no", "date", "player_id", "player", "hub", "authority", "team_entropy"])
            w.writerows(metrics)

    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    if page.count(MARKER) != 1:
        raise SystemExit(f"template must contain {MARKER} exactly once")
    fill = lambda d: page.replace(MARKER, json.dumps(d, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    if args.combined != "none":
        with open(args.combined, "w", encoding="utf-8") as f:
            f.write(fill(data))
        # the private 3D preview takes the same data block (local builds only; the site repo has no 3D template)
        if os.path.exists(TEMPLATE_3D):
            with open(TEMPLATE_3D, encoding="utf-8") as f:
                page3d = f.read()
            if page3d.count(MARKER) != 1:
                raise SystemExit(f"3D template must contain {MARKER} exactly once")
            with open(OUT_3D, "w", encoding="utf-8") as f:
                f.write(page3d.replace(MARKER, json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
            print(f"wrote {OUT_3D}")

    for league, (rel, title) in SITE_PAGES.items():
        if league not in data["teams"]:
            continue
        one = fill({"built": data["built"], "teams": {league: data["teams"][league]}})
        if one.count("<title>USF Assist Network</title>") != 1:
            raise SystemExit("template title changed; update SITE_PAGES titling")
        one = one.replace("<title>USF Assist Network</title>", f"<title>{title}</title>")
        path = os.path.join(args.site_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(SHELL_HEAD + one + SHELL_TAIL)
        print(f"wrote {path}")

    for league, t in data["teams"].items():
        for s, b in t["seasons"].items():
            n_a = sum(len(g["a"]) for g in b["games"])
            n_box = sum(g["box"] or 0 for g in b["games"])
            print(f"{league} {b['label']}: {len(b['games'])} games, {n_a} mapped, box score {n_box}")
    if args.combined != "none":
        print(f"wrote {args.combined} ({os.path.getsize(args.combined) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
