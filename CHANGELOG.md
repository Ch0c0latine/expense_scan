# Changelog

## Unreleased

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
- **WebP photos**: read by OpenCV when Pillow was built without WebP support.
- The labels of the automatic descriptions are computed once per scan
  instead of once per neighbouring expense.
- **Benchmark**: international corpus drawn from Open Prices, results per
  country (`tools/fetch_openprices.py`, `tools/bench.py --open-prices`).

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
