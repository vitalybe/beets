from os import path
import pickle

from beets import config
from beets import logging

_beets_log = logging.getLogger('beets')
_log = _beets_log.getChild("radio-stream-settings")
_settings_file = path.join(path.dirname(config.user_config_path()), "radio-stream.pickle")
_log.info("settings location: " + _settings_file)


class Settings:
    def __init__(self):
        all_music_playlist = Playlist("All music", u"", False)
        self.playlists = {all_music_playlist.name: all_music_playlist}


class Playlist:
    def __init__(self, name, query, can_delete):
        self.name = name
        self.query = unicode(query)
        self.can_delete = can_delete


def load_settings():
    try:
        with open(_settings_file, 'rb') as f:
            return pickle.load(f)
    except Exception as exc:
        _log.warn(u'could not open radio-stream settings: {0}', exc)
        return None


def save_settings(settings):
    try:
        with open(_settings_file, 'wb') as f:
            pickle.dump(settings, f)
    except IOError as exc:
        _log.error(u'radio-stream settings file could not be written: {0}', exc)
