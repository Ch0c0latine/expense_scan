# Changelog

## Unreleased

- **Dependencies declared** in the manifest (OpenCV, NumPy, RapidOCR, ONNX
  Runtime, pdf2image, openpyxl, and the `pdftoppm` program of Poppler): Odoo
  names a missing one when the module is installed. Tesseract stays optional.
- README and store page: exchange rates, rule signs and justification,
  languages, domestic and foreign VAT.
- The module is published by T.T.C. SAS (author and copyright notices).

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
