# BHF project cashflow — go-live runbook and project protocol

## Part 1 — Go-live (today)

Projects in scope: **BHF26001 Stacked Farm**, **BHF26003 Graphite Mars**, **BHF25015 CCEP Fiji**.
Every other project is near close-out and stays on its old Smartsheet cashflow.

Already done in Smartsheet (*0. Cashflow Database*):
- **Projects sheet:** rows for 26001, 26003 and 25015.
- **Forecasts sheet:** 13 lines for 26001, 13 for 26003 and 21 for 25015, built from the old cashflow sheets.
- **Payment Schedule:** 26001's 20 milestones are loaded. 26003 and 25015 load via `python -m bhf.seed` in step 4.

### Step 1 — Access (about 1.5 h, can run in parallel)
| Who | What | Gives you |
|---|---|---|
| NetSuite admin | Integration record and token-based auth (read-only role). Deploy `netsuite/bhf_render_pdf_restlet.js`. | `NS_CONSUMER_KEY/SECRET`, `NS_TOKEN_ID/SECRET` |
| You | Smartsheet API token | `SMARTSHEET_TOKEN` |
| You | A shared password for the team | `DASHBOARD_PASSWORD` |
| You | Anthropic API key (optional, for better reading of payment terms) | `ANTHROPIC_API_KEY` |

### Step 2 — Deploy (about 15 min)
1. Create a private GitHub repo and push the `bhf-cashflow` folder.
2. In Render, go to **New › Blueprint** and select the repo. This creates `bhf-cashflow` (web) and `bhf-cashflow-sync` (cron).
3. Fill in the environment group `bhf-cashflow`.

### Step 3 — Dry run (5 min)
Open a Render **Shell** on the cron job and run `python -m bhf.sync --dry-run`.
This writes nothing. Check the log:
- It connects to NetSuite. Any 401 error means token or role permissions.
- It lists every transaction it would add, and which forecast each one links to. Lines marked `[UNASSIGNED]` are ones you'll link by hand.

### Step 4 — First live run
1. In the shell, run `python -m bhf.seed` **first**. It loads the confirmed 26003 and 25015 payment schedules, so the sync doesn't re-read those POs' PDFs.
2. Then run `python -m bhf.sync`. You can also trigger the cron job.

### Step 5 — Acceptance checks
Open the dashboard and sign in with the team password. Each project tab must show these figures, which are NetSuite's totals as at 05/10/26. Small differences are allowed only for anything posted since then.

| Project | Transactions sheet rows | Invoiced (dashboard) | Costs billed (dashboard: billed) |
|---|---|---|---|
| BHF26001 | 17 (3 invoices, 10 POs, 4 bills) | $832,350.00 | $190,507.41 |
| BHF26003 | 21 (3 invoices, 9 POs, 8 bills, 1 stock issue) | $398,506.00 | $120,792.52 |
| BHF25015 | 67 (5 invoices, 24 POs, 37 bills, 1 bill credit) | $1,496,550.07 | $802,053.19 |

Also check:
- [ ] **26001, Bondalti main supply:** M1 is billed (PRO E AN66/21) and shows about +$13,343 over, which is the FX movement.
- [ ] **PDFs on 2–3 new POs:** each is attached to its Transactions row, opens from the dashboard, and the project shows "N PDFs not yet filed".
- [ ] **Filing task:** after its next run, those PDFs sit in `…/0.7 Requisitions and PO's/{Supplier}/`, *Filed* is ticked and the note clears.
- [ ] **Unassigned items:** each appears under **Needs attention**. Linking one moves it into its line after a refresh.
- [ ] **Sync timing:** the cron job ran at 07:00 / 13:00, and the "Synced" column on Transactions updates.

### Known items the first run will surface
- **25015, E&H PO006071:** coded to **BHF25002** in NetSuite but belongs to Fiji. Ask accounts to recode it, otherwise it won't appear on 25015.
- **26001, Bondalti PO005725** (design, €4k): open since February with no bill.
- **26001, Aquacorp PO006182:** the 20% payment was due 3 September, with no bill yet.
- **Employee expense claims** (Neil, Rory) usually have no PO. They appear as Unassigned. Link them to the project's Travel & expenses line.

### Step 6 — Retire the old sheets
Once the acceptance checks pass:
- Rename the old cashflow sheets for 26001, 26003 and 25015 to `… (archived - see dashboard)` and move them to an Archive folder.
- Delete *3. BHF26001 Stacked Farm Cashflow v2*.

---

## Part 2 — Protocol for every new project

