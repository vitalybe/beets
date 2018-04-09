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

    def _song(self, id, unique_artist_album):
        self._generated_song_number += 1
        last_played = self.FREEZED_DATE - timedelta(weeks=52)
        item = Item(album=u'Album {}'.format(id),
                    artist=u'Artist {}'.format(id), title=u"{}".format(id),
                    lastplayed=self._to_timestamp(last_played), rating=60)

        if unique_artist_album:
            item.artist += u" - {}".format(self._generated_song_number)
            item.album += u" - {}".format(self._generated_song_number)

        return item

    def _song_played_days_ago(self, song, days):
        song.lastplayed = self._to_timestamp(self.FREEZED_DATE - timedelta(days=days))

    def _print_songs(self, songs):
        sorted_songs = sorted(songs, key=lambda song: -sum(song.scores.values()))

        data = [[song.artist, song.album, song.title, human(song.lastplayed), song.get("playcount", 0), song.get("rating", 0),
                 sum(song.scores.values()),
                 song.scores["rule_rating"], song.scores["rule_not_played_too_early"], song.scores["rule_play_count"],
                 song.scores["rule_new_song"], song.scores["rule_play_last_time"],
                 song.scores.get("post_rule_limit_artists", 0), song.scores.get("post_rule_limit_by_low_rating", 0),
                 song.scores.get("post_rule_limit_new_songs_amount", 0)
                 ] for song
                in sorted_songs]

        return tabulate(data, headers=["artist", "album", "title", "lastplayed", "playcount", "rating", "score",
                                       "r_rating",
                                       "r_not_early",
                                       "r_play_counyt",
                                       "r_new_song",
                                       "r_last_play",
                                       "pr_artists",
                                       "pr_low_rating",
                                       "pr_new_songs",
                                       ], tablefmt='rst')

    @freeze_time(FREEZED_DATE)
    def test_prefer_songs_played_long_ago(self):

        rules = Rules()
        songs = []

        for i in range(5):
            song = self._song("played now", True)
            self._song_played_days_ago(song, rules.play_last_time_max_days / 10)
            songs.append(song)

        for i in range(5, 10):
            song = self._song("played long time ago", True)
            self._song_played_days_ago(song, rules.play_last_time_max_days)
            songs.append(song)

        lib = TestLib(songs)

        result_songs = playlist_generator.generate_playlist(lib, rules, 5, False)
        self.assertTrue(all(result_song.title == "played long time ago" for result_song in result_songs),
                        "found a recently played song in:\n" + self._print_songs(
                            result_songs) + "\n\nList of original songs:\n" + self._print_songs(songs))

    @freeze_time(FREEZED_DATE)
    def test_limit_amount_of_new_songs(self):

        rules = Rules()
        songs = []

        rules.limit_new_songs_percent = 10
        song_count = rules.limit_new_songs_percent

        for i in range(song_count):
            song = self._song("new song", True)
            song.rating = 0
            songs.append(song)

        for i in range(song_count):
            song = self._song("rated song", True)
            songs.append(song)

        lib = TestLib(songs)

        result_songs = playlist_generator.generate_playlist(lib, rules, song_count, False)
        actual_count = sum(1 if song.title == "new song" else 0 for song in result_songs)
        expected_count = song_count / 100 * rules.limit_new_songs_percent
        self.assertTrue(actual_count == expected_count,
                        "expected {} new songs, found {}:\n{}\n\nList of original songs:\n{}".format(
                            expected_count, actual_count,
                            self._print_songs(result_songs), self._print_songs(songs)))

    @freeze_time(FREEZED_DATE)
    def test_limit_amount_of_same_artist(self):

        rules = Rules()
        songs = []

        rules.limit_artists_percent = 10

        expected_percent = rules.limit_artists_percent
        song_count = rules.limit_artists_percent

        for i in range(song_count):
            song = self._song("same artist", False)
            songs.append(song)

        for i in range(song_count):
            song = self._song("different artist", True)
            songs.append(song)

        lib = TestLib(songs)

        result_songs = playlist_generator.generate_playlist(lib, rules, song_count, False)
        actual_count = sum(1 if song.title == "same artist" else 0 for song in result_songs)
        expected_count = song_count / 100 * expected_percent
        self.assertTrue(actual_count == expected_count,
                        "expected {} new songs, found {}:\n{}\n\nList of original songs:\n{}".format(
                            expected_count, actual_count,
                            self._print_songs(result_songs), self._print_songs(songs)))

    @freeze_time(FREEZED_DATE)
    def test_limit_amount_of_low_rating(self):

        rules = Rules()
        songs = []

        rules.limit_low_rating_percent = 10

        expected_percent = rules.limit_low_rating_percent
        song_count = rules.limit_low_rating_percent

        for i in range(song_count):
            song = self._song("low rating", True)
            song.rating = 20
            songs.append(song)

        for i in range(song_count):
            song = self._song("high rating", True)
            self._song_played_days_ago(song, rules.star_3_min_days + 1)
            songs.append(song)

        lib = TestLib(songs)

        result_songs = playlist_generator.generate_playlist(lib, rules, song_count, False)
        actual_count = sum(1 if song.title == "low rating" else 0 for song in result_songs)
        expected_count = song_count / 100 * expected_percent
        self.assertTrue(actual_count == expected_count,
                        "expected {} new songs, found {}:\n{}\n\nList of original songs:\n{}".format(
                            expected_count, actual_count,
                            self._print_songs(result_songs), self._print_songs(songs)))

    @freeze_time(FREEZED_DATE)
    def test_limit_new_albums_by_new_song(self):

        rules = Rules()
        rules.limit_new_albums_count = 1
        rules.limit_new_songs_percent = 100
        rules.limit_artists_percent = 100

        songs = []
        song_count = 5
        for i in range(song_count):
            song = self._song("new songs", unique_artist_album=False)
            song.rating = 0
            songs.append(song)

        for i in range(song_count):
            song = self._song("high rating", unique_artist_album=False)
            self._song_played_days_ago(song, rules.star_3_min_days + 1)
            songs.append(song)

        song = self._song("expected new song", unique_artist_album=False)
        song["playcount"] = 3
        song.rating = 0
        songs.append(song)

        lib = TestLib(songs)
        result_songs = playlist_generator.generate_playlist(lib, rules, song_count, False)

        error_info = "\n\nResult songs:\n{}\n\nList of original songs:\n{}".format(self._print_songs(result_songs),
                                                                                   self._print_songs(songs))

        specific_song_found = any(result_song.title == "expected new song" for result_song in result_songs)
        self.assertTrue(specific_song_found, "expected 'expected new song' that wasn't found" + error_info)

        new_songs_count = sum(1 if song.title == "new songs" else 0 for song in result_songs)
        self.assertTrue(new_songs_count == 0, "expected 0 new songs, found {}".format(new_songs_count) + error_info)


    @freeze_time(FREEZED_DATE)
    def test_limit_new_albums_by_not_new_song(self):

        rules = Rules()
        rules.limit_new_albums_count = 1
        rules.limit_new_songs_percent = 100
        rules.limit_artists_percent = 100

        songs = []
        song_count = 5
        for i in range(song_count*2):
            song = self._song("new songs", unique_artist_album=False)
            song.rating = 0
            songs.append(song)

        for i in range(song_count):
            song = self._song("high rating", unique_artist_album=False)
            self._song_played_days_ago(song, rules.star_3_min_days + 1)
            songs.append(song)

        # this song isn't technically new but it is used to find other new songs
        song = self._song("expected new song", unique_artist_album=False)
        song["playcount"] = 3
        songs.append(song)

        song = self._song("expected new song", unique_artist_album=False)
        song.rating = 0
        songs.append(song)


        lib = TestLib(songs)
        result_songs = playlist_generator.generate_playlist(lib, rules, song_count, False)

        error_info = "\n\nResult songs:\n{}\n\nList of original songs:\n{}".format(self._print_songs(result_songs),
                                                                               self._print_songs(songs))

        specific_song_found = any(result_song.title == "expected new song" for result_song in result_songs)
        self.assertTrue(specific_song_found, "expected 'expected new song' that wasn't found" + error_info)

        new_songs_count = sum(1 if song.title == "new songs" else 0 for song in result_songs)
        self.assertTrue(new_songs_count == 0, "expected 0 new songs, found {}".format(new_songs_count) + error_info)


def suite():
    return unittest.TestLoader().loadTestsFromName(__name__)


if __name__ == '__main__':
    unittest.main(defaultTest='suite')
