# -*- coding: utf-8 -*-

"""Tests for the 'ihate' plugin"""

from __future__ import division, absolute_import, print_function

import unittest
from beets.library import Item
from beetsplug.radio_stream import playlist_generator
from beetsplug.radio_stream.settings import Rules


class TestLib:
    def __init__(self, items):
        self._items = items

    def items(self, query):
        return self._items


class PlaylistGeneratorTest(unittest.TestCase):

    def test_standard_rules(self):

        match_pattern = {}
        test_item = Item(
            genre=u'TestGenre',
            album=u'TestAlbum',
            artist=u'TestArtist')

        lib = TestLib([test_item])
        rules = Rules()

        playlist_generator.generate_playlist(lib, rules, 100, False)



def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)

if __name__ == '__main__':
    unittest.main(defaultTest='suite')
