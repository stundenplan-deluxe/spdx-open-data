"""Checks cities.py with a small reference list and a synthetic production export.

    python -m unittest test_cities
"""
import os
import tempfile
import unittest
from unittest import mock

import cities
from cities import District, Override, Ref

SOURCE = 'openplz'

REFS = [
    Ref('60311', 'Frankfurt am Main', '06412000', 'DE-HE'),
    Ref('88316', 'Isny im Allgäu', '08436049', 'DE-BW'),
    Ref('80331', 'München', '09162000', 'DE-BY'),
    Ref('66386', 'St. Ingbert', '10045117', 'DE-SL'),
    Ref('70771', 'Leinfelden-Echterdingen', '08116077', 'DE-BW'),
    Ref('20095', 'Hamburg', '02000000', 'DE-HH', 50),
    Ref('21079', 'Hamburg', '02000000', 'DE-HH', 300),
    Ref('79761', 'Waldshut-Tiengen', '08337126', 'DE-BW'),
    Ref('79761', 'Dogern', '08337027', 'DE-BW'),
    Ref('27607', 'Geestland', '03352011', 'DE-NI'),
    Ref('27999', 'Langen', '03454031', 'DE-NI'),
    Ref('02708', 'Löbau', '14626290', 'DE-SN'),
]
DISTRICTS = [District('79761', 'Tiengen', '08337126'), District('27607', 'Langen', '03352011'),
             District('02708', 'Ebersdorf', '14626290')]
OVERRIDES = [Override('Trier-Land', '54298', 'DE-RP', '08337027', 'test')]
REFERENCE = cities.Reference(REFS, DISTRICTS, OVERRIDES)


def row(id, name, region, postal_code, institutions='0', users='0', source='NULL', key='NULL'):
    return dict(id=str(id), name=name, region_code=region, postal_code=postal_code, source=source,
                external_reference=key, institutions=institutions, users=users)


def decide(*rows, reference=REFERENCE):
    return {d.row['id']: d for d in cities.plan(list(rows), reference, SOURCE)}


def applied(decisions, reference=REFERENCE):
    """The rows as they are after running the import SQL: tagged, renamed and inserted."""
    rows = []
    for d in decisions:
        r = dict(d.row)
        if d.action in ('tag', 'rename'):
            r.update(name=d.target.name, source=SOURCE, external_reference=d.target.key)
        rows.append(r)
    for i, ref in enumerate(cities.missing(decisions, reference)):
        rows.append(row(1000 + i, ref.name, ref.region, ref.postal_code, source=SOURCE, key=ref.key))
    return rows


class ClassifyTest(unittest.TestCase):
    def test_should_tag_row_when_name_postal_code_and_region_match(self):
        d = decide(row(1, 'Frankfurt am Main', 'DE-HE', '60311'))['1']
        self.assertEqual(('match', 'tag', '06412000'), (d.kind, d.action, d.target.key))

    def test_should_keep_row_when_it_was_imported_already(self):
        d = decide(row(1, 'Frankfurt am Main', 'DE-HE', '60311', source=SOURCE, key='06412000'))['1']
        self.assertEqual('keep', d.action)

    def test_should_rename_row_when_name_differs_only_by_case_or_accents(self):
        d = decide(row(1, 'munchen', 'DE-BY', '80331'))['1']
        self.assertEqual(('spelling', 'rename', 'München'), (d.kind, d.action, d.target.name))

    def test_should_rename_only_one_row_when_two_differ_from_the_entry_only_by_case_or_accents(self):
        by = decide(row(1, 'münchen', 'DE-BY', '80331', users='1'), row(2, 'Munchen', 'DE-BY', '80331'))
        self.assertEqual(('merge', 'rename'), (by['1'].action, by['2'].action))
        self.assertEqual({'München'}, {d.target.name for d in by.values()})
        self.assertNotIn('München', [r.name for r in cities.missing(list(by.values()), REFERENCE)])

    def test_should_merge_referenced_row_when_name_is_a_spelling_variant(self):
        d = decide(row(1, 'Sankt Ingbert', 'DE-SL', '66386', institutions='5'))['1']
        self.assertEqual(('spelling', 'merge', 'St. Ingbert'), (d.kind, d.action, d.target.name))

    def test_should_delete_unreferenced_row_when_name_is_a_shortened_form(self):
        d = decide(row(1, 'Isny', 'DE-BW', '88316'))['1']
        self.assertEqual(('variant', 'delete', 'Isny im Allgäu'), (d.kind, d.action, d.target.name))

    def test_should_merge_referenced_row_when_region_is_wrong(self):
        d = decide(row(1, 'Hamburg', 'DE-SH', '20095', users='1'))['1']
        self.assertEqual(('region', 'merge', 'DE-HH'), (d.kind, d.action, d.target.region))

    def test_should_delete_unreferenced_row_when_postal_code_and_name_are_unknown(self):
        d = decide(row(1, 'Atlantis', 'DE-HE', '00000'))['1']
        self.assertEqual(('unknown-postal-code', 'delete', None), (d.kind, d.action, d.target))


