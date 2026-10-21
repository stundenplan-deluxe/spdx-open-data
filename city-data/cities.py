"""Compare the city rows of one country in production with its reference list in data/ and generate the SQL that
aligns them. Country-independent: the reference list comes from a per-country generator (generate_de_reference.py).

See README.md for the full workflow. Standard library only.

    python cities.py export  --country DE > export.sql         # the read-only export query for that country
    python cities.py diff    --country DE --db export.tsv > report.tsv   # classifies every row, summary on stderr
    python cities.py sql     --country DE --db export.tsv --creator-id N > import.sql   # idempotent import
    python cities.py cleanup --country DE --db export.tsv > cleanup.sql  # delete / merge after review, never automatic
"""
import argparse
import csv
import difflib
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict, namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
# Value of cities.source per country, i.e. which register its reference list comes from.
SOURCES = {'DE': 'openplz', 'LV': 'varis', 'PL': 'geonames'}
MIN_PREFIX = 4
# An entry the source barely backs (DE: a municipality's border streets in its neighbour's postal code area) counts
# as a match but is not inserted when the municipality has a better-backed postal code.
WEAK_WEIGHT = 2
FUZZY = 0.85

# weight: how strongly the source backs the entry (DE: number of OSM streets); None when the source has no measure
Ref = namedtuple('Ref', 'postal_code name key region weight', defaults=(None,))
# A named part of a municipality (DE: Ortsteil) at a postal code; rows named after one belong to that municipality.
District = namedtuple('District', 'postal_code name key')
# A hand-made decision for a row no rule resolves: (name, postal code, region) belongs to the municipality `key`.
Override = namedtuple('Override', 'name postal_code region key reason')
# kind: match | spelling | variant | region | moved | district | override | unknown-postal-code   (rows from users)
#       renamed | superseded                                     (imported rows whose reference entry changed its name)
# action: keep (already imported) | tag | rename | delete | merge | flag
Decision = namedtuple('Decision', 'row kind action target hint')


def data_path(country, kind):
    return os.path.join(HERE, 'data', f'{country.lower()}-{kind}.tsv')


def write_tsv(path, header, columns, rows):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(f'# {line}\n' for line in header)
        f.write('\t'.join(columns) + '\n')
        f.writelines('\t'.join('' if v is None else str(v) for v in r) + '\n' for r in rows)


def read_tsv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader((line for line in f if not line.startswith('#')), delimiter='\t'))


def write_reference(refs, country, header):
    write_tsv(data_path(country, 'reference'), header,
              ['postal_code', 'name', 'municipality_key', 'region_code', 'weight'], refs)


def write_districts(districts, country, header):
    write_tsv(data_path(country, 'districts'), header, ['postal_code', 'name', 'municipality_key'], districts)


def load_reference(country):
    refs = [Ref(r['postal_code'], r['name'], r['municipality_key'], r['region_code'],
                int(r['weight']) if r['weight'] else None) for r in read_tsv(data_path(country, 'reference'))]
    districts = [District(r['postal_code'], r['name'], r['municipality_key'])
                 for r in read_tsv(data_path(country, 'districts'))]
    overrides = [Override(r['name'], r['postal_code'], r['region_code'], r['municipality_key'], r['reason'])
                 for r in read_tsv(data_path(country, 'overrides'))]
    return Reference(refs, districts, overrides)


