#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Check the translation files (i18n/*.po) without Odoo or gettext.

Standard library only. For every file it reports, as ``file:line: [code] text``:

* file level: encoding, byte order mark, Windows line endings, header
  (Language matches the file name, UTF-8 charset, Plural-Forms when the file
  has plural entries), syntax errors, duplicates, fuzzy and obsolete entries;
* entry level: empty translations, translations identical to the source,
  placeholders that differ from the source (``%s``, ``%d``, ``%(name)s``,
  ``%%``, ``{0}``, ``{name}``), HTML tags and entities, leading and trailing
  white space, line breaks, ellipsis, final punctuation, label length;
* typography of a few languages: non-breaking space before ``: ; ! ? %`` in
  French, quotation marks in French, German and Polish, ``%`` spacing.

Errors (``E``) make the exit status non-zero; warnings (``W``) and notes
(``I``) do not unless ``--strict`` is given.

    python3 tools/check_po.py                    # every file of i18n/
    python3 tools/check_po.py i18n/fr.po         # one file
    python3 tools/check_po.py --statistics       # msgfmt-like counts
    python3 tools/check_po.py --strict --quiet   # for a pre-release check
"""
import argparse
import collections
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, 'i18n')

NBSP = ' '
NNBSP = ' '
THIN = (NBSP, NNBSP)

# Plural-Forms expected by language, for files that contain plural entries.
PLURALS = {
    'da': 2, 'de': 2, 'en': 2, 'es': 2, 'fr': 2, 'it': 2, 'nb': 2,
    'nl': 2, 'pl': 3, 'pt': 2, 'sv': 2,
}

PRINTF = re.compile(r'%(?:\([^)]*\))?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[hlL]?[sdifrxXeEgGcou%]')
BRACE = re.compile(r'\{[^{}]*\}')
TAG = re.compile(r'<[^<>]+>')
ENTITY = re.compile(r'&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);')
WORDS = re.compile(r'[^\W\d_]', re.UNICODE)


class Entry:
    """One message of a catalogue."""

    def __init__(self, line):
        self.line = line
        self.ctxt = None
        self.id = None
        self.plural = None
        self.strs = {}
        self.flags = set()
        self.refs = []
        self.obsolete = False

    @property
    def key(self):
        return (self.ctxt, self.id)

    def is_header(self):
        return self.id == '' and self.ctxt is None


def unescape(text):
    table = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\'}
    return re.sub(r'\\(.)', lambda m: table.get(m.group(1), '\\' + m.group(1)), text)


def parse(text, report):
    """Return the entries of a catalogue; syntax errors go to ``report``."""
    entries = []
    current = None
    target = None
    for number, raw in enumerate(text.split('\n'), 1):
        line = raw.rstrip('\r')
        stripped = line.strip()
        obsolete = stripped.startswith('#~')
        if obsolete:
            stripped = stripped[2:].strip()
        if not stripped:
            target = None
            continue
        if stripped.startswith('#'):
            # A comment after a msgstr opens the next entry.
            if current is not None and current.strs:
                entries.append(current)
                current = None
            if current is None:
                current = Entry(number)
            if stripped.startswith('#,'):
                current.flags.update(f.strip() for f in stripped[2:].split(','))
            elif stripped.startswith('#:'):
                current.refs.extend(stripped[2:].split())
            continue
        keyword, _, rest = stripped.partition(' ')
        if keyword in ('msgctxt', 'msgid') and current is not None and current.strs:
            entries.append(current)
            current = None
        if current is None:
            current = Entry(number)
        current.obsolete = current.obsolete or obsolete
        if keyword.startswith('"'):
            keyword, rest = None, stripped
        match = re.fullmatch(r'"(.*)"', rest.strip())
        if match is None:
            report(number, 'E', 'syntax', 'cannot read the line: %r' % line[:60])
            continue
        value = unescape(match.group(1))
        if keyword is None:
            if target is None:
                report(number, 'E', 'syntax', 'string without keyword')
            elif target == 'ctxt':
                current.ctxt += value
            elif target == 'id':
                current.id += value
            elif target == 'plural':
                current.plural += value
            else:
                current.strs[target] += value
        elif keyword == 'msgctxt':
            current.ctxt, target = value, 'ctxt'
        elif keyword == 'msgid':
            current.id, target = value, 'id'
        elif keyword == 'msgid_plural':
            current.plural, target = value, 'plural'
        elif keyword.startswith('msgstr'):
            index = 0
            found = re.fullmatch(r'msgstr\[(\d+)\]', keyword)
            if found:
                index = int(found.group(1))
            elif keyword != 'msgstr':
                report(number, 'E', 'syntax', 'unknown keyword %r' % keyword)
                continue
            current.strs[index] = value
            target = index
        else:
            report(number, 'E', 'syntax', 'unknown keyword %r' % keyword)
    if current is not None and (current.id is not None):
        entries.append(current)
    return entries


def header_fields(entry):
    fields = {}
    if entry is not None:
        for row in entry.strs.get(0, '').split('\n'):
            name, sep, value = row.partition(':')
            if sep:
                fields[name.strip()] = value.strip()
    return fields


def signature(pattern, text):
    return collections.Counter(pattern.findall(text))


def tags_of(text):
    """Tags with their attributes, in order; text attributes are translated."""
    tags = []
    for tag in TAG.findall(text):
        tag = re.sub(r'\s(title|alt|aria-label|placeholder)="[^"]*"', lambda m: ' %s=""' % m.group(1), tag)
        tags.append(re.sub(r'\s+', ' ', tag.strip()))
    return tags


def percent_spacing(text):
    """Kinds of space found between a number or ``%s``/``%d`` and ``%``."""
    kinds = []
    for match in re.finditer(r'(?:\d|%[sd]|%\([^)]*\)[sd]|\})([   ]?)%(?!%|\()(?![sdifr(])', text):
        space = match.group(1)
        kinds.append('none' if not space else 'plain' if space == ' ' else 'nbsp')
    # "%s %%" : the doubled sign is the literal percent in a printf string.
    for match in re.finditer(r'(?:\d|%[sd]|%\([^)]*\)[sd])([   ]?)%%', text):
        space = match.group(1)
        kinds.append('none' if not space else 'plain' if space == ' ' else 'nbsp')
    return kinds


def check_entry(entry, lang, emit, options):
    """Compare every translation of an entry with its source."""
    source = entry.id
    for index, target in sorted(entry.strs.items()):
        origin = entry.plural if index and entry.plural is not None else source
        line = entry.line
        if target == '':
            emit(line, 'W', 'empty', 'not translated: %r' % origin[:60])
            continue
        if 'fuzzy' in entry.flags:
            emit(line, 'W', 'fuzzy', 'fuzzy entry: %r' % origin[:60])
        if target == origin and len(WORDS.findall(origin)) > 1:
            emit(line, 'I', 'same', 'identical to the source: %r' % origin[:60])
        label = origin[:50]

        # Placeholders.
        if signature(PRINTF, origin) != signature(PRINTF, target):
            emit(line, 'E', 'printf', 'format placeholders differ: %r -> %r' % (
                sorted(signature(PRINTF, origin).elements()), sorted(signature(PRINTF, target).elements())))
        if signature(BRACE, origin) != signature(BRACE, target):
            emit(line, 'E', 'brace', 'braces differ: %r -> %r (%s)' % (
                sorted(signature(BRACE, origin).elements()), sorted(signature(BRACE, target).elements()), label))
        if tags_of(origin) != tags_of(target):
            emit(line, 'E', 'tags', 'HTML tags differ: %r -> %r' % (tags_of(origin), tags_of(target)))
        if signature(ENTITY, origin) != signature(ENTITY, target):
            emit(line, 'E', 'entity', 'HTML entities differ (%s)' % label)

        # White space.
        for name, pick in (('leading', lambda s: s[:len(s) - len(s.lstrip())]),
                           ('trailing', lambda s: s[len(s.rstrip()):])):
            if pick(origin) != pick(target):
                emit(line, 'E', 'space', '%s white space differs: %r -> %r (%s)' % (
                    name, pick(origin), pick(target), label))
        if origin.count('\n') != target.count('\n'):
            emit(line, 'W', 'newline', 'line breaks differ: %d -> %d (%s)' % (
                origin.count('\n'), target.count('\n'), label))
        if '  ' in target.strip() and '  ' not in origin.strip():
            emit(line, 'W', 'space', 'double space in the translation (%s)' % label)
        if '\t' in target and '\t' not in origin:
            emit(line, 'W', 'space', 'tab in the translation (%s)' % label)

        # Ellipsis and final punctuation.
        body, tbody = origin.rstrip(), target.rstrip()
        if body.endswith(('…', '...')) != tbody.endswith(('…', '...')):
            emit(line, 'W', 'ellipsis', 'ellipsis lost or added: %r -> %r' % (body[-12:], tbody[-12:]))
        elif body.endswith('...') and tbody.endswith('…') or body.endswith('…') and tbody.endswith('...'):
            emit(line, 'I', 'ellipsis', 'ellipsis written differently: %r -> %r' % (body[-12:], tbody[-12:]))
        for mark in (':', '.', '?', '!'):
            if body.endswith(mark) != tbody.rstrip(NBSP + NNBSP + ' ').endswith(mark) \
                    and not body.endswith(('…', '...')) and not tbody.endswith(('…', '...')):
                emit(line, 'W', 'punct', 'final %r differs: %r -> %r' % (mark, body[-14:], tbody[-14:]))

        # Label length (columns, buttons).
        if len(origin) <= options.label and len(target) > options.label + options.label // 3 \
                and '\n' not in origin and len(origin.split()) <= 4:
            emit(line, 'I', 'length', 'translation of a short label is long (%d > %d): %r -> %r' % (
                len(target), len(origin), origin, target))

        typography(entry, lang, target, emit)


def typography(entry, lang, target, emit):
    line = entry.line
    if lang == 'fr':
        for match in re.finditer(r'(?<![\d/:.])(\s?)([:;!?])(?=\s|$)', target):
            before = match.group(1)
            # Skip a time or a URL: digits around the colon.
            if before in THIN:
                continue
            if match.group(2) == ':' and re.search(r'\d:$', target[:match.end()]):
                continue
            emit(line, 'W', 'fr-space', 'French: no-break space expected before %r: %r' % (
                match.group(2), target[max(0, match.start() - 14):match.end()]))
        for match in re.finditer(r'«(.?)|(.?)»', target):
            side = match.group(1) if match.group(1) is not None else match.group(2)
            if side not in THIN:
                emit(line, 'W', 'fr-quote', 'French: no-break space expected inside the guillemets: %r' % target[:50])
                break
    if '"' in target and lang in ('fr', 'de', 'pl'):
        if '"' not in entry.id:
            emit(line, 'W', 'quote', 'straight double quotes in a %s text where the source has none: %r' % (
                lang, target[:50]))
    if lang == 'pl' and re.search(r'[“«»]', target):
        emit(line, 'W', 'pl-quote', 'Polish quotation marks are „…”: %r' % target[:50])
    if lang == 'de' and re.search(r'“[^”]*”|«', target):
        emit(line, 'W', 'de-quote', 'German quotation marks are „…“ or »…«: %r' % target[:50])
    if lang == 'fr' and re.search(r'[„“”]', target):
        emit(line, 'W', 'fr-quote', 'French quotation marks are « … »: %r' % target[:50])


def check_file(path, options):
    """Run every check on one file; return (problems, counters)."""
    problems = []
    lang = os.path.splitext(os.path.basename(path))[0]
    shown = os.path.relpath(path, ROOT)

    def emit(line, severity, code, text):
        problems.append((shown, line, severity, code, text))

    with open(path, 'rb') as handle:
        raw = handle.read()
    if raw.startswith(b'\xef\xbb\xbf'):
        emit(1, 'E', 'bom', 'byte order mark at the start of the file')
    if b'\r' in raw:
        emit(1, 'E', 'crlf', 'Windows line endings (%d)' % raw.count(b'\r\n'))
    if raw and not raw.endswith(b'\n'):
        emit(1, 'W', 'eof', 'no newline at the end of the file')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        emit(1, 'E', 'encoding', 'not valid UTF-8: %s' % error)
        return problems, collections.Counter()
    if '�' in text:
        emit(text[:text.index('�')].count('\n') + 1, 'E', 'encoding', 'replacement character U+FFFD in the file')
    for number, row in enumerate(text.split('\n'), 1):
        if row.rstrip('\r') != row.rstrip('\r').rstrip(' \t') and not row.startswith('"'):
            emit(number, 'W', 'trailing', 'trailing white space on a line')

    entries = parse(text, emit)
    header = next((e for e in entries if e.is_header()), None)
    fields = header_fields(header)
    if header is None:
        emit(1, 'E', 'header', 'no header entry')
    else:
        if fields.get('Language', '').replace('_', '-').split('-')[0].lower() != lang.split('_')[0].lower():
            emit(header.line, 'E', 'header', 'Language %r does not match the file name' % fields.get('Language'))
        charset = re.search(r'charset=([\w-]+)', fields.get('Content-Type', ''))
        if not charset or charset.group(1).upper() != 'UTF-8':
            emit(header.line, 'E', 'header', 'charset is not UTF-8: %r' % fields.get('Content-Type'))
        for name in ('Project-Id-Version',):
            if re.search(r'PACKAGE|FULL NAME|YEAR', fields.get(name, '')):
                emit(header.line, 'W', 'header', '%s still has its template value' % name)
        has_plural = any(e.plural is not None for e in entries)
        declared = re.search(r'nplurals\s*=\s*(\d+)', fields.get('Plural-Forms', ''))
        expected = PLURALS.get(lang.split('_')[0])
        if has_plural and not declared:
            emit(header.line, 'E', 'plural', 'plural entries but no Plural-Forms in the header')
        elif declared and expected and int(declared.group(1)) != expected:
            emit(header.line, 'E', 'plural', 'Plural-Forms declares %s forms, %s has %d' % (
                declared.group(1), lang, expected))
        elif not declared and options.plural_forms:
            emit(header.line, 'I', 'plural', 'Plural-Forms is empty (%d forms expected for %s)' % (expected or 0, lang))

    seen = {}
    counters = collections.Counter()
    for entry in entries:
        if entry.is_header():
            continue
        if entry.id is None:
            emit(entry.line, 'E', 'syntax', 'entry without msgid')
            continue
        if entry.obsolete:
            counters['obsolete'] += 1
            emit(entry.line, 'W', 'obsolete', 'obsolete entry: %r' % entry.id[:60])
            continue
        if entry.key in seen:
            emit(entry.line, 'E', 'duplicate', 'duplicate of line %d: %r' % (seen[entry.key], entry.id[:60]))
        seen.setdefault(entry.key, entry.line)
        if 'fuzzy' in entry.flags:
            counters['fuzzy'] += 1
        elif all(entry.strs.get(i, '') != '' for i in range(max(entry.strs or {0: ''}) + 1)) and entry.strs:
            counters['translated'] += 1
        else:
            counters['untranslated'] += 1
        if not entry.strs:
            emit(entry.line, 'E', 'syntax', 'entry without msgstr: %r' % entry.id[:60])
            continue
        check_entry(entry, lang, emit, options)
        for ref in entry.refs:
            match = re.match(r'code:addons/expense_scan/(.+?):\d+$', ref)
            if match and not os.path.exists(os.path.join(ROOT, match.group(1))):
                emit(entry.line, 'I', 'ref', 'reference to a missing file: %s' % ref)
        if entry.id.strip() == '' and entry.id != '':
            emit(entry.line, 'W', 'blank', 'source made of white space only')

    # % spacing, per language: the usage must be one and the same.
    kinds = collections.Counter()
    for entry in entries:
        if not entry.is_header() and not entry.obsolete:
            for target in entry.strs.values():
                kinds.update(percent_spacing(target))
    if len([k for k in kinds if kinds[k]]) > 1:
        emit(1, 'W', 'percent', '"%%" spacing is mixed (%s)' % ', '.join(
            '%s: %d' % item for item in sorted(kinds.items())))
    if options.statistics:
        emit(0, 'S', 'stats', '%d translated messages, %d fuzzy translations, %d untranslated messages' % (
            counters['translated'], counters['fuzzy'], counters['untranslated']))
    return problems, counters


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('files', nargs='*', help='files to check (default: i18n/*.po)')
    parser.add_argument('--statistics', action='store_true', help='print msgfmt-like counts')
    parser.add_argument('--strict', action='store_true', help='warnings also make the exit status non-zero')
    parser.add_argument('--quiet', action='store_true', help='hide the notes (I)')
    parser.add_argument('--label', type=int, default=25, help='short label length for the length note (default 25)')
    parser.add_argument('--plural-forms', action='store_true', help='note an empty Plural-Forms header')
    parser.add_argument('--only', help='comma separated codes to show (for example printf,tags)')
    options = parser.parse_args(argv)

    files = options.files or sorted(
        os.path.join(I18N, name) for name in os.listdir(I18N) if name.endswith('.po'))
    only = set(options.only.split(',')) if options.only else None
    worst = 0
    for path in files:
        problems, _counters = check_file(path, options)
        for shown, line, severity, code, text in problems:
            if only and code not in only:
                continue
            if severity == 'I' and options.quiet:
                continue
            if severity == 'S':
                print('%s: %s' % (shown, text))
                continue
            print('%s:%d: %s [%s] %s' % (shown, line, severity, code, text))
            worst = max(worst, {'I': 0, 'W': 1, 'E': 2}[severity])
    return 1 if worst == 2 or (options.strict and worst == 1) else 0


if __name__ == '__main__':
    sys.exit(main())