class LocateTest(unittest.TestCase):
    def test_should_merge_into_municipality_when_override_names_it(self):
        d = decide(row(1, 'Trier-Land', 'DE-RP', '54298', institutions='1'))['1']
        self.assertEqual(('override', 'merge', 'Dogern', 'test'), (d.kind, d.action, d.target.name, d.hint))

    def test_should_merge_into_municipality_when_name_is_its_district_at_the_postal_code(self):
        d = decide(row(1, 'Tiengen', 'DE-BW', '79761', institutions='1'))['1']
        self.assertEqual(('district', 'merge', 'Waldshut-Tiengen'), (d.kind, d.action, d.target.name))

    def test_should_prefer_district_when_a_municipality_of_that_name_is_in_the_same_postal_region(self):
        d = decide(row(1, 'Langen', 'DE-NI', '27607', institutions='1'))['1']
        self.assertEqual('Geestland', d.target.name)

    def test_should_move_to_lowest_postal_code_when_municipality_of_that_name_has_another_one(self):
        d = decide(row(1, 'Hamburg', 'DE-HH', '20038', institutions='225'))['1']
        self.assertEqual(('moved', 'merge', '20095'), (d.kind, d.action, d.target.postal_code))

    def test_should_not_move_when_municipality_of_that_name_is_in_another_postal_region(self):
        d = decide(row(1, 'Frankfurt am Main', 'DE-HE', '10999', users='1'))['1']
        self.assertEqual(('unknown-postal-code', 'flag'), (d.kind, d.action))

    def test_should_merge_into_only_municipality_when_postal_code_has_one(self):
        d = decide(row(1, 'Echterdingen', 'DE-BW', '70771', institutions='1'))['1']
        self.assertEqual(('district', 'merge', 'Leinfelden-Echterdingen'), (d.kind, d.action, d.target.name))

    def test_should_merge_into_municipality_when_name_is_its_district_elsewhere_in_the_region(self):
        d = decide(row(1, 'Ebersdorf', 'DE-SN', '02799', institutions='1'))['1']
        self.assertEqual(('district', 'merge', 'Löbau'), (d.kind, d.action, d.target.name))

    def test_should_flag_referenced_row_when_postal_code_has_several_municipalities_and_no_district(self):
        d = decide(row(1, 'Unbekannt', 'DE-BW', '79761', institutions='1'))['1']
        self.assertEqual(('district', 'flag', None), (d.kind, d.action, d.target))
        self.assertIn('Dogern, Waldshut-Tiengen', d.hint)


class ImportedRowTest(unittest.TestCase):
    def test_should_rename_imported_row_when_its_entry_got_the_official_name(self):
        d = decide(row(1, 'Isny', 'DE-BW', '88316', source=SOURCE, key='08436049'))['1']
        self.assertEqual(('renamed', 'rename', 'Isny im Allgäu'), (d.kind, d.action, d.target.name))

    def test_should_supersede_imported_row_when_a_user_row_has_the_official_name(self):
        ds = decide(row(1, 'Isny', 'DE-BW', '88316', institutions='1', source=SOURCE, key='08436049'),
                    row(2, 'Isny im Allgäu', 'DE-BW', '88316', institutions='3'))
        self.assertEqual(('match', 'tag'), (ds['2'].kind, ds['2'].action))
        self.assertEqual(('superseded', 'merge'), (ds['1'].kind, ds['1'].action))

    def test_should_flag_imported_row_when_its_municipality_is_no_longer_in_the_reference(self):
        d = decide(row(1, 'Isny', 'DE-BW', '88316', source=SOURCE, key='99999999'))['1']
        self.assertEqual('flag', d.action)


