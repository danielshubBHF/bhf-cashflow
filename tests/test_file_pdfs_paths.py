"""File names are shortened so the full path stays inside Windows' 260-character limit."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from pathlib import Path
from bhf import file_pdfs as fp

DEEP = Path(r"C:\Users\DanielShub\BHF Technologies\6.0 Projects - Documents\2.0 Projects Contracted"
            r"\BHF26001 Stacked Farm DAF UF RO 8\1.0 Working Folder\0.7 Requisitions and PO's\Enkrott_Bondalti Water")


def test_long_po_name_is_shortened_in_the_supplier_part_only():
    name = "PO006366 - Enkrott - Gestao e Tratamento de Aguas S A - Purchase Order.pdf"
    assert len(str(DEEP / name)) > 260
    got = fp.fit(DEEP, name)
    assert len(str(DEEP / got)) <= fp.MAX_PATH
    assert got.startswith("PO006366 - Enkrott") and got.endswith(" - Purchase Order.pdf")


def test_long_bill_name_keeps_its_number():
    name = "Bill IV-2026-000123 - Enkrott - Gestao e Tratamento de Aguas S A.pdf"
    got = fp.fit(DEEP, name)
    assert len(str(DEEP / got)) <= fp.MAX_PATH and got.startswith("Bill IV-2026-000123 - ") and got.endswith(".pdf")


def test_short_names_are_untouched():
    assert fp.fit(Path(r"C:\x"), "PO1 - Welkin - Purchase Order.pdf") == "PO1 - Welkin - Purchase Order.pdf"
