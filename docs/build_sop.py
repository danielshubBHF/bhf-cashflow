# -*- coding: utf-8 -*-
"""Builds docs/BHF Project Cashflow SOP.docx in the BHF document house style (the bulletin template).
Rebuild after a process change:  python docs/build_sop.py"""
import os, sys
sys.path.insert(0, os.path.expanduser(r"~\.claude\skills\bhf-technical-bulletin\scripts"))
from bhf_bulletin import Bulletin, cell

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BHF Project Cashflow SOP.docx")
URL = "https://bhf-cashflow.onrender.com"

b = Bulletin()
_bullet = b.bullet
b.bullet = lambda text, lead=None, **k: _bullet(" " + text if lead else text, lead=(lead.rstrip(",") + ":") if lead else None, **k)
b.title("Project Cashflow: Standard Operating Procedure")
b.subtitle("How BHF systems projects are set up, run, tracked and closed in the cashflow app")
b.meta("BHF Technologies  ·  Systems projects  ·  Dashboard " + URL)
b.meta("Rev 0   ·   8 October 2026   ·   Owner: Daniel Shub   ·   Internal procedure", small=True)

# 1
b.heading("1.  Purpose")
b.para("Every systems project's money in, money out and cash position is kept up to date without retyping. "
       "NetSuite is the source of truth for anything that has happened (POs, bills, invoices, payments). "
       "The PM owns the forecast: what is still expected, when, and on what terms. The app joins the two, "
       "files the paperwork, and flags what needs a person.")

# 2
b.heading("2.  How it fits together")
b.table(["Part", "What it does", "When"], [
    ["NetSuite", "Source of truth for POs, bills, invoices, payments and the P&L.", "As accounts work"],
    ["Sync (Render)", "Pulls every document coded to a project into Smartsheet, links it to a forecast line, reads payment "
                      "terms from PO PDFs, attaches PDFs, adds variation lines, updates each project's cashflow sheet.",
     "07:00 and 13:00 weekdays"],
    ["Smartsheet: 0. Cashflow Database", "The app's database (3. BHF Systems / 2. Contracted / 0. Cashflow Database): Projects, "
                                         "Forecasts, Transactions, Payment Schedule, Flag Log, Pipeline.", "Always"],
    ["Project cashflow sheet", "\"3. {code} {name} Cashflow\" in each job folder. Readable cashflow; forecast lines can be edited "
                               "or added there.", "Rebuilt each sync"],
    ["Dashboard", URL + " (password sign-in). Overview, FY budget, pipeline, each project, completed jobs.", "Live; Refresh button"],
    ["SharePoint filing", "Files new PO, bill, invoice, expense and stock PDFs into the project's SharePoint folders.",
     "07:30 daily (Daniel's PC)"],
], colw=[3.6, 10.6, 3.6])
b.note("Filing runs on a PC because SharePoint is reached through OneDrive sync. Task: \"BHF Cashflow PDF filing\" in Windows "
       "Task Scheduler; log: filing.log in the app folder.")

# 3
b.heading("3.  Who does what")
b.table(["Role", "Responsible for"], [
    ["Project manager", "Sets the project up (section 4); keeps forecast lines, expected dates and customer milestones right; "
                        "clears Needs attention weekly; sets the P&L timeline."],
    ["Accounts", "Codes every document to the project in NetSuite; follows the NetSuite rules (section 5); closes POs and tags "
                 "documents when the dashboard asks; runs month-end flattening journals."],
    ["Sales / BD", "Keeps the Enquiries Pipeline Mastersheet current (value, likelihood, timing)."],
    ["Daniel (app owner)", "Render, NetSuite access, the filing PC, budget and pipeline settings, changes to the app."],
], colw=[3.6, 14.2])

# 4
b.heading("4.  Starting a project (the only way in)")
b.para("A project starts when an enquiry is won. It is set up on the app within two working days of the customer PO.")
b.bullet("accounts create the job (customer:job) in NetSuite and give the PM the job's internal ID (in the job's URL, id=12345). "
         "If the customer is new, accounts also create its Unearned Income (21xx) and Project WIP (115x) accounts.",
         lead="NetSuite job,")
