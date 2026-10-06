# Changelog

## 20.0.3.1.0 — 2026-10-06

Port of 19.0.3.1.0 to Odoo 20, merged with the 20.0.3.0.1 port below: the same receipts PDF fix, the new summary and
the picture of the crumpled ticket.

## 19.0.3.1.0 — 2026-10-06

- Receipts PDF of the expense sheet: the PDF library is called with the names shared by PyPDF2 2 and pypdf, so that
  a server running pypdf 3 or later no longer replaces every PDF receipt by the "unreadable" page.
- Summary of the app rewritten with the words people search for (receipt, OCR, expense, scanner).
- Store page: the picture of the crumpled ticket shows the photo and the expense side by side.
## 20.0.3.0.1 — 2026-10-06

Port of 19.0.3.0.1 to Odoo 20 (branch `20.0`).

- Access rights and the multi-company rules are in `security/ir.access.csv` (Odoo 20 replaced `ir.model.access` and
  `ir.rule` by `ir.access`).
- Binary fields hold a `BinaryValue`: the module reads them through `binary_bytes` (`models/odoo_compat.py`).
- Odoo 20 makes the journal entry at the approval, one per employee: an expense the module may hold back
  (re-invoiced on a project) gets an entry of its own, so that posting, refusing or taking back an approval leaves
  the others alone; "Unapprove" deletes the draft entry.
- Re-invoice policy: `reinvoice_policy` (was `expense_policy`); the delivered quantity of the module's line is set
  even though Odoo now computes it from analytic lines for such a product.
- Front end moved to Owl 3: `useProps`, signals, `this.` in templates, `t-call-slot`, the chatter of `web_portal_project`,
  icons from the Material Symbols set (`oi` / `data-icon`) in place of Font Awesome.
- Removed: the `start_month` / `end_month` attributes of the date filter (no longer supported), the manifest
  `description` (Odoo 20 translates it; the store page is `static/description/index.html`).
- 575 tests pass on Odoo 20; the form, the upload, the chatter attachment, the retouch and the selection bar were
  checked in a browser.

## 19.0.3.0.1 — 2026-10-05

Contact address of T.T.C. SAS in the manifest (`support`) and on the store page.

## 19.0.3.0.0 — 2026-10-05

First version published on the Odoo Apps Store: it gathers everything of 19.0.2.15 to 19.0.2.17 below.

## 19.0.2.17.0 — 2026-10-05

- **Help and feedback** (Settings, Expenses): three buttons open a new GitHub issue in the browser, with the
  title and the technical lines already written (module and Odoo versions, language, Sales app installed):
  *Report a bug*, *Suggest an improvement*, *Ask for a custom adaptation* (T.T.C. SAS answers with a quote).
  The page is public and the user writes and sends it; nothing is sent from the server and no receipt, name or
  amount is added. The repository is the system parameter `expense_scan.support_repo`
  (default `TTC-Technologies/expense_scan`). Translated in the ten languages.
- **Sheet**: quantities and the tax summary follow the language; **PDF preview**: the first page is drawn once
  per file instead of at each opening.
- **Hotel nights**: a hotel category is recognised in every language of the database, so the nights read on the
  bill are kept whatever the language of the user.
- **Store page** (rules of the Odoo Apps Store): the module is named *Expense Receipt Scanner* (25 characters at most),
  the page and its screenshots are in English, with no outside link, and it lists the network access, the weight of
  the requirements and measured processing times.

## 19.0.2.16.0 — 2026-10-05

- **Excel export**: a description or merchant starting with "=" is written as text. It used to
  become a formula ("=1+1 taxi" was calculated, "=HYPERLINK(...)" opened a link).
- **Expense sheet**: the tax rate follows the language ("4,9 %" in German, not "4.9 %").
- **Several companies**: the expense rules and the Excel templates of a company are no longer
  readable from another company (record rules); those with no company stay shared.
- **Kanban**: a receipt that is a PDF no longer shows a broken thumbnail.
- **Without the Sales app**: the settings read "Expenses by project", the "Re-invoice" choice gives
  way to a message saying what to install, and nothing is left "to decide".
- Documentation: the OCR models are downloaded once, which the readme now says.

## 19.0.2.15.0 — 2026-10-04

- **Support**: a second link in the settings, the manifest, the readme and the store
  page lets users support T.T.C. SAS, the company behind the module, besides the
  author. Translated in the ten languages.

## 19.0.2.14.0 — 2026-10-03

- **Re-invoicing asks nothing when there is nothing to re-invoice.** An expense
  without a project starts at "No" and is no longer "to decide" (which blocked
  its approval); choosing a project sets "Yes", and removing it sets "No". An
  expense created with a project by another module stays "to decide". A "No"
  given to an expense that has a project is kept (cost followed on the project,
  not billed). The scan no longer raises a "Re-invoice" point when it finds no
  project. The *Project* field stays visible whatever the answer. The update sets
  "No" on the draft and submitted expenses that were waiting without a project.
