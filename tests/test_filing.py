"""SharePoint filing script (bhf.file_pdfs) against a throwaway copy of the synced folder structure."""
import os, sys, pathlib, shutil, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bhf import file_pdfs as fp

W, POS, OFFER = fp.WORK, fp.POS, fp.OFFER


def tree(files: list[str]) -> pathlib.Path:
    root = pathlib.Path(tempfile.mkdtemp())
    for f in files:
        p = root / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 x") if not f.endswith("/") else p.mkdir(exist_ok=True)
    return root


PRJ = "BHF26005 Sanector UF4"
BASE = [f"{PRJ}/{W}/{POS}/Advanced UV/Purchase Order_PO005885_1.pdf",
        f"{PRJ}/{W}/{POS}/Advanced UV/BHF Technologies Pty Ltd ORD33232098.pdf",
        f"{PRJ}/{W}/{POS}/Joe - Plumblux/Plumblux Pty Ltd Invoice #114.pdf",
        f"{PRJ}/{W}/{POS}/Mifelec/Purchase Order_PO005969_1.pdf",
        f"{PRJ}/{W}/{POS}/General Marking - Tags/",
        f"{PRJ}/{W}/{OFFER}/Invoice_INV021566_1.pdf",
        f"BHF26002 Ecolab/{W}/{POS}/Mifelec/Purchase Order_PO009999_1.pdf"]


def T(type_, doc, party, po="", pdf="x.pdf"):
    return {"Project": "BHF26005", "Type": type_, "Doc #": doc, "Party": party, "PO / Order #": po, "PDF": pdf}


def plan(t, root):
    others = {"BHF26005": fp.files_under(root / PRJ / W / POS), "BHF26002": fp.files_under(root / "BHF26002 Ecolab" / W / POS)}
    return fp.plan_one(t, root / PRJ, "BHF26005", others)


def test_po_already_filed_is_not_uploaded_again():
    root = tree(BASE)
    try:
        p = plan(T("PO", "PO005885", "Advance UV Systems Pty Ltd", "PO005885"), root)
        assert p.outcome == "ALREADY_FILED" and "PO005885" in p.matches[0]
    finally:
        shutil.rmtree(root)


def test_new_po_goes_to_matching_supplier_folder_with_custom_name():
    root = tree(BASE)
    try:
        p = plan(T("PO", "PO006059", "Plumblux", "PO006059"), root)
        assert p.outcome == "UPLOAD" and p.folder.name == "Joe - Plumblux"
        assert p.name == "PO006059 - Plumblux - Purchase Order.pdf"
    finally:
        shutil.rmtree(root)


def test_bill_follows_its_po_and_proforma_does_not_count():
    root = tree(BASE)
    try:
        p = plan(T("Bill", "33232098", "Advance UV Systems Pty Ltd", "PO005885"), root)
        assert p.outcome == "UPLOAD" and p.folder.name == "Advanced UV"          # ORD... is an acknowledgement
        assert p.name == "Bill 33232098 - Advance UV Systems.pdf" and "proforma" in p.note
        p = plan(T("Bill", "114", "Plumblux", ""), root)
        assert p.outcome == "ALREADY_FILED"                                       # "#114" whole token
        p = plan(T("Bill", "14", "Plumblux", ""), root)
        assert p.outcome == "UPLOAD"                                              # "14" is not "114"
    finally:
        shutil.rmtree(root)


def test_bill_numbered_like_a_sales_order_still_counts_as_filed():
    root = tree(BASE + [f"{PRJ}/{W}/{POS}/Hotco/Hotco invoice SO432371.pdf", f"{PRJ}/{W}/{POS}/AVFI/AVFI proforma 361514.pdf"])
    try:
        assert plan(T("Bill", "SO432371", "Hotco"), root).outcome == "ALREADY_FILED"
        assert plan(T("Bill", "361514", "AVFI Pty Ltd"), root).outcome == "UPLOAD"      # a real proforma still doesn't count
    finally:
        shutil.rmtree(root)


def test_unnumbered_bill_matches_invoice_filed_under_its_po():
    root = tree(BASE + [f"{PRJ}/{W}/{POS}/Welkin/Invoices/Welkin 30% PO005896.pdf", f"{PRJ}/{W}/{POS}/Welkin/Purchase Order_PO005917_1.pdf"])
    try:
        assert plan(T("Bill", "Bill", "Welkin", "PO005896", "Bill_346603.pdf"), root).outcome == "ALREADY_FILED"
        assert plan(T("Bill", "Bill", "Welkin", "PO005917", "Bill_346605.pdf"), root).outcome == "UPLOAD"   # only the PO itself
    finally:
        shutil.rmtree(root)


