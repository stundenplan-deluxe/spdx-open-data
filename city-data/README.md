# City data

Reference lists of **postal code -> place -> municipality** that the Stundenplan Deluxe apps and website use for
their city data, together with the scripts that build them. The lists are derived from open databases; this
directory publishes the derived database and the method under the licenses of its sources (share-alike).

## Germany

| File | Content | Rows |
|---|---|---|
| `data/de-reference.tsv` | One entry per (postal code, municipality): postal code, official municipality name, municipality key (AGS), state (`region_code`, ISO 3166-2) and `weight` (number of OpenStreetMap streets backing the entry) | 13,229 entries for 10,749 municipalities |
| `data/de-districts.tsv` | Postal code -> district (OpenStreetMap borough or suburb) -> municipality key, used to resolve place names that are districts, not municipalities | generated |
| `data/de-overrides.tsv` | Hand-made decisions for place names no rule resolves (administrative unions, districts OpenStreetMap does not know, wrong postal codes), with the evidence as reason | hand-maintained |

### Sources

- **Places (postal code, place name, municipality key, streets):** [OpenPLZ API Data](https://github.com/openpotato/openplzapi.data),
  file `src/de/osm/streets.csv`, an extract of [OpenStreetMap](https://www.openstreetmap.org) data (file of 2025-11-22).
  © OpenStreetMap contributors, [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
- **Municipalities (set of current municipalities, official names, seat postal codes):** Statistisches Bundesamt
  (Destatis), [Gemeindeverzeichnis](https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/_inhalt.html),
  quarterly extract `AuszugGV3QAktuell.xlsx` of 30.09.2026,
  [Datenlizenz Deutschland – Namensnennung – Version 2.0](https://www.govdata.de/dl-de/by-2-0).

### Rules

- One entry per (postal code, municipality); districts (Ortsteile) are not entries of their own.
- The name is the official Destatis name without its status suffix (`Blankenburg (Harz), Stadt` ->
  `Blankenburg (Harz)`). The OpenStreetMap name stays where Destatis abbreviates (`Hermsdorf/Erzgeb.`), adds the
  Sorbian name (`Kolkwitz/Gołkojce`), repeats the name within the state, or OpenStreetMap only adds an honorific or
  qualifier (`Lutherstadt Eisleben`, `Rohrbach an der Ilm`).
- `region_code` is `DE-` plus the state from the first two digits of the municipality key.
- Unincorporated areas (Destatis Textkennzeichen 66) are left out; inhabited ones (65) and Große Kreisstädte (67)
  are kept.
- A municipality without any street in the extract gets its administrative seat's postal code with weight 0.
- A street on a municipal border is listed under the neighbour's postal code as well, so a municipality can have
  entries backed by only one or two streets (`weight`). Consumers may treat entries with a weight of at most 2 as
  weak when the municipality has a better-backed postal code.
- A municipality key that is no longer in Destatis is mapped to the current key when exactly one municipality of the
  same name exists in the same state (Hanau left the Main-Kinzig-Kreis in 2026); other unknown keys are skipped.

Known shortcomings: names follow OpenStreetMap spelling (`St.`, not `Sankt`); PO box and company postal codes are not
in the list; keys that changed after the OpenStreetMap extract are only recovered when the name stayed the same.

## Regenerating

`generate_de_reference.py` needs Python 3 and `openpyxl` for the Destatis workbook:

```bash
pip install openpyxl
curl -LO https://raw.githubusercontent.com/openpotato/openplzapi.data/main/src/de/osm/streets.csv
curl -L -o AuszugGV3QAktuell.xlsx "https://www.destatis.de/DE/Themen/Laender-Regionen/Regionales/Gemeindeverzeichnis/Administrativ/Archiv/GVAuszugQ/AuszugGV3QAktuell.xlsx?__blob=publicationFile"
python generate_de_reference.py --streets streets.csv --gv AuszugGV3QAktuell.xlsx
```

`cities.py` holds the shared reading and writing of the lists, the name matching (`spelling_key`) and the tooling
that compares a database of city rows with a reference list and generates the SQL that aligns them. Its `export`,
`diff`, `sql` and `cleanup` commands target the Stundenplan Deluxe schema (`cities`, `institutions`, `user_details`)
and are published here as part of the method.

Tests (standard library only):

```bash
python -m unittest test_cities test_generate_de_reference
```

## Licenses and attribution

- The data files in `data/` are made available under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/) (`../LICENSE-ODbL.txt`), because
  they are derived from OpenStreetMap data. Any use of them has to attribute both sources:

  > © OpenStreetMap contributors, ODbL (via OpenPLZ API)
  > Statistisches Bundesamt (Destatis), Gemeindeverzeichnis, 2026

- The scripts (`*.py`) are licensed under the MIT License (`../LICENSE-MIT.txt`).
