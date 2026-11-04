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


def test_po_goes_next_to_its_requisition(tmp_path):
    sup = tmp_path / "Enkrott_Bondalti Water"
    for sub, pr in [("Variation/VR04_piping", "BHF26001_Bondalti-Water_PR_281026ds(0).docx"),
                    ("Variation/VR05_Safety valve", "BHF26001_Bondalti-Water_PR_041126ds(0).docx"),
                    ("Variation/Archived", "old_PR_041126ds.docx")]:
        (sup / sub).mkdir(parents=True)
        (sup / sub / pr).write_bytes(b"x")
    assert fp.requisition_folder(sup, "2026-11-04").name == "VR05_Safety valve"     # same day; Archived ignored
    assert fp.requisition_folder(sup, "2026-10-30").name == "VR04_piping"           # PR two days before
    assert fp.requisition_folder(sup, "2026-12-20") is None                          # no PR that fits


def test_own_file_recognised_whatever_length_it_was_cut_to():
    full = "PO006366 - Enkrott - Gestao e Tratamento de Aguas S A - Purchase Order.pdf"
    assert fp.made_here("PO006366 - Enkrott - Gestao e Tratamento - Purchase Order.pdf", full)
    assert not fp.made_here("Purchase Order_PO006366_1790916049289.pdf", full)       # NetSuite's own download, not ours


def test_very_deep_folder_names_the_file_by_po_number_only():
    deep = DEEP / "Variation" / "VR05_Sil 2 Safety valve"
    got = fp.fit(deep, "PO006388 - Enkrott - Gestao e Tratamento de Aguas S A - Purchase Order.pdf")
    assert len(str(deep / got)) <= fp.MAX_PATH and got.startswith("PO006388")
    assert fp.made_here(got, "PO006388 - Enkrott - Gestao e Tratamento de Aguas S A - Purchase Order.pdf")