- **AI engines named as such**: the reading engines are listed as *AI RapidOCR /
  PP-OCR (recommended)* and *AI Tesseract*; both read with neural networks.
  RapidOCR stays the default.
- **Settings**: the date and kept-text help reads "Oldest date accepted, and how long
  the text read on a receipt is kept"; the menus and filters option says
  "Simplifications of Odoo's display". The support link shows a cup of hot
  coffee, steam included.
- Translations of these texts in the ten languages; the help of the *Re-invoice* field
  tells how the three answers work.

## 19.0.2.13.0 — 2026-10-03

Each function is found where it is looked for, in plain words.

- **Settings, in order**: *Receipt scanning*, *Re-invoicing* (with *Limit the
  projects to the employee's own*, which used to sit among the accounting
  fields), *Fields filled in by the scan*, *Expense rules and sheets*. What an
  ordinary user never touches (reading engine, Tesseract language, threads,
  model folder, photo processing, dates and kept text, Odoo's menus and
  filters) is under *Receipt scanner: advanced options*, each with its sentence
  of explanation. No setting is removed and no value changes with the update.
  Two labels say what they do: *Turn sideways receipts upright* and *Receipt
  beside the form on narrower screens*.
- **Project budget**: the expense budget, the figures and the Excel model are
  on an **Expenses** tab of the project form, no longer at the bottom of
  *Settings* between the e-mail alias and the visibility. When re-invoicing is
  off, the tab says where to turn it on (and opens the settings for an
  administrator).
- **Reimburse the employee** is a button of a posted expense and of the
  expense list (select, then the button) for accountants; it was only in
  *Actions*.
- **Expense rules**: each set has a switch in the list and stays there, greyed,
  when suspended: no need to archive a set to suspend it.
- **Expense form**: "Done" is *Save and close* and no longer competes with
  *Submit* as a second main button. An employee whose scan failed reads
  "enter the expense by hand"; the technical reason (a missing model, a
  download address) is shown to expense managers.
- **Expense sheet**: opened from the menu or the list, the dialog starts on all
  the expenses, not on the re-invoiced ones, which gave "0 expense(s)" to
  anyone who had none. The menu is *Expense Sheet*, like its dialog.
