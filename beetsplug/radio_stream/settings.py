from os import path
import pickle

from beets import config
from beets import logging

_beets_log = logging.getLogger('beets')
_log = _beets_log.getChild("radio-stream-settings")
_settings_file = path.join(path.dirname(config.user_config_path()), "radio-stream.pickle")
_log.debug("settings location: " + _settings_file)


class Settings:

    def __init__(self):
        self.playlists = None
        self.rules = None

    @staticmethod
    def load():
        settings = None
        try:
            with open(_settings_file, 'rb') as f:
                settings = pickle.load(f)
        except Exception as exc:
            _log.warn(u'could not open radio-stream settings: {0}', exc)

        if settings is None:
            settings = Settings()

        if settings.playlists is None:
            all_music_playlist = Playlist("All music", u"", False)
            settings.playlists = {all_music_playlist.name: all_music_playlist}

        if settings.rules is None:
            settings.rules = Rules()

        return settings

    def save(self):
        try:
            with open(_settings_file, 'wb') as f:
                pickle.dump(self, f)
        except IOError as exc:
            _log.error(u'radio-stream settings file could not be written: {0}', exc)


class Rules:
    def __init__(self):
        self.rating_power = 10

        self.play_last_time_power = 15
        self.play_last_time_max_days = 300

        self.play_count_power = -15
        self.play_count_max = 100

        self.new_song_power = 30

        self.unrated_power = -1000
        self.star_1_min_days = 84
        self.star_2_min_days = 42
        self.star_3_min_days = 21
        self.star_4_min_days = 16
        self.star_5_min_days = 14
        self.unrated_min_days = 0

        self.limit_low_rating_from = 40
        self.limit_low_rating_percent = 10

        self.limit_artists_percent = 10
        self.limit_new_songs_percent = 10

        self.limit_new_albums_count = 1



class Playlist:
    def __init__(self, name, query, can_delete):
        self.name = name
        self.query = unicode(query)
        self.can_delete = can_delete

