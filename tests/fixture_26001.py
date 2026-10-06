"""Real BHF26001 data as of 05/10/26 (from NetSuite) - used for tests and the preview page."""
PROJECTS = [{"Project": "BHF26001", "Name": "Stacked Farm DAF UF RO 8", "NetSuite Job ID": "13217", "Status": "Live",
             "Contract Value": 1614700, "Internal Labour": 98100, "PM": "Daniel Shub",
             "SharePoint Folder": "2.0 Projects Contracted/BHF26001 Stacked Farm DAF UF RO 8"}]
F = lambda item, d, ct, party, amt, po="", date=None, notes="": {"Item": item, "Project": "BHF26001", "Direction": d, "Cost Type": ct,
        "Party": party, "Amount": amt, "PO / Order #": po, "Expected Date": date, "Notes": notes}
FORECASTS = [
    F("Main contract", "In", "Customer Milestone", "Stacked Farm", 1564700, "PO-0007", "2026-05-11"),
    F("Pre-flattening design", "In", "Customer Milestone", "Stacked Farm", 50000, "PO-3596", "2026-02-27"),
    F("Bondalti - main system supply", "Out", "Equipment", "Enkrott", 543655.14, "PO005995", "2026-05-22"),
    F("Bondalti - engineering design (pre-flattening)", "Out", "Engineering / Design", "Enkrott", 6692.64, "PO005725"),
    F("Bondalti - variations", "Out", "Variation", "Enkrott", 59119.67, "PO006153, PO006263"),
    F("Bondalti - documentation", "Out", "Engineering / Design", "Enkrott", 1404.48, "PO006316"),
    F("D2 Process - 3rd party design review", "Out", "Consultants", "D2 Process", 36720, "PO006017"),
    F("D2 Process - PM support", "Out", "Consultants", "D2 Process", 50000, "PO006157"),
    F("Jar testing", "Out", "Consultants", "Research Laboratory Services", 2676, "PO005893"),
    F("UF membranes", "Out", "Equipment", "Scinor", 7869.33, "PO006180"),
    F("RO membranes", "Out", "Equipment", "Aquacorp", 13035, "PO006182"),
    F("Site install - mechanical contractor", "Out", "Install / Site Works", "", 200000, "", "2027-03-01"),
    F("Freight - Portugal to Melbourne", "Out", "Freight & Logistics", "", 0, "", "2027-02-01"),
]
T = lambda doc, typ, d, party, po, amt, date, fc, paid=None, due=None, ccy="": {"Doc #": doc, "Project": "BHF26001", "Type": typ,
        "Direction": d, "Party": party, "PO / Order #": po, "Amount": amt, "Date": date, "Due Date": due,
        "Paid Date": paid, "Forecast": fc, "Currency Amount": ccy, "Status": "Paid In Full" if paid else "Open"}
TXNS = [
    T("INV021040", "Invoice", "In", "Stacked Farm", "PO-3596", 25000, "2026-01-28", "Pre-flattening design", "2026-02-20", "2026-02-27"),
    T("INV021576", "Invoice", "In", "Stacked Farm", "PO-3596", 25000, "2026-03-24", "Pre-flattening design", "2026-04-21", "2026-04-23"),
    T("INV021864", "Invoice", "In", "Stacked Farm", "PO-0007", 782350, "2026-04-11", "Main contract", "2026-05-21", "2026-05-11"),
    T("PO005995", "PO", "Out", "Enkrott, SA", "PO005995", 543655.14, "2026-05-05", "Bondalti - main system supply", ccy="EUR 337,000.00"),
    T("PO005725", "PO", "Out", "Enkrott, SA", "PO005725", 6692.64, "2026-02-13", "Bondalti - engineering design (pre-flattening)", ccy="EUR 4,000.00"),
    T("PO006153", "PO", "Out", "Enkrott, SA", "PO006153", 9796.82, "2026-07-23", "Bondalti - variations", ccy="EUR 6,005.64"),
    T("PO006263", "PO", "Out", "Enkrott, SA", "PO006263", 49322.85, "2026-09-09", "Bondalti - variations", ccy="EUR 30,634.36"),
    T("PO006316", "PO", "Out", "Enkrott, SA", "PO006316", 1404.48, "2026-10-02", "Bondalti - documentation", ccy="EUR 865.60"),
    T("PO006017", "PO", "Out", "D2 Process", "PO006017", 36720, "2026-05-06", "D2 Process - 3rd party design review"),
    T("PO006157", "PO", "Out", "D2 Process", "PO006157", 50000, "2026-07-24", "D2 Process - PM support"),
    T("PO005893", "PO", "Out", "Research Laboratory Services", "PO005893", 3224, "2026-04-14", "Jar testing"),
    T("PO006180", "PO", "Out", "Scinor Membrane Technology", "PO006180", 7869.33, "2026-08-04", "UF membranes", ccy="USD 5,508.00"),
    T("PO006182", "PO", "Out", "Aquacorp", "PO006182", 13035, "2026-08-04", "RO membranes"),
    T("PRO E AN66/21", "Bill", "Out", "Enkrott, SA", "PO005995", 176439.72, "2026-05-22", "Bondalti - main system supply", "2026-05-22", "2026-05-22", "EUR 101,100.00"),
    T("11548", "Bill", "Out", "D2 Process", "PO006017", 9294.55, "2026-06-30", "D2 Process - 3rd party design review", "2026-07-27", "2026-06-30"),
    T("INV-1820", "Bill", "Out", "Research Laboratory Services", "PO005893", 3224, "2026-09-30", "Jar testing", "2026-10-02", "2026-09-30"),
    T("SMTIN-AU-012-260804", "Bill", "Out", "Scinor Membrane Technology", "PO006180", 1549.14, "2026-08-04", "UF membranes", "2026-08-21", "2026-11-04", "USD 1,101.60"),
]
S = lambda po, party, d, seq, label, pct, amt, date=None, billed=None, src="PO PDF": {"Milestone": label, "Project": "BHF26001",
        "PO / Order #": po, "Party": party, "Direction": d, "Seq": seq, "Percent": pct, "Amount": amt, "Expected Date": date,
        "Billed Doc": billed, "Source": src, "Confirmed": True, "Terms Text": ""}
