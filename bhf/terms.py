"""Read payment terms off a PO / quote PDF and turn them into milestones.

Uses Claude when ANTHROPIC_API_KEY is set (best for messy supplier wording),
otherwise a percentage parser. Anything uncertain comes back unconfirmed.
"""
import io
import json
import os
import re

import requests

from . import config

TRIGGERS = ["advance", "deposit", "order", "acknowledg", "drawing", "design", "fat", "factory",
            "before shipment", "shipment", "dispatch", "delivery", "arrival", "install",
            "commission", "sat", "handover", "completion", "retention", "warranty"]


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)[:20000]
    except Exception:
        return ""


def _claude(text: str) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or not text.strip():
        return None
    prompt = (
        "From this purchase order / quotation text, extract the PAYMENT TERMS as milestones.\n"
        'Reply with JSON only: {"milestones":[{"label":str,"percent":number}],'
        '"confident":bool,"terms_text":str}. Percents must sum to 100. If there is a single '
        'payment (e.g. "30 days EOM"), return one milestone of 100. If unclear, confident=false.\n\n'
        + text[:15000])
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": config.CLAUDE_MODEL, "max_tokens": 800,
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        return None
    raw = "".join(b.get("text", "") for b in r.json().get("content", []))
    try:
        return json.loads(re.sub(r"```(json)?", "", raw).strip())
    except ValueError:
        return None


def _regex(text: str) -> dict:
    found = []
    for m in re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%(?=([^%\n]{0,90}))", text):
        pct = float(m.group(1))
        ctx = re.split(r"[,;]|\d{1,3}\s*%", m.group(2))[0].strip()
        if 0 < pct <= 100 and any(t in ctx.lower() for t in TRIGGERS):
            found.append({"label": f"{m.group(1)}% {ctx}"[:80], "percent": pct})
    total = sum(f["percent"] for f in found)
    if found and abs(total - 100) < 0.5:
        return {"milestones": found, "confident": True, "terms_text": "; ".join(f["label"] for f in found)}
    return {"milestones": [{"label": "Single payment", "percent": 100}], "confident": False,
            "terms_text": "Terms not found on PDF - please confirm"}


def extract(pdf: bytes | None) -> dict:
    text = pdf_text(pdf) if pdf else ""
    res = _claude(text) or _regex(text)
    ms = [m for m in res.get("milestones", []) if float(m.get("percent") or 0) > 0]
    if not ms or abs(sum(float(m["percent"]) for m in ms) - 100) > 0.5:
        return {"milestones": [{"label": "Single payment", "percent": 100}], "confident": False,
                "terms_text": res.get("terms_text") or "Terms unclear - please confirm"}
    res["milestones"] = ms
    return res