class Reference:
    """The reference entries of one country, indexed for classification. Districts and overrides are optional."""

    def __init__(self, refs, districts=(), overrides=()):
        self.refs = refs
        self.by_postal, self.by_name, self.by_key = defaultdict(list), defaultdict(list), defaultdict(list)
        for r in refs:
            self.by_postal[r.postal_code].append(r)
            self.by_name[spelling_key(r.name)].append(r)
            self.by_key[r.key].append(r)
        self.strongest = {k: max(r.weight or 0 for r in rs) for k, rs in self.by_key.items()}
        region = {r.key: r.region for r in refs}
        self.districts_at, self.districts_in = defaultdict(set), defaultdict(set)
        for d in districts:
            if d.key in region:
                self.districts_at[(d.postal_code, spelling_key(d.name))].add(d.key)
                self.districts_in[(region[d.key], spelling_key(d.name))].add(d.key)
        self.overrides = {(o.name, o.postal_code, o.region): o for o in overrides}

    def __len__(self):
        return len(self.refs)

    def is_weak(self, ref):
        return ref.weight is not None and ref.weight <= WEAK_WEIGHT < self.strongest[ref.key]

    def entry(self, key, postal_code):
        """The municipality's entry at that postal code, else its lowest well-backed postal code (usually the centre:
        Hamburg 20095, not the 21079 of Harburg, which has more streets)."""
        entries = self.by_key[key]
        return next((r for r in entries if r.postal_code == postal_code), None) or \
            min((r for r in entries if not self.is_weak(r)), key=lambda r: r.postal_code)

    def locate(self, row):
        """The municipality a non-matching row belongs to, as (kind, key, hint), or None. In order:
        1. a hand-made override
        2. a district of that name at the postal code (before 3: 'Langen' at 27607 is a district of Geestland, not
           the municipality Langen in the Emsland)
        3. a municipality of that name in the same region and postal region (wrong or PO box postal code)
        4. the only municipality at the postal code
        5. a district of that name elsewhere in the region"""
        name, postal_code, region = row['name'], row['postal_code'], row['region_code']
        override = self.overrides.get((name, postal_code, region))
        if override:
            return 'override', override.key, override.reason
        # Same postal region (first two digits): 'Gersdorf' at 04703 is not the Gersdorf at 09355.
        same_name = {r.key for r in self.by_name.get(spelling_key(name), [])
                     if r.region == region and r.postal_code[:2] == postal_code[:2]}
        candidates = [
            ('district', self.districts_at.get((postal_code, spelling_key(name)), set()), 'district at postal code'),
            ('moved', same_name, 'municipality of that name'),
            ('district', {r.key for r in self.by_postal.get(postal_code, []) if r.region == region},
             'only municipality at postal code'),
            ('district', self.districts_in.get((region, spelling_key(name)), set()), 'district elsewhere in region'),
        ]
        return next(((kind, next(iter(keys)), hint) for kind, keys, hint in candidates if len(keys) == 1), None)


# --- classification --------------------------------------------------------------------------------------------

def fold_key(name):
    """Which names are the same city: case- and accent-insensitive, ß equals ss ('Koln' is 'Köln'). The database
    compares names exactly since the move to PostgreSQL, so this is a rule of the tool, not of the unique index: a row
    that differs from its reference entry only like this is renamed in place instead of getting a second row next to
    it. (MySQL's utf8mb4_0900_ai_ci compared the same way, which is where the rule comes from.)"""
    decomposed = unicodedata.normalize('NFKD', name.casefold().replace('ß', 'ss'))
    return ''.join(c for c in decomposed if not unicodedata.combining(c))


def spelling_key(name):
    """Ignores case, umlaut spelling, punctuation and spaces: 'Thalheim / Erzgebirge' == 'Thalheim/Erzgebirge'."""
    s = name.casefold().replace('ß', 'ss').replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue')
    s = re.sub(r'\bst\.', 'sankt', s)
    return re.sub(r'[^0-9a-z]', '', fold_key(s))


def is_variant(name, ref_name):
    """A shortened or slightly misspelled form: 'Isny' for 'Isny im Allgäu', 'Leinfelden' for
    'Leinfelden-Echterdingen'."""
    a, b = spelling_key(name), spelling_key(ref_name)
    return (len(a) >= MIN_PREFIX and b.startswith(a)) or difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY


def referenced(row):
    return row['institutions'] != '0' or row['users'] != '0'


