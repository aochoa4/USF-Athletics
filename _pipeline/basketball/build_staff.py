"""Password-locked staff versions of the public USF pages, for USF's coaching staffs (Alex, 2026-11-09).

    MENS_STAFF_PASSWORD=... WOMENS_STAFF_PASSWORD=... python -I build_staff.py [--site-dir DIR] [--built YYYY-MM-DD]

Each staff page is the public page plus the scouting note, the connections-by-shot table and the unassisted
baskets column, for USF itself. The whole page is encrypted (AES-GCM, with the key drawn from the password by
PBKDF2-SHA256) into a small lock page at basketball/staff/ and womensbasketball/staff/, so the files can sit in
the public repo while only someone with the password can read them. A team with no password set is skipped.
The passwords live only in the repo's GitHub Actions secrets, which Alex sets; nothing here writes them to a file.
Needs the cryptography package; the workflow installs it.
"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORDS = {"mens": "MENS_STAFF_PASSWORD", "womens": "WOMENS_STAFF_PASSWORD"}
TEAMS = {"mens": "USF Men's Basketball", "womens": "USF Women's Basketball"}
ITERATIONS = 600_000  # PBKDF2-SHA256 rounds (OWASP's figure): every password guess costs this many

spec = importlib.util.spec_from_file_location("build_page", os.path.join(HERE, "build_page.py"))
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

# the lock page: the site's colors and fonts, a password box, and the encrypted staff page
LOCK = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<title>__TEAM__ staff page</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;600&display=swap">
<style>
:root { --bg: #f4f6f2; --panel: #ffffff; --ink: #15211b; --muted: #5d6a62; --line: #d7ded8; --green: #0b5d41; --gold: #94690a; color-scheme: light; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0f1613; --panel: #16201b; --ink: #e5ece7; --muted: #9aa89f; --line: #2a3832; --green: #5cc896; --gold: #e8b84a; color-scheme: dark; }
}
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 420px; margin: 14vh auto 0; padding: 0 16px; display: flex; flex-direction: column; gap: 12px; }
.team { margin: 0; color: var(--green); font-weight: 600; }
h1 { margin: 0; font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-size: 2.6rem; line-height: 1; font-weight: 700; }
form { display: flex; flex-direction: column; gap: 8px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }
label { font-weight: 600; }
input { font: inherit; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--ink); }
button { font: inherit; font-weight: 600; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--green); background: var(--green); color: var(--bg); cursor: pointer; }
button:disabled { opacity: 0.6; cursor: default; }
.err { margin: 0; color: var(--gold); font-weight: 600; }
</style>
</head>
<body>
<main>
  <p class="team">__TEAM__</p>
  <h1>Staff page</h1>
  <form id="f">
    <label for="pw">Password</label>
    <input id="pw" type="password" autocomplete="current-password" required autofocus>
    <button id="go" type="submit">Open</button>
    <p class="err" id="err" hidden>That password didn't work.</p>
  </form>
</main>
<script>
var ENC = /*__ENC__*/null;
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var bytes = function (s) { return Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); }); };
  var fail = function (text) { $("err").textContent = text; $("err").hidden = false; $("go").disabled = false; $("go").textContent = "Open"; $("pw").select(); };
  $("f").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var subtle = window.crypto && window.crypto.subtle;
    if (!subtle) { fail("This browser can't open the page."); return; }
    $("err").hidden = true; $("go").disabled = true; $("go").textContent = "Opening";
    subtle.importKey("raw", new TextEncoder().encode($("pw").value), "PBKDF2", false, ["deriveKey"])
      .then(function (base) {
        return subtle.deriveKey({ name: "PBKDF2", salt: bytes(ENC.salt), iterations: ENC.iter, hash: "SHA-256" },
                                base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
      })
      .then(function (key) { return subtle.decrypt({ name: "AES-GCM", iv: bytes(ENC.iv) }, key, bytes(ENC.ct)); })
      .then(function (plain) {   // the staff page replaces the lock page
        var html = new TextDecoder().decode(plain);
        document.open(); document.write(html); document.close();
      })
      .catch(function () { fail("That password didn't work."); });
  });
})();
</script>
</body>
</html>
"""


def encrypt(html, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, dklen=32)
    b64 = lambda x: base64.b64encode(x).decode("ascii")
    return {"salt": b64(salt), "iv": b64(iv), "iter": ITERATIONS, "ct": b64(AESGCM(key).encrypt(iv, html.encode("utf-8"), None))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-dir", default=bp.SITE_DIR, help="the site root; staff pages go under basketball/staff/ and womensbasketball/staff/")
    ap.add_argument("--built", default=time.strftime("%Y-%m-%d"), help="date shown as 'built' on the pages")
    args = ap.parse_args()

    with open(bp.TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    for old in ("<title>USF Assist Network</title>", bp.MARKER):
        if template.count(old) != 1:
            raise SystemExit(f"template changed; {old!r} must appear exactly once")

    for league, (rel, title) in bp.SITE_PAGES.items():
        password = os.environ.get(PASSWORDS[league])
        if not password:
            print(f"{league}: {PASSWORDS[league]} is not set, so no staff page")
            continue
        ldir = os.path.join(bp.DATA_DIR, league)
        seasons = {s: bp.season_block(league, s, shots=True)[0] for s in sorted(os.listdir(ldir), reverse=True)
                   if os.path.isdir(os.path.join(ldir, s, "games"))}
        data = {"built": args.built, "scout": True, "teams": {league: {"label": bp.LABELS[league], "seasons": seasons}}}
        page = template.replace(bp.MARKER, json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
        page = page.replace("<title>USF Assist Network</title>", f"<title>{title} (staff)</title>")
        lock = LOCK.replace("__TEAM__", TEAMS[league]).replace("/*__ENC__*/null", json.dumps(encrypt(bp.SHELL_HEAD + page + bp.SHELL_TAIL, password)))
        path = os.path.join(args.site_dir, os.path.dirname(rel), "staff", "index.html")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(lock)
        print(f"wrote {path} (locked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