def test_unknown_supplier_gets_new_folder_and_expenses_their_own():
    root = tree(BASE)
    try:
        p = plan(T("Bill", "16767", "Team Express Freight Management Pty Ltd"), root)
        assert p.outcome == "UPLOAD_NEW_FOLDER" and p.folder.name == "Team Express Freight Management"
        p = plan(T("Bill", "EXP - Feb", "Neil Morrow"), root)
        assert p.folder.name == "Expense Claims" and p.name == "Expense EXP - Feb - Neil Morrow.pdf"
    finally:
        shutil.rmtree(root)


def test_customer_invoices_go_to_top_invoices_folder_without_duplicates():
    root = tree(BASE)
    try:
        assert plan(T("Invoice", "INV021566", "BHF26005 Sanector UF4"), root).outcome == "ALREADY_FILED"
        p = plan(T("Invoice", "INV022158", "BHF26005 Sanector UF4"), root)
        assert p.outcome == "UPLOAD_NEW_FOLDER" and p.folder.name == "Invoices"
        assert p.name == "INV022158 - BHF26005 - Customer Invoice.pdf"
    finally:
        shutil.rmtree(root)


def test_po_filed_under_another_project_is_reported():
    root = tree(BASE)
    try:
        p = plan(T("PO", "PO009999", "Mifelec Pty Ltd", "PO009999"), root)
        assert "possible wrong project" in p.note and "BHF26002" in p.note
    finally:
        shutil.rmtree(root)


def test_stock_issue_goes_to_bhf_internal_folder():
    root = tree(BASE + [f"{PRJ}/{W}/{POS}/BHF Internal/Stock adj 1379.pdf"])
    try:
        p = plan(T("Stock issue", "1394", "BHF26005 Sanector UF4"), root)
        assert p.outcome == "UPLOAD" and p.folder.name == "BHF Internal" and p.name == "Stock issue 1394 - BHF Internal.pdf"
        assert plan(T("Stock issue", "1379", "BHF26005 Sanector UF4"), root).outcome == "ALREADY_FILED"
    finally:
        shutil.rmtree(root)
    root = tree(BASE[:1])                                         # no BHF Internal folder yet: make one
    try:
        p = plan(T("Stock issue", "1394", "x"), root)
        assert p.outcome == "UPLOAD_NEW_FOLDER" and p.folder.name == "BHF Internal"
    finally:
        shutil.rmtree(root)


def test_generated_stock_issue_pdf_is_readable():
    from bhf.pdfmake import record_pdf
    from bhf.terms import pdf_text
    data = record_pdf("BHF stock issue 1394", [("Project", "BHF26005 (Sanector) UF4")], [("Item", 20), ("Qty", 6)], [["H-VCF", "1"]])
    text = pdf_text(data)
    assert data[:4] == b"%PDF" and "BHF stock issue 1394" in text and "BHF26005 (Sanector) UF4" in text and "H-VCF" in text


def test_replace_own_only_overwrites_files_this_script_made():
    root = tree(BASE + [f"{PRJ}/{W}/{POS}/Mifelec/Bill I-4304 - Mifelec.pdf"])
    try:
        others = {"BHF26005": fp.files_under(root / PRJ / W / POS)}
        p = fp.plan_one(T("Bill", "I-4304", "Mifelec Pty Ltd", "PO005969"), root / PRJ, "BHF26005", others, replace_own=True)
        assert p.outcome == "REPLACE"
        p = fp.plan_one(T("Bill", "114", "Plumblux"), root / PRJ, "BHF26005", others, replace_own=True)
        assert p.outcome == "ALREADY_FILED"                                    # filed by hand: never overwritten
        p = fp.plan_one(T("Bill", "I-4304", "Mifelec Pty Ltd", "PO005969"), root / PRJ, "BHF26005", others)
        assert p.outcome == "ALREADY_FILED"
    finally:
        shutil.rmtree(root)


def test_missing_project_folder_needs_a_person():
    root = tree([])
    try:
        assert plan(T("PO", "PO1", "X"), root).outcome == "NEEDS_YOU"
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f(); print("PASS", name)