- **Names**: *Excel Export Templates*; the team page carries the title of its
  menu (*My Team's Expenses*); the list column is *Re-invoice*, like the form
  field, and no longer cut off.
- **First launch**: an empty expense list says how to add a receipt (*Scan* on
  a phone, *Upload* on a computer) and how to enter an expense by hand.
- **Approval refused for a re-invoicing still to decide** tells where to
  decide: the *Re-invoice* column of the list, or the expense.
- **Translations**: seven of the ten catalogues did not load (a stray line
  left by the last update of the files): Odoo ignored the whole file and the
  module showed English. They load again, three lost entries are back, and
  `tools/check_po.py` reports a *msgstr* without its *msgid*. The new texts are
  translated in the ten languages.
- **Tests**: rights by role (`with_user`), views by role and by language,
  settings by block, catalogues loaded and complete, rule switch. The tests
  that create a tax no longer depend on the country of the company of the
  database (a database without a chart of accounts made them fail).

## 19.0.2.12.0 — 2026-10-03

- **Nothing of Odoo changes unasked.** The renamed menu group ("Expense
  follow-up"), the lists opening on the current month and the trimmed search
  filters are three options of the Expenses settings ("Odoo's menus and
  filters"), off on a new installation. A database that already ran the module
  keeps what it had.
- **Uninstalling gives Odoo back** its menu name (in every language), the
  Expenses icon and the actions' context, which stayed modified.
- **"Limit the projects to the employee's own"** is a company setting (on, as
  before), shown with re-invoicing.
- **Scanning again** checks the right to edit the expense before anything else.
- Translations: the entries of removed texts are gone, the new texts are
  translated, and the French file uses the no-break space before `: ; ! ?` and
  inside « ».
- **Stands alone, tested**: new tests check that the module depends on Odoo apps
  only (Expenses, Project), that its data names no xmlid of Sales or of a
  companion module, that `account.move.expense_scan_sheet_files()` returns the
  Excel table on the model of the project and the receipts, and that the
  update moves the Excel model kept on a sales order to its project. The
  migration notes that Odoo drops the old column at the update of the module
  that declared it.
- **Correct VAT**:
  - The base of the entry is checked as the entry rounds its tax. A base
    whose VAT ends in half a cent (10 % of 1.75) stopped the correction with
    "The VAT generated is not the one asked", for about one 10 % expense in
    thirty.
  - Only the VAT that goes to the tax return counts as recovered. On fuel
    (80 % deductible) the expense recovers 16.00 of its 20.00 of VAT, and
    *Other amount* is what reaches the VAT account.
  - *Recoverable* gives back the VAT the entry booked with the tax, the one
    read on the receipt when it mixes rates, instead of the rate applied to
    the total; the rate applies when the entry booked none.
  - A tax due on payment is corrected once the expense is paid. The
    miscellaneous entry is due at once: its VAT goes to the VAT account and to
    the grid of the return, not to the waiting account.
  - The taxes of a purchase group are seen even when they have no scope of
    their own: their VAT is recovered and corrected, and a group of one tax
    gives it to *Recoverable* and *Other amount*.
  - The expense of a company the user cannot open is refused as such: the
    message no longer gives its name and state.
  - The warning of the wizard says what happened: "paid", and "invoiced" only
    for a re-invoiced expense. It said "invoiced and reimbursed" of an expense
    that is not re-invoiced, or that the company paid.
- **Translations**: Norwegian "et annet firma", "innrapportert"; Polish
  number of taxes; French "auprès de votre comptable"; Spanish "empresa".
- **Expense line of the sales order updated once per batch**: approving
  several expenses, re-invoicing a selection, spreading flat rates and creating
  expenses from several receipts update the expense line of each order once,
  at the end, instead of after each expense. Each update reads all the expenses
  of the projects of the order and posts the change of quantity in its
  chatter: a loop over 1,000 expenses did it up to 1,000 times. Outside these
  actions the line still follows each change at once.
  - Another module that creates or changes many expenses does the same with
    `with env['hr.expense']._expense_scan_batch_sync():`. Inside the block the
    line is behind; `_expense_scan_sync_pending()` brings it up to date (call it
    before invoicing the order there). The customer invoices of the module
    (creation, posting, reset, cancellation, credit note) do so by themselves.
  - The line follows the state at the end of the batch: when expenses still
    wait at its end, it stays held back, even if nothing waited for a moment
    during the batch.
  - An order whose line cannot be updated is logged and does not stop the
    others nor the approval, as before.
  - Benchmark out of the normal suite: `--test-tags expense_scan_bench`.

## 19.0.2.11.0 — 2026-10-03

- **Excel model of the expenses on the project**: the project form (Expenses
  group) gets the model of the table of re-invoiced expenses that goes with the
  e-mail of the customer invoices. `account.move.expense_scan_sheet_files()`
  returns the Excel table and the receipts of the expenses an invoice bills, for
  whichever module sends the invoice. A value kept on the sales order by an
  older companion module moves to its project at the update.

## 19.0.2.10.0 — 2026-10-03

- **Correct VAT**: once an expense is posted, an accounting manager can say
  that its VAT is not recoverable, is recoverable, or is another amount
  (button *Correct VAT* on the expense, or *Actions* on a selection).
  - A miscellaneous entry moves only the difference between the VAT recovered
    and the new one, through the tax engine, so that the tax return follows
    (refund repartition of the tax when VAT stops being recoverable, invoice
    repartition when it becomes recoverable). It is booked on the expense
    account with the analytic distribution of the expense: the cost of the
    project rises or falls by the difference. The expense, its entry, the
    reimbursement of the employee and the re-invoicing do not change.
  - The wizard shows the VAT recovered now (read from the accounting: the
    purchase tax lines of the entry, plus the posted corrections), proposes the
    tax of the expense, of its category, then of the company, and the date of
    the entry (a locked period is said, Odoo moves the date). *Other amount*
    needs an expense with a single tax and stays under the VAT the rate gives
    on the total.
  - A selection takes *Not recoverable* and *Recoverable*, with one entry per
    expense: its own account, analytic distribution and tax, and a standard
    reversal cancels one correction without touching the others.
  - New *Recoverable VAT* field on the expense when it differs from the VAT of
    its entry, a *VAT corrections* button, and a note in the chatter (old and
    new amount, link to the entry). The entry is linked to its expense; a
    reversal keeps the link, so it cancels the correction.
  - When the expense is reimbursed and invoiced (or not re-invoiced) the
    wizard warns that the VAT may already have been declared and requires a
    box to be ticked.
  - The entry of an expense cannot be reset, cancelled or reversed while a
    correction rests on it: reverse the correction first.
- **Expense line keeps the tax of the order**: the module no longer clears the
  tax of the line it adds to a sales order. The customer is billed the amount
  incl. tax of the expenses, and the VAT of the order is added to it as to any
  other line.
- **Projects limited to the assignments of the employee**: an employee only
  links an expense to the projects they are assigned to (project managers and
  administrators are not limited), and the check is made for the real user,
  not for the superuser that runs the constraints. A task must belong to the
  project of the expense.
- **An invoiced expense never goes back to draft**: neither one by one nor
  from the list; cancel the invoice (or issue a credit note) first.
- **Internal context marker**: the flags the module puts in the context of its
  own calls carry a random marker a client cannot guess.
- **Credit note checked line by line**: expenses are given back only when
  the credit notes take back every expense line of the invoice.
- **Scan only before approval**: an approved or posted expense is not
  rescanned, which would rewrite its figures.
- **Public holidays of the schedule of the employee** are the ones left out
  when an allowance is spread over days.
- Translations: the texts added since 19.0.2.9.3 that had none, in the ten
  languages.
## 19.0.2.9.4 — 2026-10-03

- **Expense Sheets** is a single menu entry instead of a group of three ("This
  Month", "Last Month", "Other Period..."): the wizard already offers the
  period.

## 19.0.2.9.3 — 2026-10-03

- **Expense lists** open on the current month. "Current month" and "Previous
  month" are filters of the search menu, between the filters and the expense
  date, which no longer lists the months. "Re-invoicable" filters became "Not
  re-invoicable", "Invoiced" and "Reimbursed"; the shortcuts that served no
  purpose (my team, paid by the employee or the company, ready to invoice) are
  gone.
- **"Expense follow-up"**: the menu that held "My Expenses" twice is renamed
  after what it holds.
- **Renamed records keep their translation**: the action "Reimburse the
  employee" and the renamed menu are translated again after an update.
- A hook (`_expense_scan_in_period`) lets another module give each order of a
  project only the expenses dated in its period.

## 19.0.2.9.2 — 2026-10-03

- The **Re-invoicable column** shows the answer; the total at the bottom is
  the amount that goes to the customers (the separate amount column is gone).
- The expense sheet wizard fills the mission when all the expenses belong to
  a single one.
- "Pay the employee" reads "Reimburse the employee".

## 19.0.2.9.1 — 2026-10-02

- **Re-invoicable column on a selection**: a click on a selected row applies
  the next answer to every selected expense when they share it, otherwise a
  dialog asks which one; expenses that cannot take it (no project at their
  date, already invoiced, approved and set to "to decide") are left and
  listed.
- **Re-invoiced amount** column, with its total at the bottom of the lists.
- **Pay the employee** (list action): approved expenses are posted, then one
  payment per employee settles them together with the expenses already
  posted, for instance by an invoice.
- **Spread over days**: a flat-rate expense entered for several days (a
  daily allowance, quantity 11) becomes one line per day of presence: the
  employee's missions when recorded, otherwise their working days, without
  public holidays, time off or days already holding that category.
- **Expense sheets**: the last row of an Excel template keeps the look of the
  others; files named `<kind>_<employee>_<MM-YYYY>`; the internal PDF is
  titled with the employee and the month, and only lists what the employee
  paid (what the company paid, a company car for instance, is left out).

## 19.0.2.9.0 — 2026-10-02

- **Re-invoicing on one order line**: the approved expenses of a project add
  up on the expense line of its sales order (the line whose product can be
  expensed; one is added if the order has none). Its quantity is the amount
  incl. tax at a unit price of 1, so an invoice carries one line for the
  month's expenses instead of one line per expense. Odoo's own mechanism
  (one line per posted expense) is no longer triggered: re-invoiced expenses
  no longer fill the standard "Customer to Reinvoice" field.
  - The line only moves once nothing is left waiting for the manager or for
    a decision on the project; an activity on the order says so, and the
    line bills only what it already holds in the meantime.
  - When the customer invoice is posted, the expenses it covers (oldest
    first) are noted on it and posted, unless they were posted by hand
    before. An expense that cannot be posted says why in its chatter; the
    invoice is not held up. Back to draft or cancelled, the invoice lets its
    expenses go.
  - A daily task is the safety net. Without Sales, a project or the company
    setting, nothing changes.
- **Re-invoicable column** in the expense lists: a ticked box, an empty box
  or a question mark (to decide), changed with one click. "Yes" looks for
  the project of the receipt date, as the scan does.
- **Approval settles the re-invoicing**: an expense left "to decide" cannot
  be approved.
- **Expense budget** on the project: planned amount, approved expenses,
  expenses to approve, budget left and a progress bar; a button lists the
  expenses and a filter finds the projects over budget.
- Expenses carry the invoice that re-invoiced them (filters "Ready to
  invoice" and "Invoiced").

## 19.0.2.8.7 — 2026-10-01

- **Polish, Swiss, Austrian and Italian receipts, from the corpus**:
  - Polish "SUMA PLN SUMA PTU 141,83 16,65" (total and tax on one line,
    behind their own labels), the deposit lines of the new scheme ("DO
    ZAPLATY OPAKOWANIA ZWROTNE SUMA 50,57 PLN 1,00": the amount to pay is
    the first one) and reductions ("-2,50"), which were read as the total
    on a dozen supermarket receipts.
  - "ENDSUMME", "Zahlbetrag", "Rechnungsbetrag" (Austria), "Slutsumma",
    "Bar CHF 1,65" (Swiss rounding), a total whose first letter the OCR
    lost ("otal CHF 32.50"), a space left in "403, 00".
  - A receipt that prints a currency and its conversion ("Total CHF" then
    "Total en EUR") keeps the first; "(ink. moms)" is not a tax line.
  - Italian layouts: the total and the tax side by side above "di cui
    IVA", a tax equal to the total, the title of the item column ("DESCRIZIONE
    ... IVA 13.00") taken for a tax line, a receipt cut before its total
    (the subtotal is taken).
  - Total and tax read the wrong way round, now also with several rates.
  - A receipt printed at 0 % (exempt) no longer asks for the tax.
  - Norwegian VAT tables ("Mva% Grunnlag Mva Totalt", "MVA-grunnlag MVA-%
    MVA Sum"): the tax was not read on any of the supermarket receipts.
  - Croatian "Za platiti", and a total or subtotal whose first letters the
    OCR lost ("OTAL", "OUS-TOTAL").
  - Merchants: "Lidl sp. z o.o." on a line of registry numbers, without the
    Polish form or the store number ("Rossmann SDP"), a street written in
    one word ("Hauptstrasse 45", "Kaufland - Gutschmidtstraße 19"), a
    currency code or a column title ("Stk Artikel Preis"), the slogan of
    Coop, brand names shown capitalised ("Billa", not "billa").
  - A percentage on an item ("App-Joker 25%", "Topfen 20%") is no tax rate
    when the receipt has no tax line: Austrian receipts were marked as
    carrying a foreign tax.
  - The keyword "b&b" no longer matches the "B-B" of a chewing gum.
  - Two column labels merged on the total line ("TOTALE COMPLESSIVO
    SUBTOTALE 42,48 2,15", Lidl Italia): the total and the tax were lost.
  - A custom tax at the rate of the receipt (a 7 % "IGIC") is taken when its
    tax group is named like a VAT; a pension contribution still is not.
- **Tested on eight charts of accounts**: the module's tests (399) pass on charts of
  accounts of France, Germany, Austria, Switzerland, Spain, Italy, Poland
  and Sweden, each with some twenty real receipts uploaded by an employee.
- **Dependencies declared** in the manifest (OpenCV, NumPy, RapidOCR, ONNX
  Runtime, pdf2image, openpyxl, and the `pdftoppm` program of Poppler): Odoo
  names a missing one when the module is installed. Tesseract stays optional.
- README and store page: exchange rates, rule signs and justification,
  languages, domestic and foreign VAT; banner and screenshots, taken on a
  German test database with fictitious receipts.
- The module is published by T.T.C. SAS (author and copyright notices).
- **Merchant with a street word** ("Brasserie du Quai", "Café de la Place")
  was taken for an address and left out.
- **VAT table whose rate is in the header** ("MwSt 19% Netto MwSt Brutto",
  then "33,28 6,32 39,60"): the tax is read when the amounts hold at that
  rate.
- **Receipt outline taking in a white object** (a menu touching the
  receipt): the photo was straightened along a wrong outline, which
  distorted the text and cut the end of the merchant name. An outline whose
  corners are far from right angles is no longer used.
- **Categories of a new database**: Odoo's own "Meals" category was not
  taken for meals (only "Meal" was); category names are recognised in more
  languages ("Kraftstoff", "Parkgebühren", "Übernachtung", "Drivmedel",
  "Måltider"...), flat allowances ("Pauschale") excepted. A category
  created or renamed after the installation gets the receipt words of its
  family, as the categories present at the installation did.
- **Spanish receipts** (new database in Spain, then the Spanish corpus):
  VAT tables "TIPO BASE CUOTA", "IVA% IVA + P N = PVP" (Lidl), "Imp. % Base
  Cuota" with amounts under one euro printed without their zero (",33"),
  "Tasa Sin IVA Total IVA IVA Inc."; the total "€* TOT 6,42" (Alcampo); a
  misread "FACTURA SIMPLIFICADA", a column header or the change given are
  no longer taken for the merchant.
- **More Spanish tables** (real restaurant and shop receipts): the rate
  between the base and the tax ("BASE %IVA IMP.IVA / 63,82 10,00 6,38"),
  rate codes beyond D ("F 10%"), "Neto", "€x TOT" for "€* TOT", "C IVA
  4,00" read as a rate and not as the tax. On French receipts too, a rate
  column no longer lets a base or a total pass for the tax.
- **Categories learn the words of their receipts**: every day, the words
  found on the receipts of a category once submitted, and almost never on
  the others, are learnt for it (at least 3 receipts and a third of them).
  A category named like no known family ("Subscriptions", "Training")
  is thus recognised without anyone entering words. Words common to all
  receipts and the names of the employees and companies are never learnt.
  Shown read-only on the category, next to the declared words. Only the
  categories chosen by a person teach: a suggestion of the scan kept as is
  would teach back its own words, mistakes included, and drift over time.
  A word must also come from at least two merchants (the words of a single
  merchant, its name or its street, are already known from the merchant
  history), and a word entered on a category by hand is never learnt for
  another one. Correcting a suggested category (even on a draft) checks
  the learnt words again within minutes: the correction weighs as three
  receipts against the words that led to the wrong category. The log tells
  how many words were learnt, from how many receipts.
- **Italian receipts** (new database in Italy, the Italian corpus and real
  receipts of trips): the rate printed on the items when the tax line has
  none ("di cui IVA 3,55" under items at "10,00%"; several rates give the
  highest as a ceiling); a rate among three amounts ("17.93 22.00 3.94")
  no longer passes for the tax, which was read as 20 or 22 on charging and
  hotel invoices; an exempt line at 0 % carries no tax ("ESC.IVA ART.15");
  "10.0000%" read as 10 %, not 0 %; "Total des taxes", "TVA totale" as the
  sum; "Cena bez DPH", "sin IVA", "ohne MwSt" as bases without tax.
- **Total checked against the tax**: when the tax and its single rate are
  read, the total is the amount of its line that they fit ("TOTALE
  COMPLESSIVO COCA BOTT 10,00% 19,30 3,90": 19,30, not the item price
  glued to it; "Total € 42.30 (HT: € 38.45)": 42,30).
- **Tax picked in the Italian chart**: at 4 % the only active purchase tax
  is "4% INPS", a pension contribution. A tax of another kind than the
  company's default purchase tax is no longer set; none is set rather than
  a wrong one.
- **Upside down, in every language**: the clues that tell a receipt right
  side up from upside down (an amount after its label, the merchant at the
  top, the payment at the bottom) were French words only. A Spanish receipt
  held upright was turned over on a stray "TOTAL" and the "SA" of "V sa
  Credit". Measured on the corpus, a photo as taken and turned over: 68 %
  of consistent decisions instead of 51 %, none that turns a receipt both
  ways.
- **Spanish restaurant words** for the meal category: "mesa", "camarero",
  "comensal" (added to existing categories on update).
- **"Scan again" starts from the original photo** when the receipt was not
  retouched by hand: a wrong automatic turn or crop of an earlier scan was
  read a second time. A retouch by hand is still kept.
- **Receipt cut by the crop**: a fold was taken for the edge of the paper
  and the crop went through the receipt. When the text runs off the side of
  the cropped image, the photo as taken is read too, and the reading that
  finds more is kept.
- **Country of the receipt**: a tax name is shared by several countries
  ("IVA" in Spain, Italy and Portugal, "TVA" in France and Belgium, "MwSt"
  in Germany and Austria). The tax number with its country prefix, the
  national identifier (SIRET, P.IVA, CIF, NIF, NIP, CHE) or the phone prefix
  now tells where the receipt was issued: an Italian receipt is foreign for
  a Spanish company, a Belgian one for a French company. The buyer's own
  tax number, printed on hotel invoices, is left out.
- **Tax picked in a large chart of accounts** (Spain: 4, 10 and 21 % for
  goods, services, investment goods, intra-EU purchases, imports): a tax of
  a fiscal position (intra-EU, import) is no longer taken for a receipt paid
  on the spot, and the tax named like the company's default purchase tax
  wins ("10% G" next to "21% G"); "10% EX G", an import tax, was set.
- **Total and tax read the other way round** on a crumpled receipt (tax of
  12.00 for a total of 1.92): exchanged when the total is the tax of the
  larger amount at the rate read.

## 19.0.2.8.6

Found while installing the module on a new German database.

- **Domestic tax per company country**: "MWST" was taken for a foreign tax,
  and not deducted, for a German company; only "TVA" counted as domestic.
  Each country has its names (MWST/USt, MOMS, MVA, BTW, IVA, PTU...);
  "VAT", the English word, belongs to all.
- **Old receipts converted at their day's rate**: an expense older than the
  rates known took the oldest one; the rate of its day now comes from the
  ECB history, downloaded only then.
- **VAT tables misread by the OCR**: net and gross columns make a header
  even when the tax word is misread ("NUST BRUTTO NETTO"); a "%" read as an
  8 ("A 198 0.68 4.28 3.60") is accepted when the amounts hold at that rate.

## 19.0.2.8.5

- **Exchange rates of the European Central Bank**: Odoo Community updates
  no rate, so a foreign receipt was converted one for one (29.19 EUR shown
  as 29.19 kr). A daily task adds the reference rates of the last ninety
  days for the active currencies, and runs at once when a scan activates a
  currency; draft expenses still counted one for one are converted again.
  Setting "Exchange rates from the European Central Bank", on by default.
  The only network request of the module: a public file, outside the scan.

## 19.0.2.8.4

Found while installing the module on a new Swedish database, then scanning
Swedish and foreign receipts.

- **Receipt in an inactive currency**: the currency is activated and the
  expense recorded in it; it was recorded in the company currency (12.50
  EUR counted as 12.50 USD). A missing exchange rate stays a point to check.
- **Nordic VAT tables** ("Moms% Moms Netto Brutto", Norwegian "MVA") are
  read.
- **Several taxes at one rate** (goods, services, intra-EU purchases, as in
  the Swedish chart of accounts): the category's own tax, else the first
  ordinary one; no tax was set.
- **Tax rounded line by line** (9.41 printed for a ceiling of 9.40) is no
  longer taken for a misreading.
- **Odoo's default category** ("Expenses") counts as no category chosen when
  the settings name none: the scan recognised no category at all.
- **"Use the tax read on the receipt"** is on for new companies; when it is
  off, the receipt tax shows the tax the rate gives, as the journal entry.

## 19.0.2.8.3

- **Installation on a database without French**: the installation read the
  category names in French and failed ("Invalid language code: fr_FR") when
  that language was not installed. Names are now read in the installed
  languages only; so is the language of the rule findings.
- Tests no longer assume a French company in euros: they pass on a new
  database (United States, dollars, generic taxes) as on a French one.

## 19.0.2.8.2

- **Merchant**: a label waiting for its value ("Commentaire:", "Horário de
  funcionamento:") is no longer taken for the merchant, nor the comment of
  an order (Shopcaisse), nor the Portuguese word for invoice.
- **Long receipt on a phone**: "Expand the preview" shows the whole receipt,
  as wide as the screen; the strip kept a maximum height that cut the top
  and bottom of the image, expanded or not.

## 19.0.2.8.1

- **Tax not read when the OCR glues an amount to the header of the VAT
  table** ("Total Promotion TVA Taux MONT.TTC MONT.TVA TOTAL HT 3,02", Lidl):
  three column labels in a row, the tax among them, with no figure between
  them, now make a header whatever the order of the columns.

## 19.0.2.8.0 — English base, translations, review fixes

- **English base, translations**: the module is written in English; the
  French interface is provided by a translation file, as are the other
  languages.
- **Foreign receipts**: US dates on dollar receipts, dotted dates
  ("20.08.2026"), dates glued to the time, Norwegian, Swedish and Danish
  kroner told apart, currency deduced from the legal mentions when none is
  printed (Polish tax number, Swiss company number, US address), total words
  of more languages, Swiss, American and Norwegian totals.
- **Automatic descriptions** are recognised whatever the language they were
  written in, so a new scan still replaces them.
- **Expense sheet**: a receipt with several tax rates is recognised from a
  stored flag instead of the French wording of the tax read (migration for
  the receipts already scanned).
- **Delivered rules**: a general "Good practice" set in English, without
  amounts (fines and personal expenses, hotel extras, alcohol, travel class).
  The rental car category check is removed. The URSSAF ceilings are no longer delivered; existing
  installations keep their rules. Names and notes of rules and export
  templates are translatable.
- Amounts in rule warnings follow the user's format; hotel categories are
  recognised in more languages; Tesseract reads English by default.
- **WebP photos**: read by OpenCV when Pillow was built without WebP support.
- The labels of the automatic descriptions are computed once per scan
  instead of once per neighbouring expense.
- **Benchmark**: international corpus drawn from Open Prices, results per
  country (`tools/fetch_openprices.py`, `tools/bench.py --open-prices`).
- **Receipt tax** shown as the journal entry will carry it: a manual entry
  showed 0.00 while the entry deducted the tax of the rate.
- **Multi-page PDF** receipts are kept whole: no longer replaced by an image
  of their first page, and not offered for retouch.
- **Parsing**: an unreadable total no longer takes the tax line below it; US
  sales tax is a foreign tax; the nights of a hotel bill are read; a
  merchant written in capitals keeps its contractions ("Joe's").
- **Trip description**: taken from a neighbouring expense only when its date
  was read on its receipt, never from a street named after a city, and
  noted in the history. A category chosen by hand survives "Scan again".
- **Receipt deleted, then replaced**: the reading of the deleted receipt is
  forgotten and the next receipt is scanned, keeping the fields entered by
  hand. "Scan again" without a receipt says so; two scans of one expense at
  once no longer end in a database conflict.
- **Receipt moved to its own expense** (different total or date): both
  expenses say it in their history, with a link, and "Scan again" shows a
  notification.
- **Expense rules**: an amount not converted to the company currency (currency
  not active, no exchange rate) is no longer compared with the limits; the
  general conditions printed on a ticket are left out of the word checks;
  "ℹ︎" marks a point to check, "⚠" a breach, both shown in the expense list
  and on the cards; the text is written in the employee's language. "Done"
  puts out the ℹ︎ signs, a justification the ⚠ signs; the text stays on the
  expense, the justification is shown to the manager, and a new finding
  lights its sign again. "Outside the rules" lists the breaches, justified
  or not. Entering an exchange rate checks the expenses in that currency
  again.
- **Exchange rate missing**: a receipt in an active currency without a rate
  gets a point to check (Odoo would count it one for one).
- **Not a receipt**: an image without amount or date gets one clear point to
  check instead of an expense at 0 read "with 100 % confidence".
- **Numbers** in the scan details, the tax read, error messages and the sheet
  wizard follow the user's language.
- **Account and analytic distribution** on the expense form: shown to
  expense managers only when Odoo shows them too (full accounting features
  for the account, analytic accounting for the distribution). Turning off
  analytic accounting now hides the distribution for managers as well. The
  analytic account of the project is still set automatically.
- **Interface**: history of the expense folded, one click away; help of the
  settings shown under each option; retouch controls that stay in place and
  a "0°" button; tax rate readable on a tablet in landscape; PDF preview
  fitting a tablet in portrait; one notification for several receipts.

## 19.0.2.7.1

- **Tax not read on "TVA % Taxe HTVA TVAC" receipts** (columns without VAT
  and VAT included, Belgian labels used by some till software): the header
  of the VAT table was not recognised. Fixed.
- **Description "Meals du 23/09/2026" copied from one expense to another**: a
  scan run without a language (scheduled task) wrote the automatic
  description in English; not recognised as automatic, it passed for the
  purpose of a trip and spread to the expenses of the same day (a hotel
  included). The description is now written in the employee's language and
  recognised in every installed language; a new scan replaces the ones
  already copied.

## 19.0.2.7.0 — Receipt retouch

- **Retouch**: button under the receipt preview (strip on a phone). Quarter
  and fine rotation, crop with handles, an "Auto" button that suggests the
  automatic retouch without applying it, "Reset" to go back to the uploaded
  image. The retouch always starts from the original photo, which is never
  changed, and applies the previous settings again. It is applied without
  scanning again; "Scan again" then reads the retouched image as it is. A PDF
  can be retouched too (first page rendered as an image).
- **Multi-page PDF**: if the first page gives no total, the next ones are
  read (up to 5).
- **Receipt attached to a new expense**, before it is saved: "Attach files"
  was greyed out. The expense gets a provisional description and the default
  category, is saved, then the receipt is scanned.
- **Manual entries kept**: the fields entered before uploading the receipt
  (date, amount, category, currency, tax, vendor, project), or corrected after
  a scan, are no longer replaced by the scan or by a new scan. A difference
  with the receipt is shown under the field ("The receipt says €9.90: check
  the amount entered.").
- **Second receipt**: the main receipt stays on the expense and is the base
  of a new scan; an unrelated receipt is moved to a new expense, with its own
  description and category. Fixed an error ("Record does not exist") when
  scanning again after adding a second receipt.
- **Receipt deleted then replaced**: the retouch opened the old receipt
  again, and applying it overwrote the new one; the new one was no longer
  cropped automatically. The original photo and the retouch settings are now
  deleted with the displayed receipt. The category guessed from the old
  receipt no longer stays on the new one.
- **Preview on a phone**: the retouched image is shown (the old one stayed in
  the cache); a PDF is shown by its first page and opens in the PDF viewer.
  The form is reloaded after a receipt is added or deleted.
- **Preview on a computer**: a PDF no longer overflows at the top of the
  panel.
- Comments and docstrings rewritten in a factual style; two category tests
  that did not run are back in their class.

## 19.0.2.6.4

- **Receipt with several VAT rates**: the expense kept the category's default
  tax rather than the receipt's. It now takes the highest rate actually
  printed (the tax amount was already right — the tax attached to it was
  not).

## 19.0.2.6.3

- **Tax read wrongly on vending machine receipts**: because of the OCR, the
  header of a tax table sometimes ends up glued to a nearby total on the same
  line ("TOTAL EN EUROS : 15,80 HT TVA TTC"). The module read that total as
  if it were the tax itself (10 times the real amount). Fixed, without losing
  the reading of two-rate receipts whose lines also cite HT/TVA/TTC between
  amounts.

## 19.0.2.6.2

- **Tax rate misread on "Code Taux HT Montant TTC" receipts** (fast food
  tills in particular): the header of the tax table was not recognised, for
  want of the word "TVA" (replaced by "Montant"); the rate then fell back on
  the category's default, which may differ from the one printed on the
  receipt. Fixed.

## 19.0.2.6.1

- **Text read kept for 10 years by default** (instead of 365 days): the
  retention period French law sets for accounting records (Commercial Code,
  art. L123-22), receipts included. Migration for the companies still on the
  former default.

## 19.0.2.6.0 — Hardening

No visible change in behaviour: what could bring a worker down or keep
personal data for too long is fixed.

- **Interrupted scan**: a scan "running" for more than 15 minutes goes to
  error instead of being started again forever by the form and by the
  scheduled task.
- **Input limits**: files of 25 MB at most, image scaled down while decoding
  beyond 50 megapixels (refused beyond 250), PDF rendered at a bounded
  resolution and with a timeout.
- **Rights before the lock**: starting a scan checks the write access before
  locking the row.
- **Personal data**: the text read on a receipt is erased from submitted
  expenses after a delay set per company (0 = never). The corpus test log no
  longer writes the texts.
- **Merchant history kept per company.**
- **Benchmark** (`tools/bench.py`): replays the parsing on a snapshot of the
  texts read, without Odoo; the category computation now lives in
  `ocr/categorize.py`, shared with the module.
- `tools/check_ocr.py` becomes `tools/check.py` (syntax and imports).
- View warning "link without role" removed.

## 19.0.2.5.x

Background scan with progress, hints under the fields instead of the banner,
camera / gallery / files choice, "Expense Sheets" menu by period,
chronological order of expenses, "Back to draft", trip purpose carried over,
reading fixes (tolls, online invoices, VAT).
