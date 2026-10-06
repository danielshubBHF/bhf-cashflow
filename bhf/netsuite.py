"""NetSuite: SuiteQL over REST with token-based auth, plus the PDF RESTlet."""
import base64
import os

import requests
from requests_oauthlib import OAuth1

from . import config

ORDER_TYPES = {"PurchOrd", "SalesOrd"}
TYPE_NAMES = {"PurchOrd": "PO", "SalesOrd": "Sales Order", "VendBill": "Bill", "VendCred": "Bill Credit",
              "CardChrg": "Card", "ExpRept": "Expense", "InvAdjst": "Stock issue",
              "CustInvc": "Invoice", "CustCred": "Credit Note"}
INCOMING = {"CustInvc", "CustCred", "SalesOrd"}


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
        if not ids:
            return {}
        rows = self.query(f"""
            SELECT ntl.previousdoc AS doc, MAX(p.trandate) AS paid
            FROM NextTransactionLink ntl JOIN transaction p ON p.id = ntl.nextdoc
            WHERE ntl.previousdoc IN ({",".join(map(str, ids))})
              AND p.type IN ('VendPymt','CustPymt','VendCred','CustCred')
            GROUP BY ntl.previousdoc""")
        return {int(r["doc"]): r["paid"] for r in rows}

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


def is_paid(doc: dict) -> bool:
    return doc["type"] in ("VendCred", "CustCred", "InvAdjst", "CardChrg") or \
        "Paid In Full" in (doc.get("status") or "")
