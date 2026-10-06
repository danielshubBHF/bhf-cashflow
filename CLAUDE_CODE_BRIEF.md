# Brief for Claude Code — BHF project cashflow

This is the BHF project cashflow app. It runs on FastAPI on Render, uses Smartsheet as the database, and treats NetSuite as the source of truth.

Read README.md and PROTOCOL.md first. Then run the tests:
```
python tests/test_model.py && python tests/test_sync_linking.py
DEMO=1 uvicorn bhf.web:app --reload
```
One team uses it. There is no manager view and no Microsoft Entra. Keep every test passing and add tests for each change. Ask me for API keys when you need them. Don't deploy until I've reviewed it locally.

## 1. Phase-1 mode (no Entra, no SharePoint from the app)
- Use the password login only (`DASHBOARD_PASSWORD`). Remove the Entra code path, or leave it inert if the vars are unset.
- For every new PO or bill, fetch the PDF through the NetSuite RESTlet and **attach it to that row in the Smartsheet Transactions sheet**. Use the Smartsheet attachments API: `POST /sheets/{id}/rows/{rowId}/attachments`, with the file as the body.
- Then add a `Filed` column (checkbox) to Transactions, and set it to false for new attachments.
  - **Filing into SharePoint is not done by the app.** A separate scheduled Claude task, once or twice a week, copies unfiled PDFs into `{SharePoint Folder}/1.0 Working Folder/0.7 Requisitions and PO's/{Supplier}/` and ticks Filed.
  - The dashboard should show a small "N PDFs not yet filed" note per project.

## 2. Forecast editor in the dashboard
Users must never need to open the Forecasts sheet.
- Per project, show an editable table of forecast lines: Item, Direction, Cost Type, Party, Amount, Expected Date, PO / Order #, Closed, Notes. Users can add, edit and close lines. Every save writes to Smartsheet.
- Customer milestones and supplier Payment Schedule rows are editable inline: Milestone, %, Amount, Expected Date, Confirmed.
- After a save, show the actual next to the forecast immediately. Re-run the model on cached data; there's no need to wait for a sync.
- Keep it plain and fast. Use server-rendered forms with a little JavaScript. No SPA framework.
- Tidy the Smartsheet Forecasts sheet too: group rows by project, put columns in a logical order, set sensible widths. It's the backup editor.

## 3. Flags under "Needs attention"
Each flag gets a stable ID, so it can be dismissed with a reason. Store dismissals in a small `Flag Log` sheet.
- **Wrong project.** A transaction's PO # or customer PO # appears on a forecast in a *different* project. Show the flag on both projects. This is the CCEP Moorabbin vs Richlands case.
- **Possible miscoding.** A supplier named on an open forecast here raises a PO on another live project within ±25% of this forecast's open amount, while this line still has no PO.
- **Unknown customer PO.** A customer invoice carries a customer PO # that isn't on this project's forecasts.
- **Stale PO.** A PO has been open with nothing billed **90 days** after its last expected milestone, or 90 days after the PO date if it has no schedule. The flag reads "Cancel in NetSuite or chase?".
- **Received not billed.** A PO is fully received but has no bill.
- **Standalone bill.** A bill on the project has no PO (created_from empty). Exclude expense claims and card charges.
- **Missing customer PO.** A customer invoice has an empty PO # (otherrefnum).
- **Unlinked expense.** An unassigned expense or card charge is older than 14 days.
- **Shared accounts.** Two live projects share the same Unearned or WIP account (see section 5), so revenue recognition can't be split automatically.

## 4. Restyle to match BHF's existing dashboards
I'll paste screenshots of the house style: the Site Audit Manager View and the Company Sales Dashboard.

- **Page:** light grey background with white cards.
- **Figure cards:** a coloured left-edge stripe, a small caps label, a large navy number, and a muted sub-line.
- **Branding:** BHF logo top-left, with the title and a subtitle next to it.
- **Data status:** an amber "DATA AS AT …" pill.
- **Navigation:** a segmented toggle, styled like the existing toggles, for "All live projects | BHF26001 | BHF26003 | BHF25015".
- **Colours:** navy #17294A, teal #1BA0C9, light teal #CDE9F3, grey #8A97A6, green for positive, red for negative.
- **Charts:** keep Chart.js, restyled to match.
- **Keep:** the cost "pipe" bars (paid → billed → on PO → forecast), restyled in the palette.

## 5. Revenue recognition: P&L view next to the cash view
BHF flattens project revenue and cost through dedicated balance-sheet accounts. Each pair is per customer, not per project:
- **Customer invoices** post to the project's **Unearned Income** account (21xx). Monthly journals release them into income accounts (40xx).
- **Bills** post to the project's **Project WIP** account (115x). Journals release them into cost of sales (50xx).

So, per project, from journals only:
- **Recognised revenue** = sum of Journal debits to the Unearned account, i.e. `SUM(tal.amount)` where type = 'Journal'.
- **Recognised cost** = −(sum of Journal amounts on the WIP account).
- **Unearned balance** = invoices posted − revenue recognised. A positive figure means invoiced ahead; a negative one means earned but not yet invoiced.
- **WIP balance** = bills posted − cost recognised.

Use the `transactionaccountingline` table with `posting = 'T'`. The query is validated:
```sql
SELECT t.type, ROUND(SUM(tal.amount),2) AS amount
FROM transactionaccountingline tal JOIN transaction t ON t.id = tal.transaction
WHERE tal.account = {acct_id} AND tal.posting = 'T'
GROUP BY t.type
```
The account internal IDs are already on the **Projects** sheet, in the columns `Unearned Acct ID` and `WIP Acct ID`.

| Project | Unearned (id) | WIP (id) |
|---|---|---|
| BHF26001 Stacked | 2163 (1149) | 1153 (1148) |
| BHF26003 Graphite Mars | 2165 (1157) | 1155 (1155) |
| BHF25015 CCEP Fiji | 2162 (1134) | 1152 (1135) |

**Acceptance figures, as at 05/10/26 from NetSuite:**

| Project | Revenue recognised | Invoiced into Unearned | Cost recognised | Bills into WIP |
|---|---|---|---|---|
| BHF26001 | 1,009,189.00 | 782,350.00 * | 673,125.00 | 187,283.40 |
| BHF26003 | 419,480.00 (100% of contract) | 398,506.00 | 224,947.01 | 120,792.50 (incl. stock 603.86) |
| BHF25015 | 1,409,154.50 | 1,496,550.07 | 1,040,961.20 | 787,962.34 (net of 12,511.66 credit) |

\* The two 26001 pre-flattening invoices ($50k) did not post to 2163.

**On the dashboard:**
- **Per project:** show a "P&L view" card next to the cash cards. It should show recognised revenue, recognised cost, recognised margin, and under/over-billed (Unearned balance).
- **Overview:** add the same as a total row.
- **Shared accounts:** the Graphite and CCEP account pairs are shared across those customers' projects. If two *live* projects share an account, show the P&L figures for the account pair once, marked "shared with BHFxxxxx", and raise the shared-accounts flag. Don't split them by guesswork.

## 6. Out of scope for now
Do not build these yet:
- Manager view
- Labour allocation and overheads
- Microsoft sign-in
