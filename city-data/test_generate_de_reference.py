"""Checks generate_de_reference.py with a few streets and municipalities.

    python -m unittest test_generate_de_reference
"""
import unittest

from cities import District, Ref
from generate_de_reference import base_name, build_reference, official_name


class ReferenceTest(unittest.TestCase):
    MUNICIPALITIES = {'06412000': ('Frankfurt am Main, Stadt', '60311'), '06415000': ('Hanau, Brüder-Grimm-Stadt', ''),
                      '01001000': ('Flensburg, Stadt', '24937'), '15085040': ('Blankenburg (Harz), Stadt', '38889')}

    def build(self, *streets):
        return build_reference([dict(PostalCode=p, Locality=n, RegionalKey=k, Borough=b, Suburb='')
                                for p, n, k, b in streets], self.MUNICIPALITIES)

    def test_should_count_streets_when_key_is_a_destatis_municipality(self):
        street = ('60311', 'Frankfurt am Main', '06412000', '')
        refs, _, _ = self.build(street, street)
        self.assertEqual([Ref('60311', 'Frankfurt am Main', '06412000', 'DE-HE', 2)],
                         [r for r in refs if r.key == '06412000'])

    def test_should_use_official_name_when_osm_shortens_it(self):
        refs, _, _ = self.build(('38889', 'Blankenburg', '15085040', ''))
        self.assertIn(Ref('38889', 'Blankenburg (Harz)', '15085040', 'DE-ST', 1), refs)

    def test_should_use_new_key_when_municipality_with_same_name_changed_its_key(self):
        refs, _, unknown = self.build(('63450', 'Hanau', '06435014', ''))
        self.assertIn(Ref('63450', 'Hanau', '06415000', 'DE-HE', 1), refs)
        self.assertFalse(unknown)

    def test_should_skip_entry_when_key_is_unknown_and_name_matches_no_municipality(self):
        _, _, unknown = self.build(('37444', 'Harz (LK Goslar)', '03153504', ''))
        self.assertEqual({'03153504': 1}, dict(unknown))

    def test_should_add_seat_postal_code_when_municipality_has_no_street(self):
        refs, _, _ = self.build()
        self.assertIn(Ref('24937', 'Flensburg', '01001000', 'DE-SH', 0), refs)

    def test_should_list_districts_when_they_differ_from_the_municipality_name(self):
        _, districts, _ = self.build(('60311', 'Frankfurt am Main', '06412000', 'Innenstadt'),
                                     ('60311', 'Frankfurt am Main', '06412000', 'Frankfurt am Main'))
        self.assertEqual([District('60311', 'Innenstadt', '06412000')], districts)


class OfficialNameTest(unittest.TestCase):
    def test_should_strip_status_when_destatis_appends_it_without_space(self):
        self.assertEqual('Ebstorf', base_name('Ebstorf,Klosterflecken'))

    def test_should_take_destatis_name_when_osm_is_outdated(self):
        self.assertEqual('Belgern-Schildau', official_name('Belgern', 'Belgern-Schildau', False))

    def test_should_keep_osm_name_when_destatis_abbreviates(self):
        self.assertEqual('Hermsdorf/Erzgebirge', official_name('Hermsdorf/Erzgebirge', 'Hermsdorf/Erzgeb.', False))

    def test_should_keep_osm_name_when_destatis_adds_the_sorbian_name(self):
        self.assertEqual('Kolkwitz', official_name('Kolkwitz', 'Kolkwitz/Gołkojce', False))
        self.assertEqual('Kamenz', official_name('Kamenz', 'Kamenz / Kamjenc', False))

    def test_should_keep_osm_name_when_destatis_name_repeats_in_the_state(self):
        self.assertEqual('Hamfelde (Lauenburg)', official_name('Hamfelde (Lauenburg)', 'Hamfelde', True))

    def test_should_keep_osm_name_when_it_only_adds_an_honorific_or_qualifier(self):
        self.assertEqual('Lutherstadt Eisleben', official_name('Lutherstadt Eisleben', 'Eisleben', False))
        self.assertEqual('Rohrbach an der Ilm', official_name('Rohrbach an der Ilm', 'Rohrbach', False))


if __name__ == '__main__':
    unittest.main()