def classify(row, reference):
    refs = reference.by_postal.get(row['postal_code'], [])
    same_region = [r for r in refs if r.region == row['region_code']]
    exact = [r for r in same_region if r.name == row['name']]
    if exact:
        return 'match', exact[0], ''
    if any(r.name == row['name'] for r in refs):
        return 'region', next(r for r in refs if r.name == row['name']), ''
    spelled = {r for r in same_region if fold_key(r.name) == fold_key(row['name'])} or \
        {r for r in same_region if spelling_key(r.name) == spelling_key(row['name'])}
    if len(spelled) == 1:
        return 'spelling', spelled.pop(), ''
    variants = {r for r in same_region if is_variant(row['name'], r.name)}
    if len(variants) == 1:
        return 'variant', variants.pop(), ''
    located = reference.locate(row)
    if located:
        kind, key, hint = located
        return kind, reference.entry(key, row['postal_code']), hint
    if refs:
        names = sorted({r.name for r in refs})
        return 'district', None, f'postal code belongs to {", ".join(names[:4])}'
    return 'unknown-postal-code', None, ''


def imported_entry(row, reference):
    """The current reference entry of an imported row: same postal code, same municipality key."""
    return next((r for r in reference.by_postal.get(row['postal_code'], [])
                 if r.key == row['external_reference']), None)


def plan(rows, reference, source):
    """One decision per exported row. Deletes and merges only ever happen in the reviewed cleanup script."""
    # Rows that are the same city as a reference entry apart from case and accents: one of them is renamed to the
    # reference name, the others are merged or deleted like any non-matching row.
    present = {(r['postal_code'], r['region_code'], fold_key(r['name'])): r for r in rows}
    decisions, imported = {}, []
    for row in rows:
        kind, target, hint = classify(row, reference)
        if kind == 'match':
            action = 'keep' if row['source'] == source else 'tag'
        elif row['source'] != 'NULL':
            imported.append(row)  # decided below, once it is known which entries other rows carry
            continue
        elif kind == 'spelling' and present.get((target.postal_code, target.region,
                                                 fold_key(target.name))) is row:
            action = 'rename'
        elif target is not None:
            action = 'merge' if referenced(row) else 'delete'
        else:
            action = 'flag' if referenced(row) else 'delete'
        decisions[row['id']] = Decision(row, kind, action, target, hint)
    carried = {d.target for d in decisions.values() if d.action in ('keep', 'tag', 'rename')}
    for row in imported:
        target = imported_entry(row, reference)
        if target is None:
            # no longer in the reference at all: a person decides
            decisions[row['id']] = Decision(row, 'district', 'flag', None, 'imported, no longer in the reference')
        elif target in carried:
            # the entry was renamed (official name) and a user row already has the new name: keep that row
            decisions[row['id']] = Decision(row, 'superseded', 'merge' if referenced(row) else 'delete', target, '')
        else:
            carried.add(target)
            decisions[row['id']] = Decision(row, 'renamed', 'rename', target, f'was {row["name"]}')
    return [decisions[row['id']] for row in rows]


def missing(decisions, reference):
    """Reference entries no row will carry after the import (neither exact nor renamed). Weak border entries are
    left out unless a merge needs them as its target."""
    covered = {(d.target.postal_code, d.target.region, d.target.name)
               for d in decisions if d.action in ('keep', 'tag', 'rename')}
    merge_targets = {d.target for d in decisions if d.action == 'merge'}
    return [r for r in reference.refs if (r.postal_code, r.region, r.name) not in covered
            and (r in merge_targets or not reference.is_weak(r))]


# --- output ----------------------------------------------------------------------------------------------------

def load_db(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter='\t'))


# What the backend stores in name_folded (AccentFolding.kt in :institution): every SQL that writes a name writes it
# too, or the row cannot be found by the search. The column is NOT NULL, so an INSERT without it fails.
NAME_FOLDING_EXTRA = {'ł': 'l', 'ø': 'o', 'đ': 'd', 'ð': 'd', 'ħ': 'h', 'ı': 'i', 'ŧ': 't', 'ŀ': 'l'}
NAME_FOLDING_RANGES = ((0x00C0, 0x024F), (0x1E00, 0x1EFF))


