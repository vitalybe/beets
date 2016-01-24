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

"""This is a shim to provide backwards compatibility with optparse in
Click-based applications. It translates from `OptionParser` objects to
Click's `Command` objects.
"""

import click


def option_to_click(option):
    """Convert a `optparse.Option` to a `click.Option`.
    """
    op = click.Option(
        # The option names (and spelling).
        option._long_opts + option._short_opts,

        # Details that we can port right over from optparse.
        help=option.help,
        metavar=option.metavar,
        nargs=option.nargs,
        default=option.default,
    )
    return op


def parser_to_click(parser, callback, **kwargs):
    """Convert an `optparse.OptionParser` to a `click.Command`.

    `parser` is the `OptionParser`. `func` is the callback to be invoked
    for the command; its arguments are `opts`, a namespace object for
    the named options, and `args`, a list of positional arguments.

    All other keyword arguments are passed through to the `Command`
    constructor.
    """
    # Convert each of the optparse options.
    params = []
    for option in parser.option_list:
        params.append(option_to_click(option))

    # Handle the callback and translate to the `optparse` arguments.
    def shim_callback(*args, **opts):
        print(args, kwargs)
        # callback(args, kwargs)

    # Construct the Click command object.
    command = click.Command(params=params, callback=shim_callback, **kwargs)
    return command
