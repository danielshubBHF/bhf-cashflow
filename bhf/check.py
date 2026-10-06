"""Connection check before the first sync. Reads only; writes nothing anywhere.

  python -m bhf.check

Checks the keys are set (never prints them), NetSuite SuiteQL, the PDF RESTlet and Smartsheet.
"""
import os

import requests

from . import config

NEED = {"NetSuite": ["NS_CONSUMER_KEY", "NS_CONSUMER_SECRET", "NS_TOKEN_ID", "NS_TOKEN_SECRET"],
        "Smartsheet": ["SMARTSHEET_TOKEN"]}
HINTS = {401: "token, consumer key or account ID is wrong, or the role lacks 'Log in using Access Tokens'",
         403: "the role is missing a permission (REST Web Services, or view access to that record type)"}


def ok(msg):
    print(f"  OK    {msg}")


def bad(msg, e=None):
    code = getattr(getattr(e, "response", None), "status_code", None)
    detail = ""
    if code is not None:
        detail = f" (HTTP {code}: {HINTS.get(code, '')})"
        try:
            detail += "\n          " + e.response.text[:300].replace("\n", " ")
        except Exception:
            pass
    elif e is not None:
        detail = f" ({type(e).__name__}: {str(e)[:200]})"
    print(f"  FAIL  {msg}{detail}")
    return False


def main():
    print(f"NetSuite account {config.NS_ACCOUNT}")
    good = True
    for system, keys in NEED.items():
        missing = [k for k in keys if not os.getenv(k)]
        if missing:
            good = bad(f"{system} keys missing from .env: {', '.join(missing)}")
        else:
            ok(f"{system} keys present")
    if not good:
        return

    from .netsuite import NetSuite
    ns = NetSuite()
    po = None
    try:
        n = ns.query("SELECT COUNT(*) AS n FROM transaction WHERE type = 'PurchOrd'")[0]["n"]
        ok(f"NetSuite SuiteQL works ({n} POs visible to the role)")
    except Exception as e:
        bad("NetSuite SuiteQL", e)
        return
    try:
        from .smartsheet_db import Table
        projects = [p for p in Table(config.SHEETS["projects"]).load() if p.get("NetSuite Job ID")]
        ok(f"Smartsheet Projects sheet readable ({len(projects)} projects with a NetSuite Job ID)")
    except Exception as e:
        bad("Smartsheet Projects sheet", e)
        projects = []
    for p in projects:
        if p.get("Status") == "Closed":
            continue
        try:
            docs = ns.project_docs(int(float(p["NetSuite Job ID"])))
            kinds = {}
            for d in docs:
                kinds[d["type"]] = kinds.get(d["type"], 0) + 1
            ok(f"{p['Project']}: {len(docs)} NetSuite transactions {dict(sorted(kinds.items()))}")
            po = po or next((d for d in docs if d["type"] == "PurchOrd"), None)
        except Exception as e:
            bad(f"{p['Project']}: project transactions query", e)
    if po:
        try:
            got = ns.pdf(int(po["id"]))
            if got and got[1][:4] == b"%PDF":     # (name, bytes, source)
                ok(f"PDF RESTlet returned {got[0]} ({len(got[1]) // 1024} KB) for {po['tranid']}")
            else:
                bad(f"PDF RESTlet for {po['tranid']}: no PDF back (is the script deployed, Released, and open to the role?)")
        except requests.RequestException as e:
            bad("PDF RESTlet", e)
    print("Done. Nothing was written.")


if __name__ == "__main__":
    main()
