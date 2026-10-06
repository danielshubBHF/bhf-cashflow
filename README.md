# BHF project cashflow

The only cashflow job left for people is forecasting. NetSuite provides every actual figure, the sync worker attaches every PO and bill PDF, and the dashboard does all the maths.

```
 NetSuite ──(twice daily)──► sync worker ──► Smartsheet "0. Cashflow Database" ◄── you: forecast editor on the dashboard
     │                          │                 │      │
     └── PO / bill PDFs ────────┘  attached to    │      └──► dashboard (Render): Overview + a tab per project
                                   Transactions rows
                                                  │
           scheduled Claude task (1–2× a week) ───┴──► SharePoint
           {project}/1.0 Working Folder/0.7 Requisitions and PO's/{Supplier}/   (ticks Filed)
```

## The four sheets (Smartsheet › 3. BHF Systems › 2. Contracted › 0. Cashflow Database)

| Sheet | Who edits it | Purpose |
|---|---|---|
| **Projects** | You, once per job | Project code, NetSuite job ID, contract value, BHF labour budget, PM, SharePoint folder, status |
| **Forecasts** | You, from the dashboard (the only regular input) | One row per budget line. For example, "Site install $200k", "Freight", "Bondalti supply". Rows are grouped under a header row per project; the sheet is only a backup editor |
| **Transactions (auto - do not edit)** | Sync worker | One row per NetSuite PO, bill, invoice, credit, expense, card charge or stock issue. POs and bills carry their PDF as a row attachment; *Filed* is ticked once it's in SharePoint |
| **Payment Schedule** | Sync worker, plus you from the dashboard | Milestones per PO or customer order. Supplier milestones are read from the PO PDF; you add customer milestones, fix dates and confirm terms |

### Forecasts are budget buckets
A forecast line isn't trying to guess the exact PO. When a PO arrives it rolls up into the bucket, and **NetSuite's numbers replace your forecast**:

- **PO for half the forecast:** the other half shows as *forecast, no PO*. If nothing more will be spent, tick **Closed** on the forecast and the saving shows straight away.
- **PO for double the forecast:** the line shows the overrun in red.
- **Several POs on one line** (variations, multiple contractors): they all roll up.

**How a PO finds its bucket.** The sync tries each step in turn:
1. **By PO number.** If the forecast's *PO / Order #* contains it.
2. **By supplier.** If exactly one open forecast for that project has a matching *Party*. The sync then writes the PO number back onto the forecast, so all later bills follow automatically.
3. **Otherwise, Unassigned.** It shows under **Needs attention** on the project tab, and you pick the forecast line from a dropdown. That's one click.

Customer lines work the same way. Put the customer's PO number (e.g. `PO-0007`) on the forecast; invoices carry it in NetSuite's PO # field.

### Payment terms
For each new PO, the sync pulls the PDF from NetSuite and reads the payment terms ("30% advance, 35% FAT, 25% before shipment, 10% commissioning"). It turns them into milestones in **Payment Schedule**.

- **With an `ANTHROPIC_API_KEY`:** Claude reads the terms, which copes with messy supplier wording.
- **Without one:** a percentage parser is used.

Anything uncertain is created **unconfirmed** and flagged on the dashboard. Edit the milestones on the dashboard, then tick *Confirmed*. Milestones on one PO that don't add up to 100% are flagged too. As bills arrive they are matched to the milestones in order.

Foreign-currency POs keep the remaining milestones at the PO exchange rate. Billed milestones use NetSuite's actual AUD, so FX gains and losses show where they happened.

### PDFs and filing
For each new PO, supplier bill and customer invoice, the sync fetches the PDF through the NetSuite RESTlet and **attaches it to that row in Transactions**. The *PDF* column holds the file name and *Filed* is left unticked. If the RESTlet or the upload fails, *PDF* stays blank and the next sync tries again. A **PDF** link next to the transaction on the dashboard opens the attachment.

The app never writes to SharePoint. A separate scheduled Claude task (see [FILING_TASK.md](FILING_TASK.md)) runs once or twice a week, copies each unfiled PDF to
```
6.0 Projects - Documents / {SharePoint Folder from Projects} / 1.0 Working Folder / 0.7 Requisitions and PO's / {Supplier} /
```
and ticks *Filed*. Until then the dashboard shows "N PDFs not yet filed" on the project.

## The dashboard

