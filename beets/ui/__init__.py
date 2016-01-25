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

"""This module contains all of the core logic for beets' command-line
interface. To invoke the CLI, just call beets.ui.main(). The actual
CLI commands are implemented in the ui.commands module.
"""

from __future__ import (division, absolute_import, print_function,
                        unicode_literals)

import locale
import optparse
import click
import sys
from difflib import SequenceMatcher
import sqlite3
import errno
import re
import struct
import traceback
import os.path

from beets import logging
from beets import library
from beets import plugins
from beets import util
from beets.util.functemplate import Template
from beets import config
from beets.util import confit
from beets.autotag import mb
from beets.dbcore import query as db_query
from beets.compat import optparse2click

# On Windows platforms, use colorama to support "ANSI" terminal colors.
if sys.platform == b'win32':
    try:
        import colorama
    except ImportError:
        pass
    else:
        colorama.init()


log = logging.getLogger('beets')
if not log.handlers:
    log.addHandler(logging.StreamHandler())
log.propagate = False  # Don't propagate to root handler.


PF_KEY_QUERIES = {
    'comp': 'comp:true',
    'singleton': 'singleton:true',
}


class UserError(Exception):
    """UI exception. Commands should throw this in order to display
    nonrecoverable errors to the user.
    """


# Encoding utilities.

def _out_encoding():
    """Get the encoding to use for *outputting* strings to the console.
    """
    # Configured override?
    encoding = config['terminal_encoding'].get()
    if encoding:
        return encoding

    # For testing: When sys.stdout is a StringIO under the test harness,
    # it doesn't have an `encodiing` attribute. Just use UTF-8.
    if not hasattr(sys.stdout, 'encoding'):
        return 'utf8'

    # Python's guessed output stream encoding, or UTF-8 as a fallback
    # (e.g., when piped to a file).
    return sys.stdout.encoding or 'utf8'


def _arg_encoding():
    """Get the encoding for command-line arguments (and other OS
    locale-sensitive strings).
    """
    try:
        return locale.getdefaultlocale()[1] or 'utf8'
    except ValueError:
        # Invalid locale environment variable setting. To avoid
        # failing entirely for no good reason, assume UTF-8.
        return 'utf8'


def decargs(arglist):
    """Given a list of command-line argument bytestrings, attempts to
    decode them to Unicode strings.
    """
    return [s.decode(_arg_encoding()) for s in arglist]


def print_(*strings, **kwargs):
    """Like print, but rather than raising an error when a character
    is not in the terminal's encoding's character set, just silently
    replaces it.

    If the arguments are strings then they're expected to share the same
    type: either bytes or unicode.

    The `end` keyword argument behaves similarly to the built-in `print`
    (it defaults to a newline). The value should have the same string
    type as the arguments.
    """
    end = kwargs.get('end')

    if not strings or isinstance(strings[0], unicode):
        txt = u' '.join(strings)
        txt += u'\n' if end is None else end
    else:
        txt = b' '.join(strings)
        txt += b'\n' if end is None else end

    # Always send bytes to the stdout stream.
    if isinstance(txt, unicode):
        txt = txt.encode(_out_encoding(), 'replace')

    sys.stdout.write(txt)


# Configuration wrappers.

def _bool_fallback(a, b):
    """Given a boolean or None, return the original value or a fallback.
    """
    if a is None:
        assert isinstance(b, bool)
        return b
    else:
        assert isinstance(a, bool)
        return a


def should_write(write_opt=None):
    """Decide whether a command that updates metadata should also write
    tags, using the importer configuration as the default.
    """
    return _bool_fallback(write_opt, config['import']['write'].get(bool))


def should_move(move_opt=None):
    """Decide whether a command that updates metadata should also move
    files when they're inside the library, using the importer
    configuration as the default.

    Specifically, commands should move files after metadata updates only
    when the importer is configured *either* to move *or* to copy files.
    They should avoid moving files when the importer is configured not
    to touch any filenames.
    """
    return _bool_fallback(
        move_opt,
        config['import']['move'].get(bool) or
        config['import']['copy'].get(bool)
    )


# Input prompts.