def _plain_letter(c):
    if c in NAME_FOLDING_EXTRA:
        return NAME_FOLDING_EXTRA[c]
    if not any(lo <= ord(c) <= hi for lo, hi in NAME_FOLDING_RANGES):
        return c
    d = unicodedata.normalize('NFD', c)
    plain = len(d) > 1 and 'a' <= d[0] <= 'z' and all(unicodedata.combining(m) for m in d[1:])
    return d[0] if plain else c


def name_folded(name):
    """Lower case, Latin accents removed, ß/æ/œ spelled out: 'Straße Kraków' -> 'strasse krakow'."""
    s = unicodedata.normalize('NFC', name).lower()
    for letter, spelled in (('ß', 'ss'), ('æ', 'ae'), ('œ', 'oe')):
        s = s.replace(letter, spelled)
    return ''.join(_plain_letter(c) for c in s)


def q(s):
    """A string literal; PostgreSQL takes backslashes literally (standard_conforming_strings)."""
    return "'" + s.replace("'", "''") + "'"


def report(decisions, out):
    w = csv.writer(out, delimiter='\t', lineterminator='\n')
    w.writerow(['id', 'name', 'region_code', 'postal_code', 'kind', 'action', 'target_name', 'municipality_key',
                'institutions', 'users', 'hint'])
    for d in decisions:
        r, t = d.row, d.target
        w.writerow([r['id'], r['name'], r['region_code'], r['postal_code'], d.kind, d.action,
                    t.name if t else '', t.key if t else '', r['institutions'], r['users'], d.hint])


def summary(decisions, refs):
    lines = [f'{len(decisions)} rows, {len(refs)} reference entries, '
             f'{len(missing(decisions, refs))} reference entries to insert']
    for label, key in (('kind', lambda d: d.kind), ('action', lambda d: d.action)):
        counts = Counter(key(d) for d in decisions)
        lines.append(f'by {label}: ' + ', '.join(f'{k} {v}' for k, v in counts.most_common()))
    per_state = defaultdict(Counter)
    for d in decisions:
        per_state[d.row['region_code']][d.kind] += 1
    lines += [f'  {state}: ' + ', '.join(f'{k} {v}' for k, v in c.most_common())
              for state, c in sorted(per_state.items())]
    return '\n'.join(lines)


def export_sql(country):
    return f'''-- Exports every {country} city row with the references that decide whether it may be deleted, as TSV.
-- Read-only. Generated by tools/city-data/cities.py export.
SELECT c.id, c.uuid, c.name, c.region_code, c.postal_code, c.source, c.external_reference, c.creator_id,
       c.institution_count, COALESCE(i.total, 0) AS institutions, COALESCE(i.active, 0) AS active_institutions,
       COALESCE(u.total, 0) AS users, c.last_verified
FROM cities c
LEFT JOIN (SELECT city_id, COUNT(*) AS total, COUNT(*) FILTER (WHERE NOT deleted) AS active
           FROM institutions GROUP BY city_id) i
       ON i.city_id = c.id
LEFT JOIN (SELECT city_id, COUNT(*) AS total FROM user_details WHERE city_id IS NOT NULL GROUP BY city_id) u
       ON u.city_id = c.id
WHERE c.country_code = {q(country)}
ORDER BY c.id;
'''


