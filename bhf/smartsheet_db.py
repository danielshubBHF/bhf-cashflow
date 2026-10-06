"""Smartsheet as a simple table store: rows <-> dicts keyed by column title."""
import os
import requests

from . import config  # noqa: F401  (loads .env for local runs)

API = "https://api.smartsheet.com/2.0"


class Table:
    def __init__(self, sheet_id: int):
        self.sheet_id = sheet_id
        self.h = {"Authorization": f"Bearer {os.environ['SMARTSHEET_TOKEN']}"}
        self.col = {}

    def load(self) -> list[dict]:
        r = requests.get(f"{API}/sheets/{self.sheet_id}", headers=self.h, timeout=60)
        r.raise_for_status()
        s = r.json()
        self.col = {c["title"]: c["id"] for c in s["columns"]}
        title = {c["id"]: c["title"] for c in s["columns"]}
        out = []
        self.columns = s["columns"]
        for row in s.get("rows", []):
            d = {title[c["columnId"]]: c.get("value") for c in row["cells"]}
            d["_id"] = row["id"]
            if row.get("parentId"):
                d["_parent"] = row["parentId"]
            out.append(d)
        return out

    def ensure_column(self, title: str, type_: str = "TEXT_NUMBER", width: int = 80):
        """Add a column at the end if the sheet doesn't have it yet (call after load)."""
        if title in self.col:
            return
        r = requests.post(f"{API}/sheets/{self.sheet_id}/columns", headers=self.h, timeout=60,
                          json=[{"title": title, "type": type_, "index": len(self.col), "width": width}])
        r.raise_for_status()
        self.col[title] = r.json()["result"][0]["id"]

    def _cells(self, values: dict) -> list[dict]:
        return [{"columnId": self.col[k], "value": ("" if v is None else v)}
                for k, v in values.items() if k in self.col]

    def update(self, changes: list[tuple[int, dict]]):
        body = [{"id": rid, "cells": self._cells(v)} for rid, v in changes if v]
        for i in range(0, len(body), 200):
            requests.put(f"{API}/sheets/{self.sheet_id}/rows", headers=self.h,
                         json=body[i:i + 200], timeout=60).raise_for_status()

    def add(self, rows: list[dict], parent_id: int | None = None, fmt: str | None = None) -> list[dict]:
        """Add rows at the bottom of the sheet, or at the bottom of a group when parent_id is given."""
        out = []
        where = {"parentId": parent_id, "toBottom": True} if parent_id else {"toBottom": True}
        body = [{**where, "cells": self._cells(v), **({"format": fmt} if fmt else {})} for v in rows]
        for i in range(0, len(body), 200):
            r = requests.post(f"{API}/sheets/{self.sheet_id}/rows", headers=self.h,
                              json=body[i:i + 200], timeout=60)
            r.raise_for_status()
            out += r.json()["result"]
        return out

    def delete(self, row_ids: list[int]):
        if row_ids:
            requests.delete(f"{API}/sheets/{self.sheet_id}/rows", headers=self.h, timeout=60,
                            params={"ids": ",".join(map(str, row_ids)), "ignoreRowsNotFound": "true"}).raise_for_status()

    def move(self, row_ids: list[int], parent_id: int):
        """Put rows under a group row, in the order given (one call per row: rows needn't be adjacent)."""
        for r in row_ids:
            requests.put(f"{API}/sheets/{self.sheet_id}/rows", headers=self.h, timeout=60,
                         json=[{"id": r, "parentId": parent_id, "toBottom": True}]).raise_for_status()

    def move_top(self, row_ids: list[int]):
        """Put top-level rows (and their children) at the bottom of the sheet, in the order given."""
        for r in row_ids:
            requests.put(f"{API}/sheets/{self.sheet_id}/rows", headers=self.h, timeout=60,
                         json=[{"id": r, "toBottom": True}]).raise_for_status()

    def set_width(self, title: str, width: int):
        requests.put(f"{API}/sheets/{self.sheet_id}/columns/{self.col[title]}", headers=self.h, timeout=30,
                     json={"width": width}).raise_for_status()

    # ---- row attachments (PO / bill PDFs)
    def attach(self, row_id: int, name: str, data: bytes, content_type: str = "application/pdf") -> int:
        safe = name.replace('"', "")
        r = requests.post(f"{API}/sheets/{self.sheet_id}/rows/{row_id}/attachments", data=data, timeout=120,
                          headers={**self.h, "Content-Type": content_type,
                                   "Content-Disposition": f'attachment; filename="{safe}"'})
        r.raise_for_status()
        return r.json()["result"]["id"]

    def attachments(self, row_id: int) -> list[dict]:
        r = requests.get(f"{API}/sheets/{self.sheet_id}/rows/{row_id}/attachments", headers=self.h, timeout=30)
        r.raise_for_status()
        return r.json().get("data", [])

    def attachment_url(self, attachment_id: int) -> str:
        """Short-lived download URL (Smartsheet expires it after a couple of minutes)."""
        r = requests.get(f"{API}/sheets/{self.sheet_id}/attachments/{attachment_id}", headers=self.h, timeout=30)
        r.raise_for_status()
        return r.json()["url"]