b.bullet("copy the \"BHF Project Contracted Template\" folder in Smartsheet (3. BHF Systems / 2. Contracted) and name it "
         "\"{code} {project name}\". Create the SharePoint project folder from the standard template "
         "(it must contain 1.0 Working Folder / 0.7 Requisitions and PO's).", lead="Folders,")
b.bullet("add one row to the Projects sheet: Project code, Name, NetSuite Job ID, Status Live, Contract Value (ex GST), "
         "Internal Labour (BHF labour from the costing), PM, SharePoint Folder (path inside 6.0 Projects - Documents), "
         "Unearned Acct ID and WIP Acct ID.", lead="Projects sheet,")
b.bullet("from the latest costing (1.0 Working Folder / 0.3 Costing & Cashflow, the live costing tab), add the forecast "
         "lines: see the mapping table below. Use the dashboard (Forecast & payments) or the project's cashflow sheet.",
         lead="Forecast lines,")
b.bullet("add the customer payment milestones (%, amount, expected date) on the incoming line, from the offer or customer PO. "
         "They must add up to 100%.", lead="Customer milestones,")
b.bullet("on the project's P&L recognition tab, set the start month (first deposit invoice) and the total timeline in months; "
         "the stages split automatically (e.g. 14 months = 3/3/4/2/2). Lock the baseline once agreed.", lead="P&L timing,")
b.bullet("the project appears on the dashboard and its cashflow sheet is created at the next sync. Check the first "
         "Needs attention list and link anything unassigned.", lead="Check,")
b.para("Safeguard: the dashboard's All live projects page lists any NetSuite job with POs, bills or invoices in the last six "
       "months that is not in the Projects sheet. Set it up, or, if it is not a project to track (R&D, service), add it with "
       "Status Closed and it stops showing.", italic=True)
b.table(["Costing (live tab)", "Becomes", "Notes"], [
    ["Each supplier package or section (e.g. Welkin - System, DAF System, Site install)",
     "One outgoing forecast line", "Party = the supplier; Cost Type from the dropdown; Amount = extended cost; expected date "
                                   "= when the PO is planned. Small BHF-supplied items can share one line."],
    ["Lines at $800/day (Project Management, Engineering, Procurement, Commissioning)", "Internal Labour on the Projects sheet",
     "Not a forecast line: BHF time, used for net margin."],
    ["Contingency lines", "One outgoing line \"Contingency\"", "Tick Closed when no longer needed."],
    ["Bid / contract price", "Contract Value; incoming line(s)", "One incoming line per customer PO, with the customer PO number."],
    ["Options not taken (quantity 0)", "Nothing", ""],
], colw=[5.6, 4.6, 7.6])

# 5
b.heading("5.  NetSuite rules (accounts)")
b.table(["Rule", "Why"], [
    ["Every PO, bill, expense claim and stock issue has the Project set", "It is how the sync finds it. Uncoded documents "
                                                                           "never appear (the dashboard flags likely ones as Untagged in NS)."],
    ["Customer invoices carry the customer PO number", "Links invoices to the contract line; a new PO number creates a "
                                                       "variation line automatically."],
    ["POs state the payment terms (\"30% deposit, 35% FAT ...\")", "They are read off the PO PDF into the payment schedule."],
    ["Bills are raised from the PO (Bill button on the PO)", "Links bills to POs and milestones."],
    ["Close POs that are finished or cancelled", "Otherwise they show as open commitment (flagged Close PO in NS)."],
    ["Bills keep the supplier's invoice number", "Bills without one show as \"no ref #ID\"."],
    ["Month-end flattening journals by the 10th", "The P&L tab shows the last journal date, red when last month's has not run."],
], colw=[7.0, 10.8])

# 6
b.heading("6.  Weekly (PM, about 10 minutes per project)")
b.para("Open the project on the dashboard, Chart & attention tab. Clear Needs attention: fix the cause, or Acknowledge with a "
       "reason (it leaves the list; an overrun comes back if it grows more than 10% + $500).")