def input_(prompt=None):
    """Like `raw_input`, but decodes the result to a Unicode string.
    Raises a UserError if stdin is not available. The prompt is sent to
    stdout rather than stderr. A printed between the prompt and the
    input cursor.
    """
    # raw_input incorrectly sends prompts to stderr, not stdout, so we
    # use print() explicitly to display prompts.
    # http://bugs.python.org/issue1927
    if prompt:
        print_(prompt, end=' ')

    try:
        resp = raw_input()
    except EOFError:
        raise UserError('stdin stream ended while input required')

    return resp.decode(sys.stdin.encoding or 'utf8', 'ignore')


def input_options(options, require=False, prompt=None, fallback_prompt=None,
                  numrange=None, default=None, max_width=72):
    """Prompts a user for input. The sequence of `options` defines the
    choices the user has. A single-letter shortcut is inferred for each
    option; the user's choice is returned as that single, lower-case
    letter. The options should be provided as lower-case strings unless
    a particular shortcut is desired; in that case, only that letter
    should be capitalized.

    By default, the first option is the default. `default` can be provided to
    override this. If `require` is provided, then there is no default. The
    prompt and fallback prompt are also inferred but can be overridden.

    If numrange is provided, it is a pair of `(high, low)` (both ints)
    indicating that, in addition to `options`, the user may enter an
    integer in that inclusive range.

    `max_width` specifies the maximum number of columns in the
    automatically generated prompt string.
    """
    # Assign single letters to each option. Also capitalize the options
    # to indicate the letter.
    letters = {}
    display_letters = []
    capitalized = []
    first = True
    for option in options:
        # Is a letter already capitalized?
        for letter in option:
            if letter.isalpha() and letter.upper() == letter:
                found_letter = letter
                break
        else:
            # Infer a letter.
            for letter in option:
                if not letter.isalpha():
                    continue  # Don't use punctuation.
                if letter not in letters:
                    found_letter = letter
                    break
            else:
                raise ValueError('no unambiguous lettering found')

        letters[found_letter.lower()] = option
        index = option.index(found_letter)

        # Mark the option's shortcut letter for display.
        if not require and (
            (default is None and not numrange and first) or
            (isinstance(default, basestring) and
             found_letter.lower() == default.lower())):
            # The first option is the default; mark it.
            show_letter = '[%s]' % found_letter.upper()
            is_default = True
        else:
            show_letter = found_letter.upper()
            is_default = False

        # Colorize the letter shortcut.
        show_letter = colorize('action_default' if is_default else 'action',
                               show_letter)

        # Insert the highlighted letter back into the word.
        capitalized.append(
            option[:index] + show_letter + option[index + 1:]
        )
        display_letters.append(found_letter.upper())

        first = False

    # The default is just the first option if unspecified.
    if require:
        default = None
    elif default is None:
        if numrange:
            default = numrange[0]
        else:
            default = display_letters[0].lower()

    # Make a prompt if one is not provided.
    if not prompt:
        prompt_parts = []
        prompt_part_lengths = []
        if numrange:
            if isinstance(default, int):
                default_name = unicode(default)
                default_name = colorize('action_default', default_name)
                tmpl = '# selection (default %s)'
                prompt_parts.append(tmpl % default_name)
                prompt_part_lengths.append(len(tmpl % unicode(default)))
            else:
                prompt_parts.append('# selection')
                prompt_part_lengths.append(len(prompt_parts[-1]))
        prompt_parts += capitalized
        prompt_part_lengths += [len(s) for s in options]

        # Wrap the query text.
        prompt = ''
        line_length = 0
        for i, (part, length) in enumerate(zip(prompt_parts,
                                               prompt_part_lengths)):
            # Add punctuation.
            if i == len(prompt_parts) - 1:
                part += '?'
            else:
                part += ','
            length += 1

            # Choose either the current line or the beginning of the next.
            if line_length + length + 1 > max_width:
                prompt += '\n'
                line_length = 0

            if line_length != 0:
                # Not the beginning of the line; need a space.
                part = ' ' + part
                length += 1

            prompt += part
            line_length += length

    # Make a fallback prompt too. This is displayed if the user enters
    # something that is not recognized.
    if not fallback_prompt:
        fallback_prompt = 'Enter one of '
        if numrange:
            fallback_prompt += '%i-%i, ' % numrange
        fallback_prompt += ', '.join(display_letters) + ':'

    resp = input_(prompt)
    while True:
        resp = resp.strip().lower()

        # Try default option.
        if default is not None and not resp:
            resp = default

        # Try an integer input if available.
        if numrange:
            try:
                resp = int(resp)
            except ValueError:
                pass
            else:
                low, high = numrange
                if low <= resp <= high:
                    return resp
                else:
                    resp = None

        # Try a normal letter input.
        if resp:
            resp = resp[0]
            if resp in letters:
                return resp

        # Prompt for new input.
        resp = input_(fallback_prompt)