class ImportTest(unittest.TestCase):
    ROWS = [row(1, 'Frankfurt am Main', 'DE-HE', '60311'), row(2, 'munchen', 'DE-BY', '80331'),
            row(3, 'Isny', 'DE-BW', '88316', institutions='2'),
            row(4, 'Leinfelden', 'DE-BW', '70771', source=SOURCE, key='08116077')]

    def test_should_insert_only_reference_entries_no_row_carries(self):
        missing = cities.missing(cities.plan(self.ROWS, REFERENCE, SOURCE), REFERENCE)
        self.assertEqual(['Isny im Allgäu', 'St. Ingbert', 'Hamburg', 'Hamburg', 'Waldshut-Tiengen', 'Dogern',
                          'Geestland', 'Langen', 'Löbau'], [r.name for r in missing])

    def test_should_change_nothing_when_import_runs_a_second_time(self):
        again = cities.plan(applied(cities.plan(self.ROWS, REFERENCE, SOURCE)), REFERENCE, SOURCE)
        self.assertEqual([], cities.missing(again, REFERENCE))
        self.assertFalse([d for d in again if d.action in ('tag', 'rename')])

    def test_should_guard_every_statement_when_generating_import_sql(self):
        sql = cities.import_sql(cities.plan(self.ROWS, REFERENCE, SOURCE), REFERENCE, 'DE', SOURCE, creator_id=7)
        self.assertIn("UPDATE cities SET name = 'München', name_folded = 'munchen', source = 'openplz', "
                      "external_reference = '09162000' WHERE id = 2 AND source IS NULL;", sql)
        self.assertIn("UPDATE cities SET name = 'Leinfelden-Echterdingen', name_folded = 'leinfelden-echterdingen' "
                      "WHERE id = 4 AND source = 'openplz';", sql)
        self.assertIn("SELECT gen_random_uuid()::text, 'Isny im Allgäu', 'isny im allgau', 'DE', 'DE-BW', '88316', "
                      "7, 0, 'openplz', '08436049' WHERE NOT EXISTS", sql)
        self.assertTrue(sql.startswith('--') and sql.rstrip().endswith('COMMIT;'))


class WeakEntryTest(unittest.TestCase):
    REFERENCE = cities.Reference([
        Ref('01067', 'Dresden', '14612000', 'DE-SN', 400), Ref('01445', 'Dresden', '14612000', 'DE-SN', 2),
        Ref('01445', 'Radebeul', '14627210', 'DE-SN', 362), Ref('01468', 'Moritzburg', '14627170', 'DE-SN', 2)])

    def missing(self, *rows):
        decisions = cities.plan(list(rows), self.REFERENCE, SOURCE)
        return [(r.postal_code, r.name) for r in cities.missing(decisions, self.REFERENCE)]

    def test_should_not_insert_border_entry_when_municipality_has_a_stronger_postal_code(self):
        self.assertNotIn(('01445', 'Dresden'), self.missing())

    def test_should_insert_entry_when_it_is_the_only_one_of_its_municipality(self):
        self.assertIn(('01468', 'Moritzburg'), self.missing())

    def test_should_accept_border_entry_as_match_when_a_row_has_it(self):
        d = cities.plan([row(1, 'Dresden', 'DE-SN', '01445')], self.REFERENCE, SOURCE)[0]
        self.assertEqual('tag', d.action)

    def test_should_insert_border_entry_when_a_merge_needs_it(self):
        self.assertIn(('01445', 'Dresden'), self.missing(row(1, 'dresden.', 'DE-SN', '01445', institutions='1')))

    def test_should_move_to_strong_postal_code_when_the_lowest_one_is_weak(self):
        entry = self.REFERENCE.entry('14612000', '99999')
        self.assertEqual('01067', entry.postal_code)


