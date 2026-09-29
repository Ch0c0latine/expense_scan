# Receipt Scanner for Expenses — Odoo 19

Photograph a receipt with your phone and get a filled-in expense, reviewed
side by side with the image of the receipt.

Everything runs **on your own server**: no IAP account, no API key, no paid
token, no document sent to a third party.

---

## What the module does

| Step | Detail |
|---|---|
| **One tap** | The **Scan** (mobile) / **Upload** (desktop) button of the expense list opens the camera or the photo library directly. |
| **Crop** | The edges of the receipt are detected in the photo, the perspective is corrected, the image is cut out. |
| **Straighten** | The remaining tilt of the lines is measured and cancelled; a photo taken sideways (quarter turn) is turned upright. |
| **Read** | The PP-OCR networks read the receipt (text area detection + recognition), in about 0.5 to 1.5 s on a recent processor. |
| **Extract** | Merchant, date, time, total, currency and tax are extracted, each with a confidence score. |
| **Review** | The form opens at once, receipt on one side, fields on the other: the scan runs in the background, shows its progress and fills the fields as it goes. A hint appears under each field to check (date, total, tax, category...) and goes away once it is corrected. |

The module **never approves** an expense on its own: it fills in, flags what
is doubtful, and leaves the decision to the user.

### What it adds to Odoo 19

Odoo 19 Community already provides the receipt upload button and the receipt
preview panel. The module builds on them rather than rewriting them, and adds
what is missing:

- the OCR reading itself (Enterprise only, through IAP);
- cropping and straightening of the photo;
- opening the review form directly after a single scan (Odoo otherwise goes
  back to a list);
- the receipt preview on phones and on screens narrower than 1400 px, where
  Odoo shows no panel;
- a camera / gallery / files choice on phones, so that the camera comes
  first on iOS and Android.

### And around the scan

- **Category**: recognised from the receipt words declared on each category,
  the activity code printed on the receipt, the price per litre or kWh, a
  known brand at the top, and the history of merchants already classified.
- **Several receipts for one purchase** (till receipt + card slip): the
  pieces are compared and merged into one expense, or split when they
  clearly describe two purchases.
- **Retouch**: rotate and crop a receipt by hand, or accept the automatic
  suggestion.
- **Trip purpose**: an expense on the same day or at the same place as a
  neighbouring expense takes up its description ("Sales visit Acme").
- **Projects**: re-invoice an expense to a project, or charge it to the
  project budget only; the analytic account follows the project.
- **Expense sheets**: PDF summary with tax by rate and numbered receipts,
  Excel export on your own template.
- **Expense rules**: meal, daily and hotel ceilings, words to watch; a
  warning shows on the expense and a filter gathers them.
- **Team**: managers enter expenses for their team members.
- **E-mail**: receipts sent from an employee's private address are
  recognised; the subject becomes the description.
- **Mileage**: a mileage rate per employee.

---

## Server requirements

To install in Odoo's Python environment, with `pip` for the Python packages
and the system package manager for Tesseract and Poppler:

| Package | Role | Required |
|---|---|---|
| `opencv-python` (or `-headless`) | cropping, straightening | yes |
| `numpy` | same | yes (pulled by OpenCV) |
| `Pillow` | EXIF reading | already in Odoo |
| `rapidocr` + `onnxruntime` | recommended OCR engine | yes (unless Tesseract is used) |
| `pytesseract` + a `tesseract-ocr-<lang>` package | fallback engine | no |
| `pdf2image` + `poppler-utils` | PDF receipts | no |
| `openpyxl` | Excel export of expense sheets | for that export |

None of these dependencies is declared in the manifest: the module installs
even if they are missing, and the settings screen then says exactly what is
missing.

**Footprint**: the PP-OCR models weigh a few tens of megabytes and are
downloaded once, at the first scan or with the *Test and preload the engine*
button. They are stored in Odoo's data directory, not in `site-packages`.