b.table(["Flag", "What to do"], [
    ["Unlinked", "Pick the forecast line from the dropdown (expense claims show after 14 days)."],
    ["Terms", "Type the real split (30/70 or 30% deposit, 70% delivery) and Apply, or Terms are right."],
    ["Over", "Real overrun: raise the forecast or acknowledge with the reason (FX, approved variation)."],
    ["Late", "Forecast line past its date with no PO: chase or move the date."],
    ["Overdue", "Customer invoice past due: chase the customer."],
    ["Close PO in NS / No PO on bill", "Ask accounts to close the PO in NetSuite. The app already treats it as done."],
    ["Stale PO / Not billed", "Chase the supplier invoice, or close the PO."],
    ["New variation", "Check the auto-added line's name, value and payment terms, then acknowledge."],
    ["Cost missing?", "A cost line with nothing in NetSuite on a mostly invoiced job: check it was entered."],
    ["Untagged in NS", "Ask accounts to tag the document to the project."],
    ["Wrong project? / Miscoded? / Customer PO / Shared account", "Fix the coding in NetSuite or the forecast line."],
], colw=[5.0, 12.8])
b.para("Also move any expected date that has slipped (forecast lines and milestones), so the cash curve stays honest.")

# 7
b.heading("7.  During the job")
b.bullet("the sync adds a \"Variation\" line itself when an invoice quotes a new customer PO, or when a supplier whose line is "
         "fully ordered issues a new PO. Better still, add the variation line (with its PO number) before NetSuite has it.",
         lead="Variations,")
b.bullet("add an outgoing line with Type Supplier recovery for money a supplier owes us; it reduces still to pay until "
         "their credit is linked.", lead="Back-charges,")
b.bullet("edit the Amount or expected date on the dashboard or the cashflow sheet. Sheet edits are saved at the next "
         "sync or when anyone presses Refresh.", lead="Forecast changes,")
b.bullet("on the project cashflow sheet, insert a row under Money out (or Money in): Item and Forecast are required; "
         "Type from the list; Party; Date.", lead="Adding a cost on the sheet,")

# 8
b.heading("8.  Month end and financial year")
b.bullet("accounts post the flattening journals; check each project's P&L tab shows last month's journal.", lead="Month end,")
b.bullet("the FY27 vs budget tab compares Systems sales (4071-4079) with budget; the P&L formula projects the rest of the "
         "year; the pipeline adds weighted enquiries.", lead="Budget,")
b.bullet("at year end, re-lock each project's P&L baseline; the P&L tab shows what moved between years.", lead="Year end,")

# 9
b.heading("9.  Pipeline (sales)")
b.para("Open Systems enquiries come from the Enquiries Pipeline Mastersheet. On the dashboard's Pipeline tab, set the start "
       "month, total timeline and probability for enquiries you expect to win (Add), and Hide the ones that will not. "
       "Settings are saved in the Pipeline sheet, never in the enquiries sheet.")

# 10
b.heading("10.  Close-out")
b.bullet("all bills in and paid; no open POs (the Completed page lists open POs on completed jobs for accounts).",
         lead="Clear NetSuite,")
b.bullet("tick Closed on finished forecast lines; set the project's Status to Complete (kept for FY history and the "
         "Completed page) or Closed (left out entirely).", lead="Close the project,")
b.bullet("move the project folder to BHF Projects Completed in Smartsheet and SharePoint.", lead="Archive,")

# 11
b.heading("11.  If something looks wrong")
b.table(["Symptom", "Check"], [
    ["Data as at time is old", "Render: bhf-cashflow-sync logs; press Refresh on the dashboard."],
    ["A PO or bill is missing", "Is the Project set in NetSuite? Untagged in NS flags list likely ones."],
    ["PDF not yet filed", "filing.log on the filing PC; the PC must be on and signed in."],
    ["Numbers differ from NetSuite", "Cash ties to NetSuite to the cent; P&L uses accounts 4071-4079 (not Unearned / WIP "
                                     "movements, which include the 1 July netting journal)."],
], colw=[5.0, 12.8])

b.save(OUT)
print(OUT)
