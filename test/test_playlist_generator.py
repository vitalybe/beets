# -*- coding: utf-8 -*-

from __future__ import division, absolute_import, print_function

import unittest

from datetime import datetime, timedelta
from freezegun import freeze_time
from dateutil import parser
import time
from ago import human
from tabulate import tabulate

from beets import logging

logging.getLogger('beets.radio-stream-settings').setLevel(logging.ERROR)

from beets.library import Item
from beetsplug.radio_stream import playlist_generator
from beetsplug.radio_stream.settings import Rules


class TestLib:
    def __init__(self, items):
        self._items = items

    def items(self, query):
        return self._items


class PlaylistGeneratorTest(unittest.TestCase):
    FREEZED_DATE = parser.parse("Aug 28 1999")

    _generated_song_number = 0

    def _to_timestamp(self, date):
        return time.mktime(date.timetuple())

    def _song(self, id):
        self._generated_song_number += 1
        last_played = self.FREEZED_DATE - timedelta(weeks=52)
        return Item(album=u'Album {} {}'.format(id, self._generated_song_number),
                    artist=u'Artist {} {}'.format(id, self._generated_song_number), title=u"{}".format(id),
                    lastplayed=self._to_timestamp(last_played), rating=60)

    def _song_played_days_ago(self, song, days):
        song.lastplayed = self._to_timestamp(self.FREEZED_DATE - timedelta(days=days))

    def _print_songs(self, songs):
        sorted_songs = sorted(songs, key=lambda song: -sum(song.scores.values()))

        data = [[song.artist, song.album, song.title, human(song.lastplayed), sum(song.scores.values()),
                 song.scores["rule_rating"], song.scores["rule_not_played_too_early"], song.scores["rule_play_count"],
                 song.scores["rule_new_song"], song.scores["rule_play_last_time"],
                 song.scores.get("post_rule_limit_artists", 0), song.scores.get("post_rule_limit_by_low_rating", 0)
                 ] for song
                in sorted_songs]

        return tabulate(data, headers=["artist", "album", "title", "lastplayed", "score", "rule_rating",
                                       "rule_not_played_too_early",
                                       "rule_play_count",
                                       "rule_new_song",
                                       "rule_play_last_time",
                                       "post_rule_limit_artists",
                                       "post_rule_limit_by_low_rating"], tablefmt='rst')

    @freeze_time(FREEZED_DATE)
    def test_prefer_songs_played_long_ago(self):

        rules = Rules()
        songs = []

        for i in range(5):
            song = self._song("played now")
            self._song_played_days_ago(song, rules.play_last_time_max_days / 10)
            songs.append(song)

        for i in range(5, 10):
            song = self._song("played long time ago")
            self._song_played_days_ago(song, rules.play_last_time_max_days)
            songs.append(song)

        lib = TestLib(songs)

        result_songs = playlist_generator.generate_playlist(lib, rules, 5, False)
        self.assertTrue(all(result_song.title == "played long time ago" for result_song in result_songs),
                        "found a recently played song in:\n" + self._print_songs(
                            result_songs) + "\n\nList of original songs:\n" + self._print_songs(songs))


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