### At contract award (PM, about 30 min, once)
1. **In NetSuite (accounts):** create the project/job. Note its **internal ID**: open the job; it's in the URL `…id=12345`.
2. **In SharePoint:** create the project folder from the standard template. It must contain `1.0 Working Folder/0.7 Requisitions and PO's`.
3. **Projects sheet:** add one row.
   - Project code, name, NetSuite job ID
   - Status **Live**
   - Contract value (ex GST)
   - BHF labour budget: PM weeks × rate
   - PM
   - SharePoint folder, as the path inside *6.0 Projects - Documents*, e.g. `2.0 Projects Contracted/BHF26009 Customer Thing`
   - **Unearned Acct ID** and **WIP Acct ID**: the customer's flattening accounts. Accounts create a new 21xx / 115x pair for a new customer. Claude can look up the IDs.
4. **Forecast lines** (dashboard project tab › Forecast lines; the Forecasts sheet is the backup): one row per budget line, taken from the costing.
   - **Incoming:** one row per customer PO, with the customer's PO number in *PO / Order #*. Put the milestone split in Notes.
   - **Outgoing:** one row per major supplier package (main system, variations, freight, install, commissioning support, travel). Fill **Party** if you know the supplier. Give each row an **Expected date**.
   - Keep each bucket meaningful. "Install" is one line, even if two contractors end up sharing it.
5. **Customer payments** (dashboard project tab): add the customer milestone rows (%, amount, expected date). They should add up to 100%. Supplier schedules come from the PO PDFs automatically.

The project appears on the dashboard at the next sync.

### Rules that keep it hands-free
These are agreed with accounts.
| Rule | Why |
|---|---|
| Every PO, bill and expense claim has the **Project** field set in NetSuite | It's how the sync finds the transaction. Uncoded items never appear. |
| Customer invoices carry the **customer PO number** in NetSuite's *PO #* field | Links invoices to the incoming milestones |
| POs state **payment terms** in the body or memo ("30% deposit, 35% FAT …") | They're read off the PDF into the schedule |
| Bills are raised **from the PO** (Bill button on the PO), not as standalone bills | Links bills to POs and milestones automatically |
| Cancelled POs are **closed** in NetSuite | Stops them showing as committed cost |
| Expense claims are coded to the project | They land on the project as Unassigned, ready to link |

### Weekly (PM, about 10 min per project)
1. Open the project tab and clear **Needs attention**:
   - **Unassigned:** link each one to a forecast line. Expense claims only show after 14 days unlinked.
   - **Terms need checking:** type the actual split in the item's box (`30/70`, or `30% deposit, 70% on delivery`) and press *Apply*. That rewrites the PO's milestones (billed ones keep their bill) and confirms them. If the split shown is already right, press *Terms are right*.
   - **Late forecast:** the PO hasn't been raised. Chase it, or move the expected date in Forecast lines.
   - **Overrun:** decide whether it's real, and adjust if so.
   - **Wrong project?** A PO or customer PO coded to this job that another job's forecast lists. Fix the coding in NetSuite, or the forecast.
   - **Miscoded?** A line here isn't ordered yet, but a PO from the same supplier, within 25% of the amount, sits unlinked on another job.
   - **Not billed:** the PO is received in NetSuite, with no bill for 30 days. Chase the supplier invoice.
   - **Stale PO:** still open with nothing billed 90 days after it was due. Cancel it in NetSuite, or chase.
   - **No PO on bill:** the bill wasn't raised from the PO, so the PO still shows open in NetSuite. Close the PO.
   - **Customer PO:** an invoice with no customer PO number, or one no contract line lists.
   - **Shared account:** two live jobs use the same Unearned Income or WIP account, so the P&L can't split them.
   - Anything that's fine as it is: **Acknowledge** it, with an optional reason. It leaves the list and the counts, and is recorded in the Flag Log sheet. The reason is a note only; it doesn't change anything. An overrun comes back if it grows by more than 10% + $500. *N acknowledged* lists them, with *Restore*.
2. Update **Expected Date** on any forecast or milestone that has slipped, so the cash curve stays honest.
3. Tick **Closed** on any forecast line where spending is finished.

### Changes during the job
- **Customer variation:** add an incoming forecast row with the variation PO number, plus its milestones.
- **New supplier package not in the costing:** add a forecast row. If the PO already exists, link it from Unassigned.
- **Forecast changes:** edit the Amount. The dashboard shows the original-forecast marker against the actuals either way.

### Close-out
1. Make sure there are no open POs left. Close any leftovers in NetSuite.
2. Tick **Closed** on all forecast lines.
3. Set the project Status to **Closed**. It drops off the dashboard and the sync stops pulling it. The data stays in Smartsheet.

### Who owns what
| Role | Owns |
|---|---|
| PM | Projects row, Forecasts, customer milestones, the weekly clear-down |
| Accounts | Project coding on every transaction, customer PO # on invoices, closing cancelled POs |
| System | Everything else: transactions, PDFs, supplier schedules, dashboard |
