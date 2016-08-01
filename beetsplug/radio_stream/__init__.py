# -*- coding: utf-8 -*-
# This file is part of beets.
# Copyright 2016, Adrian Sampson.
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

"""A Web interface to beets."""
from __future__ import division, absolute_import, print_function

from datetime import datetime
import time

from beets.plugins import BeetsPlugin
from beets import ui
from beets import util
import beets.library
import flask
from flask import g, request
from werkzeug.routing import BaseConverter, PathConverter
import os
import json
import logging
from beetsplug.radio_stream import playlist_generator
from beets.ui import print_, decargs


# Utilities.

def _rep(obj, expand=False):
    """Get a flat -- i.e., JSON-ish -- representation of a beets Item or
    Album object. For Albums, `expand` dictates whether tracks are
    included.
    """
    out = dict(obj)

    if isinstance(obj, beets.library.Item):
        music_folder_name = "radio-stream/music"
        headless_start_index = out['path'].find(music_folder_name) + len(music_folder_name) + 1
        out['path'] = out['path'][headless_start_index::]

        # Get the size (in bytes) of the backing file. This is useful
        # for the Tomahawk resolver API.
        try:
            out['size'] = os.path.getsize(util.syspath(obj.path))
        except OSError:
            out['size'] = 0

        return out

    elif isinstance(obj, beets.library.Album):
        del out['artpath']
        if expand:
            out['items'] = [_rep(item) for item in obj.items()]
        return out


def json_generator(items, root):
    """Generator that dumps list of beets Items or Albums as JSON

    :param root:  root key for JSON
    :param items: list of :class:`Item` or :class:`Album` to dump
    :returns:     generator that yields strings
    """
    yield '{"%s":[' % root
    first = True
    for item in items:
        if first:
            first = False
        else:
            yield ','
        yield json.dumps(_rep(item))
    yield ']}'


def resource(name):
    """Decorates a function to handle RESTful HTTP requests for a resource.
    """
    def make_responder(retriever):
        def responder(ids):
            entities = [retriever(id) for id in ids]
            entities = [entity for entity in entities if entity]

            if len(entities) == 1:
                return flask.jsonify(_rep(entities[0]))
            elif entities:
                return app.response_class(
                    json_generator(entities, root=name),
                    mimetype='application/json'
                )
            else:
                return flask.abort(404)
        responder.__name__ = b'get_%s' % name.encode('utf8')
        return responder
    return make_responder


def resource_query(name):
    """Decorates a function to handle RESTful HTTP queries for resources.
    """
    def make_responder(query_func):
        def responder(queries):
            return app.response_class(
                json_generator(query_func(queries), root='results'),
                mimetype='application/json'
            )
        responder.__name__ = b'query_%s' % name.encode('utf8')
        return responder
    return make_responder


def resource_list(name):
    """Decorates a function to handle RESTful HTTP request for a list of
    resources.
    """
    def make_responder(list_all):
        def responder():
            return app.response_class(
                json_generator(list_all(), root=name),
                mimetype='application/json'
            )
        responder.__name__ = b'all_%s' % name.encode('utf8')
        return responder
    return make_responder


class IdListConverter(BaseConverter):
    """Converts comma separated lists of ids in urls to integer lists.
    """

    def to_python(self, value):
        ids = []
        for id in value.split(','):
            try:
                ids.append(int(id))
            except ValueError:
                pass
        return ids

    def to_url(self, value):
        return ','.join(value)


class QueryConverter(PathConverter):
    """Converts slash separated lists of queries in the url to string list.
    """

    def to_python(self, value):
        return value.split('/')

    def to_url(self, value):
        return ','.join(value)


# Flask setup.

app = flask.Flask(__name__)
app.url_map.converters['idlist'] = IdListConverter
app.url_map.converters['query'] = QueryConverter

@app.before_request
def before_request():
    g.lib = app.config['lib']

# Smart playlists

smart_playlists = {
    "Metal": u"aggression::[34]",
    "NoMetal": u"aggression::[12]",
    "NoMetal-NoNew": u"aggression::[12] itunes_rating:1.."
}

@app.route('/playlists')
def playlists():
    return flask.jsonify({
        'playlists': smart_playlists.keys()
    })


@app.route('/playlists/<queries>')
@resource_query('playlist_items')
def playlist_by_name(queries):
    query = smart_playlists[queries]
    tracks = playlist_generator.generate_playlist(g.lib, 30, True, query)

    return tracks


@app.route('/item/<id>/rating', methods=["PUT"])
def update_rating(id):
    track = g.lib.get_item(id)
    track.itunes_rating = request.get_json()["newRating"]
    with g.lib.transaction():
        track.try_sync(True, False)

    return "", 200


@app.route('/item/<id>/last-played', methods=["POST"])
def update_last_played(id):
    track = g.lib.get_item(id)
    if "itunes_playcount" not in track:
        track.itunes_playcount = 0

    track.itunes_playcount += 1
    track.itunes_lastplayed = time.mktime(datetime.utcnow().timetuple())
    with g.lib.transaction():
        track.try_sync(True, False)

    return "", 200


# Plugin hook.
class RadioStreamPlugin(BeetsPlugin):
    def __init__(self):
        super(RadioStreamPlugin, self).__init__()

    def preview_playlist_command(self, lib, opts, args):
        name_column_length = 60
        count = 10

        if opts:
            count = int(opts.count)

        query = decargs(args)
        items = playlist_generator.generate_playlist(lib, count, opts.shuffle, u" ".join(query))

        for item in items:
            score_string = ", ".join(['%s: %s' % (key.replace("rule_", ""), value) for (key, value) in sorted(item.scores.items())])
            score_sum = round(sum(item.scores.values()), 2)
            item_name = unicode(item)
            if len(item_name) > name_column_length-5:
                item_name = item_name[:name_column_length-5] + "..."
            item_string = item_name.ljust(name_column_length)
            print_(u"Track: {0} Scores: {1}=[{2}]".format(item_string, score_sum, score_string))

    def start_server_command(self, lib, opts, args):
        args = ui.decargs(args)

        self.config.add({
            'host': u'0.0.0.0',
            'port': 5000,
            'cors': '',
        })

        if args:
            self.config['host'] = args.pop(0)
        if args:
            self.config['port'] = int(args.pop(0))

        app.config['lib'] = lib
        # Enable CORS if required.
        if self.config['cors']:
            self._log.info(u'Enabling CORS with origin: {0}',
                           self.config['cors'])
            from flask.ext.cors import CORS
            app.config['CORS_ALLOW_HEADERS'] = "Content-Type"
            app.config['CORS_RESOURCES'] = {
                r"/*": {"origins": self.config['cors'].get(str)}
            }
            CORS(app)
        # Start the web application.
        app.run(host=self.config['host'].get(unicode),
                port=self.config['port'].get(int),
                debug=opts.debug, threaded=True)

    def commands(self):
        server_command = ui.Subcommand('radio', help=u'start the sever')
        server_command.parser.add_option(u'-d', u'--debug', action='store_true', default=False, help=u'debug mode')
        server_command.func = self.start_server_command

        preview_command = ui.Subcommand('radio-preview',
                                         help=u'preview generated playlists, e.g: radio-preview -c 10 genre:metal')
        preview_command.parser.add_option(u'-c', u'--count', dest='count', default=30, help="generated track count")
        preview_command.parser.add_option(u'-s', u'--shuffle', action='store_true', help="shuffle the result")
        preview_command.func = self.preview_playlist_command

        return [server_command, preview_command]