**Worker memory**: with workers (`workers > 0`), Odoo recycles a worker that
exceeds `limit_memory_soft`, measured as *virtual* memory. The glibc
allocator reserves up to 64 MB of address space per allocating thread, and
the OCR engine adds about twenty: the virtual memory of a worker jumps by two
gigabytes at the first scan, while the memory actually used does not follow.
The worker is then recycled after each scan, and the next one loads the
models again — one more second. Limiting these reservations in the service
environment solves it:

```ini
# systemctl edit odoo19
[Service]
Environment=MALLOC_ARENA_MAX=2
```

---

## Settings

**Expenses → Configuration → Settings → Receipt scanning**

- **OCR engine**: `Automatic` uses RapidOCR if available, otherwise
  Tesseract.
- **Threads per worker**: 4 by default. Odoo already runs several workers;
  letting ONNX open one thread per core in each of them lowers the throughput
  instead of raising it.
- **Photo processing**: crop, straighten, fix quarter turns, keep the
  original photo.
- **Accounting fields**: using the tax read and looking up the vendor, **off
  by default**. A wrong reading there has accounting consequences, unlike an
  amount checked on screen.
- **Split view from 768 px**: shows the receipt next to the form on laptop
  screens, which Odoo otherwise leaves without a preview.

The **Test and preload the engine** button loads the models and reads a
control image: use it after each restart of the service, so that the first
real scan does not pay for the loading.

---

## Getting started in five minutes

1. **Expenses → Configuration → Settings**: scanning is on by default;
   *Test and preload the engine* checks the installation.
2. **Scan a receipt** from the expense list (*Scan* button on a phone,
   *Upload* on a computer).
3. **Expense rules**: the delivered "Good practice" set already watches
   fines, hotel extras, alcohol and travel class on every expense. Add your
   own ceilings, duplicate it for a customer, or archive it
   (*Configuration → Expense Rules*).
4. **Expense sheet**: select expenses, then the *Expense Sheet* button. The
   delivered Excel template, "Expense sheet (basic template)", can be
   downloaded, adapted and uploaded again (*Configuration → Export
   Templates*).
5. **Projects** (optional): tick *Re-invoice expenses to a project* in the
   settings.

A database created with demo data also holds a made-up customer, its project
and its own requirements.

---

## Projects, expense sheets and expense rules

- **Project and task**: "Re-invoice: Yes" puts the expense on the project's
  sales order; "No" can keep the project, for tracking only. The expense is
  charged to the project's analytic account (created if needed): it shows
  under the project's **Expenses** button, and in its profitability once
  posted. The **Task** field only shows if the project has open tasks; the
  first one, in the project's order, is suggested.
- **Expense sheet** (button in place of "Print" on the list): PDF summary
  with tax by rate and numbered receipts, Excel export on a template
  (*Configuration → Export Templates*), one sheet per employee, gathered in
  an archive when there are several.
- **Expense rules** (*Configuration → Expense Rules*): ceilings per meal, per
  day or per night, words and rental categories to watch. The warning shows
  at the top of the expense and can be filtered ("Rule breaches"). A set of
  rules applies to all expenses, to the projects of some customers, or to
  chosen projects.
- **Team and batches**: *My Team's Expenses* to enter expenses in a team
  member's name, and *Actions → Back to draft* on a selection.

---

## Country specifics

Nothing in the module is tied to one country; a few things are there for the
receipts of some countries:

- **French merchants**: many French receipts print the SIRET number but not
  the activity code. An administrator can load the public Sirene database of
  establishments (`expense.scan.sirene`), which then gives the activity of
  the merchant from its SIRET. Without it, the other clues are used.
- **Categories without recoverable VAT**: where VAT cannot be deducted on
  some expenses (employee accommodation or passenger transport in France,
  for instance), tick *Flat rate without VAT* on the category.
