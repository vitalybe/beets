# -*- coding: utf-8 -*-
# This file is part of beets.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

"""Adds support for ipfs. Requires go-ipfs and a running ipfs daemon
"""
import sys
import random
from datetime import datetime

from beets import ui, logging
from beets.plugins import BeetsPlugin
from beets.ui import print_, decargs

log = logging.getLogger('beets')


class VitalySmartPlaylists(BeetsPlugin):

    def __init__(self):
        super(VitalySmartPlaylists, self).__init__()

    def commands(self):
        cmd = ui.Subcommand('smart', help=u'generate smart vitaly playlists by song aggression, '
                                          u'e.g: beets smart -c 10 -s aggression::[12]')
        cmd.parser.add_option(u'-c', u'--count', dest='count', help="generated track count")
        cmd.parser.add_option(u'-s', u'--shuffle', action='store_true', help="shuffle the result")

        def func(lib, opts, args):

            count = int(opts.count)

            query = decargs(args)
            items = generate_playlist(lib, count, opts.shuffle, " ".join(query))

            for item in items:
                score_string = ", ".join(['%s: %s' % (key.replace("rule_", ""), value) for (key, value) in sorted(item.scores.items())])
                score_sum = round(sum(item.scores.values()), 2)
                item_string = unicode(item).ljust(60)
                print_(u"Track: {0} Scores: {1}=[{2}]".format(item_string, score_sum, score_string))


        cmd.func = func
        return [cmd]


def run_rules(tracks, rules):
    for track in tracks:
        scores = {rule.__name__: rule(track) for rule in rules}

        track.scores = scores

    return tracks


def rule_play_rating(track, power=10):
    MAX_RATING = 100
    if "itunes_rating" in track:
        return float(track.itunes_rating) / MAX_RATING * power
    else:
        return 0


def rule_play_last_time(track, power=15):
    MAX_PLAYED_LAST_DAYS = 300
    if "itunes_lastplayed" in track:
        last_played = datetime.fromtimestamp(track.itunes_lastplayed)
        days_passed = (datetime.now()-last_played).days
    else:
        days_passed = sys.maxint

    days_passed = min(MAX_PLAYED_LAST_DAYS, days_passed)

    return float(days_passed) / MAX_PLAYED_LAST_DAYS * power


def rule_not_played_too_early(track, power=-1000):
    if "itunes_lastplayed" in track:
        last_played = datetime.fromtimestamp(track.itunes_lastplayed)
        days_passed = (datetime.now()-last_played).days
    else:
        days_passed = sys.maxint

    min_days_for_rating = {
        # Rating: Min days
        20: 84,
        40: 42,
        60: 21,
        80: 16,
        100: 14,
        # Unrated (new) songs
        0: 3
    }

    rating = track.get("itunes_rating", 0)
    min_days = min_days_for_rating.get(rating, None)
    if not min_days:
        raise KeyError("Invalid rating: %s" % track)

    if days_passed < min_days:
        return power
    else:
        return 0


def rule_play_count(track, power=-10):
    MAX_COUNT = 30
    playcount = track.get("itunes_playcount", 0)
    return float(playcount) / MAX_COUNT * power


def rule_new_song(track, power=30):
    if track.get("itunes_rating", 0) == 0:
        return power
    else:
        return 0


def post_rule_limit_artists(sorted_tracks):
    """Reduce the score of tracks with artists that
    appeared before"""
    artists = {}
    for track in sorted_tracks:
        if track.artist in artists:
            artists[track.artist] += 1
            score = -(artists[track.artist]**2)
            track.scores["post_rule_limit_artists"] = score
            log.debug(u"Post score to track {0}: {1}".format(track, score))
        else:
            artists[track.artist] = 0


def generate_playlist(lib, count, shuffle, input_query=""):
    # TODO: Validate aggression (int, min, max)
    # TODO: accept range of aggression and query by aggression::[234] (ranges don't work since it is stored as string)

    RULES = [rule_play_rating, rule_not_played_too_early, rule_play_count, rule_new_song, rule_play_last_time]

    log.debug(u"Getting tracks")
    log.debug("Querying: " + input_query)
    items = lib.items(input_query)

    log.debug(u"Running rules")
    run_rules(items, RULES)
    sorted_tracks = sorted(items, key=lambda track: -sum(track.scores.values()))
    log.debug(u"Running post rules")
    post_rule_limit_artists(sorted_tracks)
    sorted_tracks = sorted(items, key=lambda track: -sum(track.scores.values()))

    trimmed_tracks = sorted_tracks[0:count]
    if shuffle:
        random.shuffle(trimmed_tracks)

    return trimmed_tracks
