# SharePoint filing task (scheduled Claude task)

The cashflow app attaches each PO, supplier bill, expense claim and customer invoice PDF to its row in **Transactions (auto - do not edit)** and leaves *Filed* unticked. It never writes to SharePoint. This task does the filing.

**Schedule:** once or twice a week (for example Tuesday and Friday at 08:00).
**Connectors it needs:** Smartsheet (read attachments, update rows) and SharePoint (list folders, upload files).
**Library:** *6.0 Projects - Documents* (site 6.0Projects, "Shared Documents"). Each project's *SharePoint Folder* on the Projects sheet is the path inside it, e.g. `2.0 Projects Contracted/BHF26001 Stacked Farm DAF UF RO 8`. Completed projects may have moved under `2.0 Projects Contracted/BHF Projects Completed/`.

Decisions agreed with Daniel on 06/10/26 are marked ✔.

## Where each document goes

| Transactions *Type* | Folder (under `{SharePoint Folder}/1.0 Working Folder/`) | File name ✔ |
|---|---|---|
| PO | `0.7 Requisitions and PO's/{Supplier}/` | `{PO#} - {Supplier} - Purchase Order.pdf` |
| Bill (supplier invoice) ✔ same folder as its PO | `0.7 Requisitions and PO's/{Supplier}/` | `Bill {Doc #} - {Supplier}.pdf` |
| Expense claim ✔ file them: *Type* "Expense", or a Bill whose *Doc #* starts with `EXP` (e.g. Neil Morrow's) | `0.7 Requisitions and PO's/Expense Claims/` | `Expense {Doc #} - {Employee}.pdf` |
| Stock issue ✔ (inventory adjustment from BHF's warehouse; the PDF is a record the sync builds from NetSuite) | `0.7 Requisitions and PO's/BHF Internal/` (create it if missing) | `Stock issue {Doc #} - BHF Internal.pdf` |
| Invoice (customer invoice) ✔ | `0.2 Final Offer Contracts and PO/Invoices/` (the top-level *Invoices* folder directly under 0.2; create it if missing — don't use deeper subfolders like `Invoices/Engineering`) | `{INV#} - {Project code} - Customer Invoice.pdf` |

In file names, replace `\ / : * ? " < > | # %` with `-`. If *Doc #* is just "Bill" (no supplier invoice number in NetSuite), use the number from the attachment's file name (NetSuite internal ID) instead.

**Supplier folder.** Clean the *Party* name: drop anything in brackets; drop company suffixes (Pty, Ltd, Lty, Limited, Co, Company, Inc, LLC, GmbH, SA, Lda, Australia, Aust); drop commas and full stops. Compare names on letters and digits only, ignoring case, against the folders directly under `0.7 Requisitions and PO's`:
1. If the PO for this bill is already filed in a folder, use that folder (e.g. Endress + Hauser → `E&H`, Piping and Automation Systems → `Paas`, Enkrott → `Bondalti Water`).
2. Otherwise reuse a folder whose name equals the cleaned name, starts with it, is the start of it, or contains it (`Fusion Plastics Pty Ltd` → `Fusion Plastics - site nozzle`; `Plumblux` → `Joe - Plumblux`).
3. Otherwise a folder starting with the supplier's first word, if that word is longer than 3 letters.
4. Otherwise create a folder with the cleaned supplier name.
If two folders match equally, don't guess: leave it unfiled and list it.

## Don't duplicate — check before every upload

Collect every file name in the whole `0.7 Requisitions and PO's` tree (all subfolders) and, for invoices, the whole `0.2 Final Offer Contracts and PO` tree. Compare on uppercase letters and digits only.

- **PO:** already filed if any file name contains the PO number (e.g. `PO005995`), or its digits as a separate token together with "PO".
- **Bill / expense:** already filed if a file **in that supplier folder** contains the bill number as a whole token (or the normalised number when it is 6+ characters). Short numbers (under 5 characters) count only when the match is clearly the same document; otherwise list it as "probable".
- **Invoice:** already filed if a file anywhere in the 0.2 tree contains the invoice number (e.g. `INV021372`).
- ✔ **A proforma, order acknowledgement, sales order or quote is not the bill.** If the only match looks like one of those (`proforma`, `PI_`, `ORD`, `SO`, `quote` in the name), still file the tax invoice from NetSuite.

When it's already there: don't upload; tick *Filed*; note the existing file in the summary.

## Then

- Download the row's attachment (the name is in the *PDF* column), upload it with the new name, and tick *Filed* only once the upload succeeded.
- Leave the row unticked, and list it, if the project has no *SharePoint Folder*, the `1.0 Working Folder` / `0.7` / `0.2` folder can't be found, or the folder match is ambiguous.
- ✔ **Wrong project?** If a PO or bill number is found filed in a *different* project's folders, don't move anything: list it as "possible wrong project in NetSuite" so accounts can check the coding.
- Don't change any other cell in Transactions, and never delete, move or rename existing SharePoint files.

Finish with a short summary per project: filed (uploaded), already there, folders created, left unfiled (with reasons), possible wrong-project items.

## First run
The read-only dry run on 06/10/26 (TEST sheets, 160 PDFs): 95 already filed, 56 to upload, 3 needing a new folder, 6 for decision (now decided above). The first live run should cover one project, checked by Daniel in SharePoint, before the rest.

## Checking it worked
On the dashboard each project shows **"N PDFs not yet filed"**; after a successful run that number drops to the items listed as left unfiled.