- **Mileage**: each employee can have their own rate per kilometre or mile,
  to follow the official scale of your country.

---

## Architecture

```
expense_scan/
├── ocr/                     ← independent of Odoo, testable on its own
│   ├── types.py             shared structures
│   ├── preprocess.py        EXIF, receipt detection, perspective, straightening
│   ├── engines.py           interchangeable engines (RapidOCR, Tesseract)
│   ├── parser.py            field extraction from a European receipt
│   ├── lexicon.py           receipt words, activity codes, brands
│   └── categorize.py        category clues, shared with the benchmark
├── models/
│   ├── hr_expense.py        scan chain, hooked on create_expense_from_attachments
│   ├── hr_expense_*.py      categories, pieces, projects, team, mileage, nights
│   ├── expense_sheet.py     expense sheets (PDF, Excel)
│   ├── expense_policy.py    expense rules
│   ├── res_company.py       settings
│   └── ...
├── static/src/              upload, mobile preview, split view, retouch
├── tools/                   quick check, benchmark, corpus download
└── tests/
```

The `ocr/` folder does not know Odoo: keywords and thresholds can be tuned,
and the parser tests run, without a database.

### Adding an engine

Write a subclass of `ScanEngine` (`availability()` and `recognize()`),
register it in `ENGINE_CLASSES`, add its code to the selection on
`res.company`. Nothing else to change: the preprocessing and the parser work
on the same list of positioned words.

---

## Known limits

- **Recognition model**: the module keeps the first set of models that reads
  its control receipt back. On `rapidocr` 3.9.2, the `latin` models load but
  recognise nothing; they are therefore skipped in favour of the library's
  default model, which reads accented Latin text without trouble. If a later
  version of the library fixes the latin models, they will be candidates
  again with nothing to change.
- **Item lines**: the module extracts the header and the total, not the item
  details. An Odoo expense has a single amount anyway.
- **Several tax rates** on the same receipt: the module keeps the exact sum
  of the taxes read and sets the highest rate on the expense; the accountant
  can correct it.
- **First scan after a restart**: each Odoo worker loads the models on its
  own, which adds one to three seconds. The warm-up button only covers the
  worker that handled the request.
- **PDF**: the first page is always read; if it gives no total, the next
  pages are read too (up to 5), to find it at the foot of a multi-page
  invoice. The image shown stays the first page.
- **Limits**: a receipt over 25 MB is refused; an image over 50 megapixels is
  scaled down while decoding (over 250 Mpx, refused); a PDF is rendered at a
  resolution that fits within the same limit, with a 30 s timeout.
- **Interrupted scan**: a scan "running" for more than 15 minutes — a receipt
  that got its worker killed — is no longer picked up; the expense goes to
  error and is entered by hand.

---

## Personal data

The text read on a receipt (names, addresses, last digits of a card...) is
kept on the expense for the scan and for linking neighbouring trips. A daily
task erases it from submitted expenses older than **Keep the text read for**
(10 years by default, the retention period of accounting records in France —
Commercial Code, art. L123-22; 0 = never). The image of the receipt stays:
it is the accounting record.

---

## Development

`tools/check.py` compiles the whole module in a second (run it before an
update). `tools/bench.py` replays the parsing on a snapshot of texts already
read, without Odoo, and compares file by file with a reference measure: this
is what allows changing the parser without regressions. Its instructions are
at the top of the file. The receipts, the snapshot and the truth tables are
personal data: they are never versioned.

---

## Tests

```bash
odoo -d <database> -i expense_scan --test-enable --test-tags /expense_scan --stop-after-init
```

The preprocessing tests skip themselves if OpenCV is not installed.

---

## Licence

LGPL-3, like Odoo Community.

## Supporting the project

The module is free and will stay free. If it saves you time, you can
[buy its author a coffee](https://github.com/sponsors/Ch0c0latine) — or, if
you prefer, remember it in your will.
