#!/bin/bash
# Manage the Cloudflare Access allowlist for bulletin-admin.fpccorinth.org.
#   cloudflare-access.sh list
#   cloudflare-access.sh add    someone@example.com [more@example.com ...]
#   cloudflare-access.sh remove someone@example.com [more@example.com ...]
# Needs ~/.config/cloudflare/env with CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID (mode 600).
# Access is only the outer gate: the person also needs a row on the builder's Users screen to get a role.
set -euo pipefail
set -a; . ~/.config/cloudflare/env; set +a
APP=9ecb43fd-772a-476a-b2ee-2556e18e9ca2        # Access application "FPC Bulletin Builder"
POLICY=23b51a9b-d325-497e-94a1-b6cb4c7f9f7e     # its allow policy "Bulletin builder users"
export URL="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/access/apps/$APP/policies/$POLICY"
exec python3 - "$@" <<'PY'
import json, os, sys, urllib.request
url, token = os.environ["URL"], os.environ["CLOUDFLARE_API_TOKEN"]
def call(method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req))
    if not d["success"]: sys.exit(f"Cloudflare error: {d['errors']}")
    return d["result"]
cmd, emails = (sys.argv[1] if len(sys.argv) > 1 else "list"), [e.strip().lower() for e in sys.argv[2:]]
p = call()
current = [i["email"]["email"] for i in p["include"] if "email" in i]
if cmd == "list":
    print("\n".join(current)); sys.exit()
if cmd not in ("add", "remove") or not emails or any("@" not in e for e in emails):
    sys.exit(__doc__ or "usage: cloudflare-access.sh list | add EMAIL... | remove EMAIL...")
wanted = sorted(set(current) | set(emails)) if cmd == "add" else [e for e in current if e not in emails]
if not wanted: sys.exit("refusing to empty the allowlist")
body = {k: p[k] for k in ("name", "decision", "precedence", "exclude", "require") if k in p}
body["include"] = [{"email": {"email": e}} for e in wanted]
call("PUT", body)
print("allowlist now:", ", ".join(wanted))
PY
