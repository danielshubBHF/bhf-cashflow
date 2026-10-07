"""NetSuite: SuiteQL over REST with token-based auth, plus the PDF RESTlet."""
import base64
import os
from collections import defaultdict

import requests
from requests_oauthlib import OAuth1

from . import config

ORDER_TYPES = {"PurchOrd", "SalesOrd"}
TYPE_NAMES = {"PurchOrd": "PO", "SalesOrd": "Sales Order", "VendBill": "Bill", "VendCred": "Bill Credit",
              "CardChrg": "Card", "ExpRept": "Expense", "InvAdjst": "Stock issue",
              "CustInvc": "Invoice", "CustCred": "Credit Note"}
INCOMING = {"CustInvc", "CustCred", "SalesOrd"}
MONTH_NO = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class NetSuite:
    def __init__(self):
        acct = config.NS_ACCOUNT
        host = acct.lower().replace("_", "-")
        self.base = f"https://{host}.suitetalk.api.netsuite.com"
        self.restlet = f"https://{host}.restlets.api.netsuite.com/app/site/hosting/restlet.nl"
        self.auth = OAuth1(os.environ["NS_CONSUMER_KEY"], os.environ["NS_CONSUMER_SECRET"],
                           os.environ["NS_TOKEN_ID"], os.environ["NS_TOKEN_SECRET"],
                           signature_method="HMAC-SHA256", realm=acct)

    def query(self, q: str) -> list[dict]:
        out, offset = [], 0
        while True:
            r = requests.post(f"{self.base}/services/rest/query/v1/suiteql", auth=self.auth,
                              json={"q": q}, params={"limit": 1000, "offset": offset},
                              headers={"Prefer": "transient"}, timeout=90)
            r.raise_for_status()
            b = r.json()
            out += b.get("items", [])
            if not b.get("hasMore"):
                return out
            offset += 1000

    def project_docs(self, job_id: int) -> list[dict]:
        # Bills/POs carry the project on the header Project field; customer invoices on the
        # line customer:job. Both are checked (validated against live data 05/10/26).
        return self.query(f"""
            SELECT t.id, t.type, t.tranid, t.trandate, t.duedate, t.otherrefnum,
                   BUILTIN.DF(t.entity) AS party, BUILTIN.DF(t.status) AS status,
                   BUILTIN.DF(t.currency) AS ccy,
                   ROUND(SUM(tl.netamount * t.exchangerate), 2) AS aud,
                   ROUND(SUM(tl.foreignamount), 2) AS fx,
                   MAX(src.tranid) AS from_doc
            FROM transaction t
            JOIN transactionline tl ON tl.transaction = t.id AND tl.mainline = 'F' AND tl.taxline = 'F'
            LEFT JOIN transaction src ON src.id = tl.createdfrom
            WHERE t.type IN ('PurchOrd','SalesOrd','VendBill','VendCred','CardChrg','ExpRept',
                             'InvAdjst','CustInvc','CustCred')
              AND (t.custbody_project = {int(job_id)}
                   OR EXISTS (SELECT 1 FROM transactionline x WHERE x.transaction = t.id AND x.entity = {int(job_id)}))
            GROUP BY t.id, t.type, t.tranid, t.trandate, t.duedate, t.otherrefnum,
                     BUILTIN.DF(t.entity), BUILTIN.DF(t.status), BUILTIN.DF(t.currency)""")

    def paid_dates(self, ids: list[int]) -> dict[int, str]:
        """When each bill / invoice was paid. Most are paid by a payment; some by applying a supplier prepayment
        (VPrepApp) or customer deposit (DepAppl). For those the cash moved when the prepayment / deposit was made,
        so that date is used (the application date if the source can't be found)."""
        if not ids:
            return {}
        rows = self.query(f"""
            SELECT ntl.previousdoc AS doc, p.id AS pid, p.type AS ptype, p.trandate AS paid
            FROM NextTransactionLink ntl JOIN transaction p ON p.id = ntl.nextdoc
            WHERE ntl.previousdoc IN ({",".join(map(str, ids))})
              AND p.type IN ('VendPymt','CustPymt','VendCred','CustCred','VPrepApp','DepAppl')""")
        apps = sorted({int(r["pid"]) for r in rows if r["ptype"] in ("VPrepApp", "DepAppl")})
        source = {}
        if apps:
            for r in self.query(f"""
                SELECT ntl.nextdoc AS app, src.trandate AS paid
                FROM NextTransactionLink ntl JOIN transaction src ON src.id = ntl.previousdoc
                WHERE ntl.nextdoc IN ({",".join(map(str, apps))}) AND src.type IN ('VendPrep','CustDep')"""):
                source.setdefault(int(r["app"]), []).append(r["paid"])
        out = {}
        for r in rows:
            d = min(source[int(r["pid"])], key=dmy) if int(r["pid"]) in source else r["paid"]
            doc = int(r["doc"])
            if doc not in out or dmy(d) > dmy(out[doc]):          # fully paid on the latest payment
                out[doc] = d
        return out

    def part_paid(self, ids: list[int]) -> dict[int, tuple[float, str]]:
        """Open invoices / bills that are partly paid: {id: (share of the total paid, latest payment date)}."""
        if not ids:
            return {}
        rows = self.query(f"""
            SELECT t.id, t.foreigntotal AS total, t.foreignamountpaid AS paid,
                   (SELECT MAX(p.trandate) FROM NextTransactionLink ntl JOIN transaction p ON p.id = ntl.nextdoc
                    WHERE ntl.previousdoc = t.id) AS last
            FROM transaction t WHERE t.id IN ({",".join(map(str, ids))})""")
        out = {}
        for r in rows:
            total, paid = abs(float(r.get("total") or 0)), abs(float(r.get("paid") or 0))
            if total and 0.005 < paid < total - 0.005:
                out[int(r["id"])] = (paid / total, r.get("last"))
        return out

    # ---- FY budget tracking: "Systems Sales" (accounts 4071-4079) budget vs recognised revenue, by period
    SYSTEMS = "BUILTIN.DF({col}) LIKE '407%'"

    def systems_budget(self, fy: str) -> dict[str, float]:
        """{'Jul 2026': 559100.0, ...} for 'FY27' (NetSuite budget year 'FY 2027')."""
        rows = self.query(f"""
            SELECT BUILTIN.DF(bm.period) AS p, ROUND(SUM(bm.amount), 2) AS amt
            FROM budgets b JOIN budgetsmachine bm ON bm.budget = b.id
            WHERE BUILTIN.DF(b.year) = 'FY {2000 + int(fy[2:])}' AND {self.SYSTEMS.format(col='b.account')}
            GROUP BY BUILTIN.DF(bm.period)""")
        return {r["p"]: float(r["amt"] or 0) for r in rows}

    def systems_actual(self, periods: list[str]) -> dict[str, float]:
        """Revenue posted to the Systems Sales accounts (credits, so sign-flipped) by posting period."""
        names = ",".join("'" + p.replace("'", "''") + "'" for p in periods)
        rows = self.query(f"""
            SELECT BUILTIN.DF(t.postingperiod) AS p, ROUND(SUM(tal.amount), 2) AS amt
            FROM transactionaccountingline tal JOIN transaction t ON t.id = tal.transaction
            WHERE tal.posting = 'T' AND {self.SYSTEMS.format(col='tal.account')} AND BUILTIN.DF(t.postingperiod) IN ({names})
            GROUP BY BUILTIN.DF(t.postingperiod)""")
        return {r["p"]: -float(r["amt"] or 0) for r in rows}

    def recognised(self, projects: list[tuple], fy_start: str) -> dict[str, dict]:
        """Systems Sales revenue recognised per project, as it lands in the P&L (407x accounts):
          * monthly journals touching the project's Unearned Income account (their 407x credits; Unearned/WIP
            netting entries carry no 407x line, so they don't count), plus
          * customer invoices coded to the project that post straight to 407x (no Unearned account used).
        projects = [(code, netsuite_job_id, unearned_acct_id or None)]. Returns {code: {'all': x, 'fy': y}}."""
        out = {c: {"all": 0.0, "fy": 0.0} for c, _, _ in projects}
        fy = f"t.trandate >= TO_DATE('{fy_start}', 'YYYY-MM-DD')"
        acct_of = {str(int(float(u))): c for c, _, u in projects if u}
        if acct_of:
            for r in self.query(f"""
                SELECT u.account AS key, ROUND(SUM(-tal.amount), 2) AS all_, ROUND(SUM(CASE WHEN {fy} THEN -tal.amount ELSE 0 END), 2) AS fy
                FROM transactionaccountingline tal JOIN transaction t ON t.id = tal.transaction
                JOIN (SELECT DISTINCT x.transaction, x.account FROM transactionaccountingline x
                      WHERE x.account IN ({",".join(acct_of)}) AND x.posting = 'T') u ON u.transaction = t.id
                WHERE tal.posting = 'T' AND t.type = 'Journal' AND {self.SYSTEMS.format(col='tal.account')}
                GROUP BY u.account"""):
                c = acct_of[str(r["key"])]
                out[c]["all"] += float(r.get("all_") or 0)
                out[c]["fy"] += float(r.get("fy") or 0)
        job_of = {str(int(float(j))): c for c, j, _ in projects if j}
        if job_of:
            jobs = ",".join(job_of)
            for r in self.query(f"""
                SELECT tl.entity AS key, ROUND(SUM(-tal.amount), 2) AS all_,
                       ROUND(SUM(CASE WHEN {fy} THEN -tal.amount ELSE 0 END), 2) AS fy
                FROM transactionaccountingline tal
                JOIN transactionline tl ON tl.transaction = tal.transaction AND tl.id = tal.transactionline
                JOIN transaction t ON t.id = tal.transaction
                WHERE tal.posting = 'T' AND t.type IN ('CustInvc', 'CustCred') AND {self.SYSTEMS.format(col='tal.account')}
                  AND tl.entity IN ({jobs})
                GROUP BY tl.entity"""):
                c = job_of.get(str(r["key"]))
                if c:
                    out[c]["all"] += float(r.get("all_") or 0)
                    out[c]["fy"] += float(r.get("fy") or 0)
        return {c: {k: round(v, 2) for k, v in d.items()} for c, d in out.items()}

    def pl_actuals(self, projects: list[tuple]) -> dict[str, dict]:
        """What NetSuite's P&L actually shows per project per month (posting period -> 'YYYY-MM').
        projects = [(code, job_id, unearned_acct_id, wip_acct_id)].
          rev:  Systems Sales (407x) credited by journals touching the project's Unearned account, plus
                customer invoices whose project lines post straight to 407x
          cost: cost of sales (5xxx) debited by journals touching the project's WIP account, plus supplier
                bills coded to the project that post straight to 5xxx"""
        out = {c: {"rev": defaultdict(float), "cost": defaultdict(float)} for c, *_ in projects}

        def period(p):
            mon, yr = str(p).split()
            return f"{yr}-{MONTH_NO[mon]:02d}"

        def journals(side, accts: dict, like: str, sign: int):
            if not accts:
                return
            for r in self.query(f"""
                SELECT u.account AS key, BUILTIN.DF(t.postingperiod) AS p, ROUND(SUM(tal.amount), 2) AS amt
                FROM transactionaccountingline tal JOIN transaction t ON t.id = tal.transaction
                JOIN (SELECT DISTINCT x.transaction, x.account FROM transactionaccountingline x
                      WHERE x.account IN ({",".join(accts)}) AND x.posting = 'T') u ON u.transaction = t.id
                WHERE tal.posting = 'T' AND t.type = 'Journal' AND BUILTIN.DF(tal.account) LIKE '{like}'
                GROUP BY u.account, BUILTIN.DF(t.postingperiod)"""):
                out[accts[str(r["key"])]][side][period(r["p"])] += sign * float(r["amt"] or 0)

        journals("rev", {str(int(float(u))): c for c, _, u, _ in projects if u}, "407%", -1)
        journals("cost", {str(int(float(w))): c for c, _, _, w in projects if w}, "5%", 1)
        job_of = {str(int(float(j))): c for c, j, _, _ in projects if j}
        if job_of:
            jobs = ",".join(job_of)
            for r in self.query(f"""
                SELECT tl.entity AS key, BUILTIN.DF(t.postingperiod) AS p, ROUND(SUM(tal.amount), 2) AS amt
                FROM transactionaccountingline tal
                JOIN transactionline tl ON tl.transaction = tal.transaction AND tl.id = tal.transactionline
                JOIN transaction t ON t.id = tal.transaction
                WHERE tal.posting = 'T' AND t.type IN ('CustInvc', 'CustCred') AND BUILTIN.DF(tal.account) LIKE '407%'
                  AND tl.entity IN ({jobs})
                GROUP BY tl.entity, BUILTIN.DF(t.postingperiod)"""):
                if str(r["key"]) in job_of:
                    out[job_of[str(r["key"])]]["rev"][period(r["p"])] -= float(r["amt"] or 0)
            for r in self.query(f"""
                SELECT t.custbody_project AS key, BUILTIN.DF(t.postingperiod) AS p, ROUND(SUM(tal.amount), 2) AS amt
                FROM transactionaccountingline tal JOIN transaction t ON t.id = tal.transaction
                WHERE tal.posting = 'T' AND t.type IN ('VendBill', 'VendCred') AND BUILTIN.DF(tal.account) LIKE '5%'
                  AND t.custbody_project IN ({jobs})
                GROUP BY t.custbody_project, BUILTIN.DF(t.postingperiod)"""):
                if str(r["key"]) in job_of:
                    out[job_of[str(r["key"])]]["cost"][period(r["p"])] += float(r["amt"] or 0)
        return {c: {k: {m: round(a, 2) for m, a in d.items()} for k, d in v.items()} for c, v in out.items()}

    def stock_issue_pdf(self, txn_id: int):
        """NetSuite has no print template for inventory adjustments: build a one-page record instead."""
        from .pdfmake import record_pdf
        h = self.query(f"""SELECT t.tranid, t.trandate, t.memo, BUILTIN.DF(t.createdby) AS by_, BUILTIN.DF(t.account) AS acct
                           FROM transaction t WHERE t.id = {int(txn_id)}""")[0]
        lines = self.query(f"""SELECT BUILTIN.DF(tl.item) AS item, tl.quantity AS qty, BUILTIN.DF(tl.location) AS loc,
                                      tl.memo, ROUND(tl.netamount, 2) AS amt, BUILTIN.DF(tl.entity) AS ent
                               FROM transactionline tl WHERE tl.transaction = {int(txn_id)} AND tl.mainline = 'F'
                               ORDER BY tl.linesequencenumber""")
        total = -sum(float(r.get("amt") or 0) for r in lines)
        pdf = record_pdf(
            f"BHF stock issue {h.get('tranid')}",
            [("Date", h.get("trandate") or ""), ("Project", (lines[0].get("ent") if lines else "") or ""),
             ("Memo", h.get("memo") or ""), ("Raised by", h.get("by_") or ""), ("Adjustment account", h.get("acct") or ""),
             ("Value issued (AUD ex GST)", f"${total:,.2f}"), ("NetSuite internal ID", str(txn_id))],
            [("Item", 28), ("Qty", 7), ("Location", 24), ("Value AUD", 13), ("Memo", 22)],
            [[r.get("item"), f"{-float(r.get('qty') or 0):g}", r.get("loc"), f"${-float(r.get('amt') or 0):,.2f}", r.get("memo")]
             for r in lines],
            "Record generated from NetSuite by the BHF cashflow sync (NetSuite has no print layout for inventory adjustments).")
        return f"Stock_Issue_{h.get('tranid')}.pdf", pdf

    def pdf(self, txn_id: int, attached: bool = False):
        """(file name, bytes, source). attached=True asks for the PDF attached to the record (a bill's supplier
        invoice) and falls back to NetSuite's printout; source says which ("attached" / "netsuite")."""
        params = {"script": config.NS_RESTLET_SCRIPT, "deploy": config.NS_RESTLET_DEPLOY, "id": txn_id}
        if attached:
            params["attached"] = "1"
        # NetSuite picks the RESTlet's reply format from the request's Content-Type: ask for JSON.
        r = requests.get(self.restlet, auth=self.auth, timeout=120, headers={"Content-Type": "application/json"}, params=params)
        if r.status_code != 200:
            return None
        b = r.json()
        return b["name"], base64.b64decode(b["base64"]), b.get("source", "netsuite")


def amount(doc: dict) -> float:
    """AUD ex GST: positive for normal flow (cost out / revenue in), negative for credits."""
    a = float(doc.get("aud") or 0)
    t = doc["type"]
    if t in ("CustInvc", "SalesOrd", "InvAdjst"):
        a = -a
    if t in ("VendCred", "CustCred"):
        a = -abs(a)
    return round(a, 2)


def dmy(d) -> tuple:
    """NetSuite's d/m/yyyy as a sortable tuple."""
    try:
        day, mon, yr = (int(x) for x in str(d).split("/"))
        return yr, mon, day
    except ValueError:
        return 0, 0, 0


def is_paid(doc: dict) -> bool:
    return doc["type"] in ("VendCred", "CustCred", "InvAdjst", "CardChrg") or \
        "Paid In Full" in (doc.get("status") or "")