def import_sql(decisions, refs, country, source, creator_id):
    """Tags exact matches, fixes case/accent-only spellings in place and inserts the rest. Re-running is a no-op:
    every statement is guarded by the state it changes."""
    out = [f'-- Generated by tools/city-data/cities.py sql. Imports the reference list into cities ({country}).',
           'BEGIN;']
    for d in decisions:
        if d.action in ('tag', 'rename'):
            t = d.target
            if d.kind == 'renamed':
                out.append(f'UPDATE cities SET name = {q(t.name)}, name_folded = {q(name_folded(t.name))} '
                           f'WHERE id = {d.row["id"]} AND source = {q(source)};')
            else:
                out.append(f'UPDATE cities SET name = {q(t.name)}, name_folded = {q(name_folded(t.name))}, '
                           f'source = {q(source)}, '
                           f'external_reference = {q(t.key)} WHERE id = {d.row["id"]} AND source IS NULL;')
    for r in missing(decisions, refs):
        out.append('INSERT INTO cities (uuid, name, name_folded, country_code, region_code, postal_code, creator_id, '
                   'institution_count, source, external_reference) '
                   f'SELECT gen_random_uuid()::text, {q(r.name)}, {q(name_folded(r.name))}, {q(country)}, '
                   f'{q(r.region)}, {q(r.postal_code)}, {creator_id}, 0, '
                   f'{q(source)}, {q(r.key)} WHERE NOT EXISTS (SELECT 1 FROM cities WHERE country_code = '
                   f'{q(country)} AND region_code = {q(r.region)} AND postal_code = {q(r.postal_code)} '
                   f'AND name = {q(r.name)});')
    out.append('COMMIT;')
    return '\n'.join(out) + '\n'


def cleanup_sql(decisions, country, source):
    """Deletes unreferenced rows that are not in the reference and merges referenced ones into their reference row.
    Every delete re-checks for references at run time; the foreign keys would refuse it anyway."""
    out = ['-- Generated by tools/city-data/cities.py cleanup. Review before running; run the import first.',
           'BEGIN;']
    for d in (d for d in decisions if d.action in ('merge', 'delete')):
        r, t = d.row, d.target
        out.append(f'-- {d.action} {d.kind}: {r["name"]} {r["postal_code"]} {r["region_code"]}'
                   + (f' -> {t.name} ({t.key})' if t else ''))
        if d.action == 'merge':
            # A missing target yields NULL, which city_id refuses, so the transaction stops instead of losing data.
            target = (f'(SELECT MIN(id) FROM cities WHERE country_code = {q(country)} AND source = {q(source)} AND '
                      f'external_reference = {q(t.key)} AND postal_code = {q(t.postal_code)} AND name = {q(t.name)})')
            out.append(f'UPDATE institutions SET city_id = {target} WHERE city_id = {r["id"]};')
            out.append(f'UPDATE user_details SET city_id = {target} WHERE city_id = {r["id"]};')
        # Imported rows are only ever deleted as superseded by a user row that has the reference name.
        own_source = 'source IS NULL' if r['source'] == 'NULL' else f'source = {q(source)}'
        out.append(f'DELETE FROM cities WHERE id = {r["id"]} AND {own_source} '
                   f'AND NOT EXISTS (SELECT 1 FROM institutions WHERE city_id = {r["id"]}) '
                   f'AND NOT EXISTS (SELECT 1 FROM user_details WHERE city_id = {r["id"]});')
    out.append('UPDATE cities c SET institution_count = (SELECT COUNT(*) FROM institutions i '
               f'WHERE i.city_id = c.id AND i.deleted = FALSE) WHERE c.country_code = {q(country)};')
    out.append('COMMIT;')
    return '\n'.join(out) + '\n'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['export', 'diff', 'sql', 'cleanup'])
    p.add_argument('--country', required=True, choices=SOURCES, help='ISO alpha-2 code with a reference list in data/')
    p.add_argument('--db', help='TSV of the query printed by export')
    p.add_argument('--creator-id', type=int, help='user id recorded as creator of inserted rows (sql only)')
    args = p.parse_args()
    country, source = args.country, SOURCES[args.country]
    if args.command == 'export':
        sys.stdout.write(export_sql(country))
        return
    if not args.db:
        p.error(f'{args.command} needs --db')
    refs = load_reference(country)
    decisions = plan(load_db(args.db), refs, source)
    if args.command == 'diff':
        report(decisions, sys.stdout)
        print(summary(decisions, refs), file=sys.stderr)
    elif args.command == 'sql':
        if args.creator_id is None:
            p.error('sql needs --creator-id')
        sys.stdout.write(import_sql(decisions, refs, country, source, args.creator_id))
    else:
        sys.stdout.write(cleanup_sql(decisions, country, source))


if __name__ == '__main__':
    main()