SCHEDULE = [
    S("PO-0007", "Stacked Farm", "In", 1, "50% deposit on order", .5, 782350, "2026-05-11", "INV021864", "Customer PO"),
    S("PO-0007", "Stacked Farm", "In", 2, "25% FAT documents", .25, 391175, "2026-12-15", None, "Customer PO"),
    S("PO-0007", "Stacked Farm", "In", 3, "20% delivery", .2, 312940, "2027-03-31", None, "Customer PO"),
    S("PO-0007", "Stacked Farm", "In", 4, "5% SAT", .05, 78235, "2027-06-30", None, "Customer PO"),
    S("PO-3596", "Stacked Farm", "In", 1, "50% order", .5, 25000, "2026-02-27", "INV021040", "Customer PO"),
    S("PO-3596", "Stacked Farm", "In", 2, "50% P&ID + IFC", .5, 25000, "2026-04-23", "INV021576", "Customer PO"),
    S("PO005995", "Enkrott", "Out", 1, "30% advance", .3, 163096.54, "2026-05-22", "PRO E AN66/21"),
    S("PO005995", "Enkrott", "Out", 2, "35% FAT", .35, 190279.30, "2026-12-15"),
    S("PO005995", "Enkrott", "Out", 3, "25% before shipment", .25, 135913.79, "2027-01-15"),
    S("PO005995", "Enkrott", "Out", 4, "10% commissioning", .1, 54365.51, "2027-05-15"),
    S("PO005725", "Enkrott", "Out", 1, "50% deposit", .5, 3346.32, "2026-11-15"),
    S("PO005725", "Enkrott", "Out", 2, "50% final", .5, 3346.32, "2026-12-15"),
    S("PO006157", "D2 Process", "Out", 1, "Instalment 1", .25, 12500, "2026-10-24"),
    S("PO006157", "D2 Process", "Out", 2, "Instalment 2", .25, 12500, "2026-12-24"),
    S("PO006157", "D2 Process", "Out", 3, "Instalment 3", .25, 12500, "2027-02-23"),
    S("PO006157", "D2 Process", "Out", 4, "Instalment 4", .25, 12500, "2027-04-23"),
    S("PO006180", "Scinor", "Out", 1, "20% on order", .2, 1573.87, "2026-08-04", "SMTIN-AU-012-260804"),
    S("PO006180", "Scinor", "Out", 2, "80% before shipment", .8, 6295.46, "2026-10-20"),
    S("PO006182", "Aquacorp", "Out", 1, "20% on order", .2, 2607, "2026-10-15"),
    S("PO006182", "Aquacorp", "Out", 2, "80% on delivery", .8, 10428, "2026-11-15"),
]

# FY27 Systems Sales budget vs recognised revenue, as NetSuite reports it (budget and actuals Jul-Oct 26).
BUDGET = {
    "fy": "FY27", "at": "demo data",
    "budget": {"Jul 2026": 559100, "Aug 2026": 253300, "Sep 2026": 473300, "Oct 2026": 493300, "Nov 2026": 503300,
               "Dec 2026": 473300, "Jan 2027": 677400, "Feb 2027": 812400, "Mar 2027": 662400, "Apr 2027": 692400,
               "May 2027": 662400, "Jun 2027": 662400},
    "actual": {"Jul 2026": 305529, "Aug 2026": 278020, "Sep 2026": 262020, "Oct 2026": 35065},
    "recognised": {"BHF26001": {"all": 1059189.0, "fy": 363309.0}},
}