def input_yn(prompt, require=False):
    """Prompts the user for a "yes" or "no" response. The default is
    "yes" unless `require` is `True`, in which case there is no default.
    """
    sel = input_options(
        ('y', 'n'), require, prompt, 'Enter Y or N:'
    )
    return sel == 'y'


# Human output formatting.

def human_bytes(size):
    """Formats size, a number of bytes, in a human-readable way."""
    powers = ['', 'K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'H']
    unit = 'B'
    for power in powers:
        if size < 1024:
            return "%3.1f %s%s" % (size, power, unit)
        size /= 1024.0
        unit = 'iB'
    return "big"


def human_seconds(interval):
    """Formats interval, a number of seconds, as a human-readable time
    interval using English words.
    """
    units = [
        (1, 'second'),
        (60, 'minute'),
        (60, 'hour'),
        (24, 'day'),
        (7, 'week'),
        (52, 'year'),
        (10, 'decade'),
    ]
    for i in range(len(units) - 1):
        increment, suffix = units[i]
        next_increment, _ = units[i + 1]
        interval /= float(increment)
        if interval < next_increment:
            break
    else:
        # Last unit.
        increment, suffix = units[-1]
        interval /= float(increment)

    return "%3.1f %ss" % (interval, suffix)


def human_seconds_short(interval):
    """Formats a number of seconds as a short human-readable M:SS
    string.
    """
    interval = int(interval)
    return u'%i:%02i' % (interval // 60, interval % 60)


# Colorization.

# ANSI terminal colorization code heavily inspired by pygments:
# http://dev.pocoo.org/hg/pygments-main/file/b2deea5b5030/pygments/console.py
# (pygments is by Tim Hatch, Armin Ronacher, et al.)
COLOR_ESCAPE = "\x1b["
DARK_COLORS = {
    "black": 0,
    "darkred": 1,
    "darkgreen": 2,
    "brown": 3,
    "darkyellow": 3,
    "darkblue": 4,
    "purple": 5,
    "darkmagenta": 5,
    "teal": 6,
    "darkcyan": 6,
    "lightgray": 7
}
LIGHT_COLORS = {
    "darkgray": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "fuchsia": 5,
    "magenta": 5,
    "turquoise": 6,
    "cyan": 6,
    "white": 7
}
RESET_COLOR = COLOR_ESCAPE + "39;49;00m"

# These abstract COLOR_NAMES are lazily mapped on to the actual color in COLORS
# as they are defined in the configuration files, see function: colorize
COLOR_NAMES = ['text_success', 'text_warning', 'text_error', 'text_highlight',
               'text_highlight_minor', 'action_default', 'action']
COLORS = None


def _colorize(color, text):
    """Returns a string that prints the given text in the given color
    in a terminal that is ANSI color-aware. The color must be something
    in DARK_COLORS or LIGHT_COLORS.
    """
    if color in DARK_COLORS:
        escape = COLOR_ESCAPE + "%im" % (DARK_COLORS[color] + 30)
    elif color in LIGHT_COLORS:
        escape = COLOR_ESCAPE + "%i;01m" % (LIGHT_COLORS[color] + 30)
    else:
        raise ValueError('no such color %s', color)
    return escape + text + RESET_COLOR


def colorize(color_name, text):
    """Colorize text if colored output is enabled. (Like _colorize but
    conditional.)
    """
    if config['ui']['color']:
        global COLORS
        if not COLORS:
            COLORS = dict((name, config['ui']['colors'][name].get(unicode))
                          for name in COLOR_NAMES)
        # In case a 3rd party plugin is still passing the actual color ('red')
        # instead of the abstract color name ('text_error')
        color = COLORS.get(color_name)
        if not color:
            log.debug(u'Invalid color_name: {0}', color_name)
            color = color_name
        return _colorize(color, text)
    else:
        return text


def _colordiff(a, b, highlight='text_highlight',
               minor_highlight='text_highlight_minor'):
    """Given two values, return the same pair of strings except with
    their differences highlighted in the specified color. Strings are
    highlighted intelligently to show differences; other values are
    stringified and highlighted in their entirety.
    """
    if not isinstance(a, basestring) or not isinstance(b, basestring):
        # Non-strings: use ordinary equality.
        a = unicode(a)
        b = unicode(b)
        if a == b:
            return a, b
        else:
            return colorize(highlight, a), colorize(highlight, b)

    if isinstance(a, bytes) or isinstance(b, bytes):
        # A path field.
        a = util.displayable_path(a)
        b = util.displayable_path(b)

    a_out = []
    b_out = []

    matcher = SequenceMatcher(lambda x: False, a, b)
    for op, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if op == 'equal':
            # In both strings.
            a_out.append(a[a_start:a_end])
            b_out.append(b[b_start:b_end])
        elif op == 'insert':
            # Right only.
            b_out.append(colorize(highlight, b[b_start:b_end]))
        elif op == 'delete':
            # Left only.
            a_out.append(colorize(highlight, a[a_start:a_end]))
        elif op == 'replace':
            # Right and left differ. Colorise with second highlight if
            # it's just a case change.
            if a[a_start:a_end].lower() != b[b_start:b_end].lower():
                color = highlight
            else:
                color = minor_highlight
            a_out.append(colorize(color, a[a_start:a_end]))
            b_out.append(colorize(color, b[b_start:b_end]))
        else:
            assert(False)

    return u''.join(a_out), u''.join(b_out)


def colordiff(a, b, highlight='text_highlight'):
    """Colorize differences between two values if color is enabled.
    (Like _colordiff but conditional.)
    """
    if config['ui']['color']:
        return _colordiff(a, b, highlight)
    else:
        return unicode(a), unicode(b)


def get_path_formats(subview=None):
    """Get the configuration's path formats as a list of query/template
    pairs.
    """
    path_formats = []
    subview = subview or config['paths']
    for query, view in subview.items():
        query = PF_KEY_QUERIES.get(query, query)  # Expand common queries.
        path_formats.append((query, Template(view.get(unicode))))
    return path_formats


def get_replacements():
    """Confit validation function that reads regex/string pairs.
    """
    replacements = []
    for pattern, repl in config['replace'].get(dict).items():
        repl = repl or ''
        try:
            replacements.append((re.compile(pattern), repl))
        except re.error:
            raise UserError(
                u'malformed regular expression in replace: {0}'.format(
                    pattern
                )
            )
    return replacements


def term_width():
    """Get the width (columns) of the terminal."""
    fallback = config['ui']['terminal_width'].get(int)

    # The fcntl and termios modules are not available on non-Unix
    # platforms, so we fall back to a constant.
    try:
        import fcntl
        import termios
    except ImportError:
        return fallback

    try:
        buf = fcntl.ioctl(0, termios.TIOCGWINSZ, ' ' * 4)
    except IOError:
        return fallback
    try:
        height, width = struct.unpack(b'hh', buf)
    except struct.error:
        return fallback
    return width


FLOAT_EPSILON = 0.01


def _field_diff(field, old, new):
    """Given two Model objects, format their values for `field` and
    highlight changes among them. Return a human-readable string. If the
    value has not changed, return None instead.
    """
    oldval = old.get(field)
    newval = new.get(field)

    # If no change, abort.
    if isinstance(oldval, float) and isinstance(newval, float) and \
            abs(oldval - newval) < FLOAT_EPSILON:
        return None
    elif oldval == newval:
        return None

    # Get formatted values for output.
    oldstr = old.formatted().get(field, u'')
    newstr = new.formatted().get(field, u'')

    # For strings, highlight changes. For others, colorize the whole
    # thing.
    if isinstance(oldval, basestring):
        oldstr, newstr = colordiff(oldval, newstr)
    else:
        oldstr = colorize('text_error', oldstr)
        newstr = colorize('text_error', newstr)

    return u'{0} -> {1}'.format(oldstr, newstr)


def show_model_changes(new, old=None, fields=None, always=False):
    """Given a Model object, print a list of changes from its pristine
    version stored in the database. Return a boolean indicating whether
    any changes were found.

    `old` may be the "original" object to avoid using the pristine
    version from the database. `fields` may be a list of fields to
    restrict the detection to. `always` indicates whether the object is
    always identified, regardless of whether any changes are present.
    """
    old = old or new._db._get(type(new), new.id)

    # Build up lines showing changed fields.
    changes = []
    for field in old:
        # Subset of the fields. Never show mtime.
        if field == 'mtime' or (fields and field not in fields):
            continue

        # Detect and show difference for this field.
        line = _field_diff(field, old, new)
        if line:
            changes.append(u'  {0}: {1}'.format(field, line))

    # New fields.
    for field in set(new) - set(old):
        if fields and field not in fields:
            continue

        changes.append(u'  {0}: {1}'.format(
            field,
            colorize('text_highlight', new.formatted()[field])
        ))

    # Print changes.
    if changes or always:
        print_(format(old))
    if changes:
        print_(u'\n'.join(changes))

    return bool(changes)


def show_path_changes(path_changes):
    """Given a list of tuples (source, destination) that indicate the
    path changes, log the changes as INFO-level output to the beets log.
    The output is guaranteed to be unicode.

    Every pair is shown on a single line if the terminal width permits it,
    else it is split over two lines. E.g.,

    Source -> Destination

    vs.

    Source
      -> Destination
    """
    sources, destinations = zip(*path_changes)

    # Ensure unicode output
    sources = map(util.displayable_path, sources)
    destinations = map(util.displayable_path, destinations)

    # Calculate widths for terminal split
    col_width = (term_width() - len(' -> ')) // 2
    max_width = len(max(sources + destinations, key=len))

    if max_width > col_width:
        # Print every change over two lines
        for source, dest in zip(sources, destinations):
            log.info(u'{0} \n  -> {1}', source, dest)
    else:
        # Print every change on a single line, and add a header
        title_pad = max_width - len('Source ') + len(' -> ')

        log.info(u'Source {0} Destination', ' ' * title_pad)
        for source, dest in zip(sources, destinations):
            pad = max_width - len(source)
            log.info(u'{0} {1} -> {2}', source, ' ' * pad, dest)


# The main entry point and bootstrapping.

def _load_plugins(config):
    """Load the plugins specified in the configuration.
    """
    paths = config['pluginpath'].get(confit.StrSeq(split=False))
    paths = map(util.normpath, paths)
    log.debug('plugin paths: {0}', util.displayable_path(paths))

    import beetsplug
    beetsplug.__path__ = paths + beetsplug.__path__
    # For backwards compatibility.
    sys.path += paths

    plugins.load_plugins(config['plugins'].as_str_seq())
    plugins.send("pluginload")
    return plugins


def _setup(lib=None):
    """Prepare and global state and updates it with command line options.

    Returns a list of subcommands, a list of plugins, and a library instance.
    """
    # Configure the MusicBrainz API.
    mb.configure()

    if lib is None:
        lib = _open_library(config)
        plugins.send("library_opened", lib=lib)
    library.Item._types.update(plugins.types(library.Item))
    library.Album._types.update(plugins.types(library.Album))

    return lib


def _configure(options):
    """Amend the global configuration object with command line options.
    """
    # Add any additional config files specified with --config. This
    # special handling lets specified plugins get loaded before we
    # finish parsing the command line.
    config_path = options.pop('config', None)
    if config_path is not None:
        config.set_file(config_path)
    config.set_args(options)

    # Configure the logger.
    if config['verbose'].get(int):
        log.set_global_level(logging.DEBUG)
    else:
        log.set_global_level(logging.INFO)

    # Ensure compatibility with old (top-level) color configuration.
    # Deprecation msg to motivate user to switch to config['ui']['color].
    if config['color'].exists():
        log.warning(u'Warning: top-level configuration of `color` '
                    u'is deprecated. Configure color use under `ui`. '
                    u'See documentation for more info.')
        config['ui']['color'].set(config['color'].get(bool))

    # Compatibility from list_format_{item,album} to format_{item,album}
    for elem in ('item', 'album'):
        old_key = 'list_format_{0}'.format(elem)
        if config[old_key].exists():
            new_key = 'format_{0}'.format(elem)
            log.warning('Warning: configuration uses "{0}" which is deprecated'
                        ' in favor of "{1}" now that it affects all commands. '
                        'See changelog & documentation.'.format(old_key,
                                                                new_key))
            config[new_key].set(config[old_key])

    config_path = config.user_config_path()
    if os.path.isfile(config_path):
        log.debug(u'user configuration: {0}',
                  util.displayable_path(config_path))
    else:
        log.debug(u'no user configuration found at {0}',
                  util.displayable_path(config_path))

    log.debug(u'data directory: {0}',
              util.displayable_path(config.config_dir()))
    return config


def _open_library(config):
    """Create a new library instance from the configuration.
    """
    dbpath = config['library'].as_filename()
    try:
        lib = library.Library(
            dbpath,
            config['directory'].as_filename(),
            get_path_formats(),
            get_replacements(),
        )
        lib.get_item(0)  # Test database connection.
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        log.debug('{}', traceback.format_exc())
        raise UserError(u"database file {0} could not be opened".format(
            util.displayable_path(dbpath)
        ))
    log.debug(u'library database: {0}\n'
              u'library directory: {1}',
              util.displayable_path(lib.path),
              util.displayable_path(lib.directory))
    return lib


# Click infrastructure for the root command, subcommands, and common
# options.

class AliasedSubcommand(click.Command):
    """A `click.Command` with aliases (i.e., alternate spellings for the
    command's name.

    For the aliases to actually work, this has to be used with a
    `click.MultiCommand` instance that knows to look for them. In our
    case, this is `BeetsCommand`.
    """
    def __init__(self, *args, **kwargs):
        aliases = kwargs.pop('aliases', ())
        self._help = ''
        self.aliases = aliases
        click.Command.__init__(self, *args, **kwargs)

    # TODO Remove this property in favor of modifying the formatter.
    @property
    def help(self):
        return self._help

    @help.setter
    def help(self, value):
        value = value or ''
        if self.aliases:
            value += '\n\nCommand aliases: {}'.format(' '.join(self.aliases))
        self._help = value


class BeetsCommand(click.MultiCommand):

    def _commands(self, ctx):
        # FIXME: Awful performance, cache per ctx
        from beets.ui.commands import default_commands

        config = _configure(ctx.params)
        _load_plugins(config)

        subcommands = list(default_commands)
        subcommands.extend(plugins.commands())

        out = {}

        for command in subcommands:
            # For compatibility, convert old optparse-based commands
            # into Click commands.
            if isinstance(command, LegacySubcommand):
                command = command._to_click(ctx)

            # Register the alias names.
            out[command.name] = command
            if hasattr(command, 'aliases'):
                for alias in command.aliases:
                    out[alias] = command

        return out

    def list_commands(self, ctx):
        # Skip aliases for help page
        return set(command.name for command in self._commands(ctx).values())

    def get_command(self, ctx, name):
        # Special case for the `config --edit` command: bypass loading config
        # so that an invalid configuration does not prevent the editor from
        # starting.
        if ctx.args and ctx.args[0] == 'config' and \
           ('-e' in ctx.args or '--edit' in ctx.args):
            from beets.ui.commands import config_cmd
            return config_cmd

        return self._commands(ctx).get(name)


class Context(object):
    """A context object that is passed to each command callback.
    """
    def __init__(self, lib=None):
        self.lib = lib


pass_context = click.make_pass_decorator(Context, ensure=True)


def album_option(f):
    """Add a -a/--album option to match albums instead of tracks.

    If used then the format option can auto-detect whether we're setting
    the format for items or albums.
    Sets the album property on the options extracted from the CLI.
    """
    return click.option('-a', '--album', is_eager=True, is_flag=True,
                        help='match albums instead of tracks')(f)


def _set_format(target, fmt):
    config[target._format_config_key].set(fmt)


def path_option(f):
    """Add a -p/--path option to display the path instead of the default
    format.

    By default this affects both items and albums. If album_option()
    is used then the target will be autodetected.

    Sets the format property to u'$path' on the options extracted from the
    CLI.
    """
    def callback(ctx, param, value):
        if not value:
            return

        if 'album' in ctx.params:
            if ctx.params['album']:
                _set_format(library.Album, '$path')
            else:
                _set_format(library.Item, '$path')
        else:
            _set_format(library.Item, '$path')
            _set_format(library.Album, '$path')

    return click.option('-p', '--path', callback=callback, is_flag=True,
                        help='print paths for matched items or albums')(f)


def format_option(flags=('-f', '--format'), target=None):
    def callback(ctx, param, value):
        if not value:
            return

        value = unicode(value)

        _target = target
        if _target:
            if _target in ('item', 'album'):
                _target = {'item': library.Item,
                           'album': library.Album}[_target]
            _set_format(_target, value)
        else:
            if 'album' in ctx.params:
                if ctx.params['album']:
                    _set_format(library.Album, value)
                else:
                    _set_format(library.Item, value)
            else:
                _set_format(library.Item, value)
                _set_format(library.Album, value)

    def decorator(f):
        return click.option(*flags, callback=callback, expose_value=False,
                            help='print with custom format')(f)

    return decorator


def all_common_options(f):
    return album_option(path_option(format_option()(f)))


# Legacy compatibility for optparse-based code.

class LegacyOptionsParser(optparse.OptionParser, object):
    """For backwards compatibility with optparse code, provides helpers
    for adding common command-line options.
    """
    def __init__(self, *args, **kwargs):
        super(LegacyOptionsParser, self).__init__(*args, **kwargs)
        self._album_flags = False
        # this serves both as an indicator that we offer the feature AND allows
        # us to check whether it has been specified on the CLI - bypassing the
        # fact that arguments may be in any order

    def add_album_option(self, flags=('-a', '--album')):
        """Add a -a/--album option to match albums instead of tracks.

        If used then the format option can auto-detect whether we're setting
        the format for items or albums.
        Sets the album property on the options extracted from the CLI.
        """
        album = optparse.Option(*flags, action='store_true',
                                help='match albums instead of tracks')
        self.add_option(album)
        self._album_flags = set(flags)

    def _set_format(self, option, opt_str, value, parser, target=None,
                    fmt=None, store_true=False):
        """Internal callback that sets the correct format while parsing CLI
        arguments.
        """
        if store_true:
            setattr(parser.values, option.dest, True)

        value = fmt or value and unicode(value) or ''
        parser.values.format = value
        if target:
            config[target._format_config_key].set(value)
        else:
            if self._album_flags:
                if parser.values.album:
                    target = library.Album
                else:
                    # the option is either missing either not parsed yet
                    if self._album_flags & set(parser.rargs):
                        target = library.Album
                    else:
                        target = library.Item
                config[target._format_config_key].set(value)
            else:
                config[library.Item._format_config_key].set(value)
                config[library.Album._format_config_key].set(value)

    def add_path_option(self, flags=('-p', '--path')):
        """Add a -p/--path option to display the path instead of the default
        format.

        By default this affects both items and albums. If add_album_option()
        is used then the target will be autodetected.

        Sets the format property to u'$path' on the options extracted from the
        CLI.
        """
        path = optparse.Option(*flags, nargs=0, action='callback',
                               callback=self._set_format,
                               callback_kwargs={'fmt': '$path',
                                                'store_true': True},
                               help='print paths for matched items or albums')
        self.add_option(path)

    def add_format_option(self, flags=('-f', '--format'), target=None):
        """Add -f/--format option to print some LibModel instances with a
        custom format.

        `target` is optional and can be one of ``library.Item``, 'item',
        ``library.Album`` and 'album'.

        Several behaviors are available:
            - if `target` is given then the format is only applied to that
            LibModel
            - if the album option is used then the target will be autodetected
            - otherwise the format is applied to both items and albums.

        Sets the format property on the options extracted from the CLI.
        """
        kwargs = {}
        if target:
            if isinstance(target, basestring):
                target = {'item': library.Item,
                          'album': library.Album}[target]
            kwargs['target'] = target

        opt = optparse.Option(*flags, action='callback',
                              callback=self._set_format,
                              callback_kwargs=kwargs,
                              help='print with custom format')
        self.add_option(opt)

    def add_all_common_options(self):
        """Add album, path and format options.
        """
        self.add_album_option()
        self.add_path_option()
        self.add_format_option()


class LegacySubcommand(object):
    """A backwards-compatibility shim for code that still uses the
    `optparse` API. We translate these into Click commands.
    """
    def __init__(self, name, parser=None, help='', aliases=(), hide=False):
        self.name = name
        self.parser = parser or LegacyOptionsParser()
        self.aliases = aliases
        self.help = help
        self.hide = hide

    def _to_click(self, ctx):
        """Create a `click.Command` from the `OptionParser`.
        """
        # The callback gets the beets Context and invokes the
        # appropriate command. Using a default argument here is
        # a *totally hacky* way around Python's ordinary
        # variable scoping, which doesn't let this closure
        # capture the *current* value of `command` (i.e., later
        # mutations confuse it).
        def callback(opts, args, func=self.func):
            func(ctx.find_object(Context).lib, opts, args)

        return optparse2click.parser_to_click(
            self.parser,
            callback,
            cls=AliasedSubcommand,
            name=self.name,
            help=self.help,
            aliases=self.aliases,
        )


Subcommand = LegacySubcommand  # The old name.


# TODO `main` should be the command; not `_raw_main`.
@click.command(cls=BeetsCommand,
               context_settings={'help_option_names': ['-h', '--help']})
@format_option(flags=('--format-item',), target=library.Item)
@format_option(flags=('--format-album',), target=library.Album)
@click.option('-l', '--library', metavar='LIBRARY',
              help='Library database file to use.')
@click.option('-d', '--directory', metavar='DIRECTORY',
              help='Destination music directory.')
@click.option('-v', '--verbose', count=True,
              help='Print debugging information.')
@click.option('-c', '--config', metavar='CONFIG',
              help='Path to configuration file.')
@click.pass_context
def _raw_main(ctx, **options):
    """A helper function for `main` without top-level exception
    handling.
    """
    if ctx.invoked_subcommand == 'config':
        return

    beets_ctx = ctx.ensure_object(Context)
    beets_ctx.lib = _setup(beets_ctx.lib)

    @ctx.call_on_close
    def send_plugin_cli_exit():
        plugins.send('cli_exit', lib=beets_ctx.lib)


def main(args=None):
    """Run the main command-line interface for beets. Includes top-level
    exception handlers that print friendly error messages.
    """
    try:
        _raw_main(args)
    except UserError as exc:
        message = exc.args[0] if exc.args else None
        log.error(u'error: {0}', message)
        sys.exit(1)
    except util.HumanReadableException as exc:
        exc.log(log)
        sys.exit(1)
    except library.FileOperationError as exc:
        # These errors have reasonable human-readable descriptions, but
        # we still want to log their tracebacks for debugging.
        log.debug('{}', traceback.format_exc())
        log.error('{}', exc)
        sys.exit(1)
    except confit.ConfigError as exc:
        log.error(u'configuration error: {0}', exc)
        sys.exit(1)
    except db_query.InvalidQueryError as exc:
        log.error(u'invalid query: {0}', exc)
        sys.exit(1)
    except IOError as exc:
        if exc.errno == errno.EPIPE:
            # "Broken pipe". End silently.
            pass
        else:
            raise
    except KeyboardInterrupt:
        # Silently ignore ^C except in verbose mode.
        log.debug('{}', traceback.format_exc())
