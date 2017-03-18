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

log = logging.getLogger('beets')


def run_rules(tracks, rules):
    for track in tracks:
        scores = {rule.__name__: rule(track) for rule in rules}

        track.scores = scores

    return tracks


def rule_play_rating(track, power=10):
    MAX_RATING = 100
    if "rating" in track:
        return float(track.rating) / MAX_RATING * power
    else:
        return 0


def rule_play_last_time(track, max_last_played_days=300, power=15):
    if "lastplayed" in track:
        last_played = datetime.fromtimestamp(track.lastplayed)
        days_passed = (datetime.now()-last_played).days
    else:
        days_passed = sys.maxint

    days_passed = min(max_last_played_days, days_passed)

    return min(max_last_played_days, float(days_passed)) / max_last_played_days * power


def rule_not_played_too_early(track,
                              star_1_min_days=84,
                              star_2_min_days=42,
                              star_3_min_days=21,
                              star_4_min_days=16,
                              star_5_min_days=14,
                              unrated_min_days=3,
                              power=-1000):
    if "lastplayed" in track:
        last_played = datetime.fromtimestamp(track.lastplayed)
        days_passed = (datetime.now()-last_played).days
    else:
        days_passed = sys.maxint

    min_days_for_rating = {
        # Rating: Min days
        20: star_1_min_days,
        40: star_2_min_days,
        60: star_3_min_days,
        80: star_4_min_days,
        100: star_5_min_days,
        # Unrated (new) songs
        0: unrated_min_days
    }

    rating = track.get("rating", 0)
    min_days = min_days_for_rating.get(rating, None)
    if not min_days:
        raise KeyError("Invalid rating: %s" % track)

    if days_passed < min_days:
        return power
    else:
        return 0


def rule_play_count(track, max_playcount_affect=100, power=-15):
    playcount = track.get("playcount", 0)
    return min(max_playcount_affect, float(playcount)) / max_playcount_affect * power


def rule_new_song(track, power=30):
    if track.get("rating", 0) == 0:
        return power
    else:
        return 0


def post_rule_limit_artists(sorted_tracks, power=2):
    """Reduce the score of tracks with artists that
    appeared before"""
    artists = {}
    for track in sorted_tracks:
        if track.artist in artists:
            artists[track.artist] += 1
            score = -(artists[track.artist]*power)
            track.scores["post_rule_limit_artists"] = score
            log.debug(u"Post score 'post_rule_limit_artists' to track {0}: {1}".format(track, score))
        else:
            artists[track.artist] = 0


def post_rule_limit_low_rating(sorted_tracks, low_rating=40, power=2):
    """Reduce the score of tracks with low rating"""
    rule_name = "post_rule_limit_by_low_rating"

    rating_count = 0

    for track in sorted_tracks:
        rating = track.get("rating", 0)
        if low_rating >= rating > 0:
            rating_count += 1
            score = -(rating_count*power)
            track.scores[rule_name] = score
            log.debug(u"Post score '{0}', to track {1}: {2}".format(rule_name, track, score))


def generate_playlist(lib, count, shuffle, input_query=""):
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
    post_rule_limit_low_rating(sorted_tracks)
    sorted_tracks = sorted(items, key=lambda track: -sum(track.scores.values()))

    trimmed_tracks = sorted_tracks[0:count]
    if shuffle:
        random.shuffle(trimmed_tracks)

    return trimmed_tracks