class CleanupTest(unittest.TestCase):
    def test_should_repoint_references_before_deleting_when_merging(self):
        decisions = cities.plan([row(3, 'Isny', 'DE-BW', '88316', institutions='2')], REFERENCE, SOURCE)
        sql = cities.cleanup_sql(decisions, 'DE', SOURCE)
        update = sql.index('UPDATE institutions SET city_id = (SELECT MIN(id) FROM cities WHERE country_code = '
                           "'DE' AND source = 'openplz' AND external_reference = '08436049'")
        self.assertLess(update, sql.index('DELETE FROM cities WHERE id = 3 AND source IS NULL AND NOT EXISTS'))
        self.assertIn('UPDATE user_details SET city_id', sql)

    def test_should_delete_superseded_imported_row_only_with_its_source_when_merging(self):
        decisions = cities.plan([row(1, 'Isny', 'DE-BW', '88316', source=SOURCE, key='08436049'),
                                 row(2, 'Isny im Allgäu', 'DE-BW', '88316', institutions='3')], REFERENCE, SOURCE)
        self.assertIn("DELETE FROM cities WHERE id = 1 AND source = 'openplz' AND NOT EXISTS",
                      cities.cleanup_sql(decisions, 'DE', SOURCE))

    def test_should_not_touch_rows_when_they_match_or_are_flagged(self):
        decisions = cities.plan([row(1, 'Frankfurt am Main', 'DE-HE', '60311'),
                                 row(2, 'Unbekannt', 'DE-BW', '79761', institutions='1')], REFERENCE, SOURCE)
        sql = cities.cleanup_sql(decisions, 'DE', SOURCE)
        self.assertNotIn('DELETE', sql)
        self.assertNotIn('UPDATE institutions', sql)


class ExportTest(unittest.TestCase):
    def test_should_limit_export_to_country_when_generating_query(self):
        sql = cities.export_sql('AT')
        self.assertIn("WHERE c.country_code = 'AT'", sql)
        self.assertTrue(sql.rstrip().endswith('ORDER BY c.id;'))


class DataFileTest(unittest.TestCase):
    def test_should_read_back_entries_and_districts_when_they_were_written(self):
        refs = [Ref('79761', 'Waldshut-Tiengen', '08337126', 'DE-BW', 3), Ref('1010', 'Wien', '90001', 'AT-9')]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cities, 'HERE', tmp):
            os.mkdir(os.path.join(tmp, 'data'))
            cities.write_reference(refs, 'XX', ['header'])
            cities.write_districts(DISTRICTS, 'XX', ['header'])
            reference = cities.load_reference('XX')
        self.assertEqual(refs, reference.refs)
        self.assertEqual({'08337126'}, reference.districts_at[('79761', 'tiengen')])

    def test_should_load_overrides_when_the_country_has_an_overrides_file(self):
        reference = cities.load_reference('DE')
        self.assertTrue(reference.overrides)
        self.assertTrue(all(o.key in reference.by_key for o in reference.overrides.values()))


class QuoteTest(unittest.TestCase):
    def test_should_double_quotes_and_keep_backslashes_when_quoting(self):
        self.assertEqual("'St. Peter''s \\'", cities.q("St. Peter's \\"))


class NameFoldedTest(unittest.TestCase):
    """Must fold exactly like AccentFolding.fold in the backend, whose tests use the same examples."""

    def test_should_fold_like_the_backend_when_a_name_has_accents(self):
        self.assertEqual('munchen', cities.name_folded('München'))
        self.assertEqual('krakow', cities.name_folded('KRAKÓW'))
        self.assertEqual('lodz', cities.name_folded('Łódź'))
        self.assertEqual('strasse', cities.name_folded('Straße'))
        self.assertEqual('aeroskobing', cities.name_folded('Ærøskøbing'))
        self.assertEqual('coeur', cities.name_folded('Cœur'))
        self.assertEqual('munchen', cities.name_folded('Mu\u0308nchen'))

if __name__ == '__main__':
    unittest.main()