**Nobody needs to open the Forecasts sheet.** Each project tab has:
- **Forecast lines:** an editable table (Item, Direction, Cost Type, Party, Amount, Expected Date, PO / Order #, Closed, Notes), with the NetSuite actuals (*On order*, *Billed*, *vs forecast*) right beside each forecast. Add a line in the bottom row. Each Save writes the changed cells to Smartsheet, and the figures update straight away; there's no need to wait for a sync. Renaming a line keeps its linked NetSuite documents.
- **Payment schedules:** editable inline (Milestone, %, Amount, Expected Date, Confirmed). Customer milestones are under *Customer payments*; supplier ones are inside each cost line. Typing a % fills in the amount, and the other way round. Billed milestones are locked because NetSuite has the actual.

The dashboard uses the BHF house dashboard style (full-width grey board, white cards, KPI cards with a coloured spine) and is built to fit one screen on a desktop; on laptops it gets denser, and below 1100px wide it stacks into one scrolling column.

- **All live projects:** the cashflow statement for all live jobs (received − paid = cash now; + still to receive − still to pay = final position), five clickable figures (contract, invoiced / received, expected cost, margin → net after BHF labour, lowest cash ahead), a projects table (in, out, cash now, final position, margins, cost stages, attention, PDFs), the cash chart and **Financial years**: cash basis (received, paid, net) and document basis (invoiced, costs billed, gross) per FY, including completed projects; part years say where the loaded history starts. Every figure opens a drawer with what it is, the formula, a per-project breakdown and the basis.
- **Project status:** *Live* and *Closing* projects are live. *Complete* projects drop out of the tabs, live figures, table, chart and attention counts but still count in financial years (and the chart's FY buttons). *Closed* projects are left out entirely.
- **One tab per project:** the cashflow statement first, then *Incoming* and *Outgoing* side by side at full height, laid out like the old Smartsheet cashflow sheets: one header row per forecast line (supplier, PO numbers, expected total, a pipe of its four money stages, status) with its payments underneath (milestone, bill, open balance or unplaced forecast, date, amount, Paid / Billed / On order / Forecast, overdue). Filter each list by All, To come, Done or Overdue (remembered). Click a line or payment for its side panel: the stages, every payment, how the numbers add up, NetSuite documents with PDFs and notes; *Edit line* switches the panel to the line's form and payment schedule. The cash chart, cost pipes and Needs attention sit below. The page scrolls; nothing is squeezed to fit one screen.
- **Forecast & payments** (one button, or click any row): *Money in — customer* and *Money out — costs*. Each line shows four stages that don't overlap and add up to the expected total: paid (received) | billed, not paid | on order, not billed | forecast, not ordered, plus a pipe bar and vs forecast. Costs are grouped *Ordered*, *Still to place*, *Closed* and *Not linked to a line*, with subtotals. Rows are read-only; click one to open its edit form, its payment schedule (milestones, % and $ kept in step), how the numbers add up, and its NetSuite documents with PDFs. *+ Add customer line* / *+ Add cost line* open a blank form. After a save the page reloads with the same lines open.
- **Cash by month:** bars for received / paid (solid) and still expected (light); anything still to come that was due before today is counted in the current month and hatched amber as *overdue*, so past months only ever show actuals. The solid line is the actual cash position up to today, the dashed line the projection. Months read “Jan 27”; the tooltip reconciles each month (opening + in − out = closing). A toggle filters by Australian financial year (FY27 = Jul 2026 to Jun 2027, part years labelled); each year opens at the real position carried from before it.

Gross margin excludes BHF labour, which is taken from the *Internal Labour* column on Projects. Net margin includes it.

## One-off setup

**1. NetSuite token and RESTlet** (admin, about 45 min)
1. **Setup › Integration › New.** Enable *Token-Based Authentication*. Copy the consumer key and consumer secret.
2. **Create a read-only role.** Give it view access to Purchase Order, Bill, Bill Credit, Invoice, Credit Memo, Sales Order, Expense Report, Credit Card, Inventory Adjustment and Find Transaction. Also give it *REST Web Services*, *Log in using Access Tokens* and *SuiteAnalytics Workbook*.
3. **Create an access token** for that role and user. Copy the token ID and token secret.
4. **Deploy the PDF RESTlet.**
   - Upload `netsuite/bhf_render_pdf_restlet.js` to the File Cabinet.
   - Create the script with ID `_bhf_render_pdf`.
   - Deploy it with ID `_bhf_render_pdf`, Released, for the role above.

**2. Smartsheet API token.** Go to **Account › Personal Settings › API Access**. A service account is best.

**3. Render** (about 15 min)
1. Push this folder to a private GitHub repository.
2. In Render, go to **New › Blueprint** and select the repository. This creates the web service, the cron job and the environment group.
3. Fill in the secrets in the `bhf-cashflow` environment group, including `DASHBOARD_PASSWORD`, the team's shared sign-in password. Without it the dashboard stays locked.
4. Open the cron job and run it once manually. Its log lists every link it made and every transaction it added.

Optionally, add `ANTHROPIC_API_KEY` for payment-terms reading.

The first sync adds the *Filed* checkbox column to Transactions if it isn't there.

**4. SharePoint filing task.** Set up the scheduled Claude task in [FILING_TASK.md](FILING_TASK.md).

## Adding a project
1. **Projects sheet:** add a row with code, NetSuite job ID, contract value, labour budget, PM, SharePoint folder and status *Live*.
2. **Forecast lines** (on the project's dashboard tab, after the next sync shows it): add the budget lines. Fill *Party* where you know the supplier, and the *PO / Order #* for the customer PO.
3. **Wait for the next sync.** That's it.

## Local run / tests
```bash
pip install -r requirements.txt
DEMO=1 uvicorn bhf.web:app --reload        # dashboard on real 26001 data, no keys or password needed
python tests/test_model.py && python tests/test_sync_linking.py && python tests/test_phase1.py && python tests/test_editor.py
python -m bhf.tidy_forecasts               # needs SMARTSHEET_TOKEN; shows how it would group the Forecasts sheet (--apply to do it)
python -m bhf.sync --dry-run               # needs the env vars; prints what it would change
```
