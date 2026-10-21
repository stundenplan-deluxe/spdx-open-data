"""Builds the German reference lists for cities.py from OpenPLZ and Destatis:

    data/de-reference.tsv   postal code -> municipality, with the official municipality name
    data/de-districts.tsv   postal code -> district (Ortsteil) -> municipality, to resolve rows named after a district

See README.md. Needs openpyxl for the Destatis workbook (pip install openpyxl).

    python generate_de_reference.py --streets streets.csv --gv AuszugGV3QAktuell.xlsx
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

from cities import District, Ref, spelling_key, write_districts, write_reference

STATES = {'01': 'SH', '02': 'HH', '03': 'NI', '04': 'HB', '05': 'NW', '06': 'HE', '07': 'RP', '08': 'BW',
          '09': 'BY', '10': 'SL', '11': 'BE', '12': 'BB', '13': 'MV', '14': 'SN', '15': 'ST', '16': 'TH'}
# Destatis Textkennzeichen of uninhabited unincorporated areas (gemeindefreie Gebiete). 65 is an inhabited one, 67 a
# Große Kreisstadt; both stay.
UNINCORPORATED = {'66'}


def region_of(key):
    return 'DE-' + STATES[key[:2]]


def read_destatis(path):
    """Municipality key -> (official name, postal code of the administration seat) from the Destatis GV extract."""
    import openpyxl  # only needed here, so the tests run on the standard library
    sheet = openpyxl.load_workbook(path, read_only=True).worksheets[1]
    municipalities = {}
    for r in sheet.iter_rows(values_only=True):
        if r[0] == '60' and r[1] not in UNINCORPORATED:
            municipalities[r[2] + r[3] + r[4] + r[6]] = (r[7], r[13] or '')
    return municipalities


def base_name(destatis_name):
    """'Blankenburg (Harz), Stadt' -> 'Blankenburg (Harz)': Destatis appends the status after a comma."""
    return re.split(r',\s*', destatis_name)[0]  # sometimes without the space: 'Ebstorf,Klosterflecken'


def official_name(osm, base, ambiguous):
    """The Destatis name, which OSM sometimes shortens ('Blankenburg' for 'Blankenburg (Harz)') or keeps outdated
    ('Belgern' for 'Belgern-Schildau'). OSM stays where Destatis
    - abbreviates ('Hermsdorf/Erzgeb.') or adds the Sorbian name ('Kamenz / Kamjenc', 'Kolkwitz/Gołkojce'),
    - repeats the name within the state ('Hamfelde'),
    - or OSM only adds something: an honorific in front ('Lutherstadt Eisleben' for 'Eisleben, Lutherstadt') or a
      qualifier behind ('Rohrbach an der Ilm' for 'Rohrbach')."""
    sorbian = '/' in base and spelling_key(base.split('/')[0]) == spelling_key(osm)
    adds = spelling_key(osm).startswith(spelling_key(base)) or spelling_key(osm).endswith(spelling_key(base))
    return osm if '.' in base or ' / ' in base or sorbian or ambiguous or adds else base


def resolve_key(key, locality, municipalities, by_name):
    """The current key: OSM keys that changed since the extract are mapped by name within the state (Hanau 2026)."""
    if key in municipalities:
        return key
    renamed = by_name.get((key[:2], spelling_key(locality)), [])
    return renamed[0] if len(renamed) == 1 else None


def build_reference(streets, municipalities):
    """Distinct (postal code, municipality) of the OpenPLZ street list, limited to Destatis keys; the weight is the
    number of streets. Municipalities without any street get their seat postal code. Also returns the districts:
    OpenPLZ borough and suburb names per postal code that are not the municipality's own name."""
    streets_per_entry, osm_names, districts, unknown = Counter(), {}, set(), Counter()
    by_name = defaultdict(list)
    for key, (name, _) in municipalities.items():
        by_name[(key[:2], spelling_key(base_name(name)))].append(key)
    for row in streets:
        key = resolve_key(row['RegionalKey'], row['Locality'], municipalities, by_name)
        if key is None:
            unknown[row['RegionalKey']] += 1
            continue
        streets_per_entry[(row['PostalCode'], key)] += 1
        osm_names[key] = row['Locality']
        districts.update((row['PostalCode'], d, key) for d in (row['Borough'], row['Suburb']) if d)
    names = {}
    for key, (name, _) in municipalities.items():
        base = base_name(name)
        ambiguous = len(by_name[(key[:2], spelling_key(base))]) > 1
        names[key] = official_name(osm_names[key], base, ambiguous) if key in osm_names else base
    refs = {Ref(p, names[k], k, region_of(k), c) for (p, k), c in streets_per_entry.items()}
    for key, (_, seat) in municipalities.items():
        if key not in osm_names and seat:
            refs.add(Ref(seat, names[key], key, region_of(key), 0))
    own = {(r.postal_code, spelling_key(r.name), r.key) for r in refs}
    found = {District(p, d, k) for p, d, k in districts if (p, spelling_key(d), k) not in own}
    return sorted(refs), sorted(found), unknown


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--streets', required=True, help='OpenPLZ src/de/osm/streets.csv')
    p.add_argument('--gv', required=True, help='Destatis Gemeindeverzeichnis quarterly extract, .xlsx')
    args = p.parse_args()
    with open(args.streets, encoding='utf-8') as f:
        refs, districts, unknown = build_reference(csv.DictReader(f), read_destatis(args.gv))
    sources = [
        f'Places: OpenPLZ API Data ({os.path.basename(args.streets)}), (c) OpenStreetMap contributors, ODbL 1.0.',
        f'Municipalities: Statistisches Bundesamt (Destatis), Gemeindeverzeichnis ({os.path.basename(args.gv)}).',
        'This file is made available under the Open Database License: https://opendatacommons.org/licenses/odbl/1-0/',
    ]
    write_reference(refs, 'DE', [
        'Postal code -> municipality (official name, key AGS) for Germany; weight = number of OSM streets.',
        'Generated by generate_de_reference.py.'] + sources)
    write_districts(districts, 'DE', [
        'Postal code -> district (OSM borough or suburb) -> municipality key (AGS) for Germany.',
        'Generated by generate_de_reference.py.'] + sources)
    print(f'{len(refs)} entries, {len(districts)} districts; skipped keys not in Destatis: {dict(unknown)}',
          file=sys.stderr)


if __name__ == '__main__':
    main()
