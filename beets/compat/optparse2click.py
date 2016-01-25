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
import optparse


def option_to_click(option):
    """Convert a `optparse.Option` to a `click.Option`.

    If the option should be dropped (i.e., it's a help option), return
    None.
    """
    is_flag = False
    multiple = False
    callback = None
    flag_value = None

    if option.action == 'store_true':
        is_flag = True
        flag_value = True

    elif option.action == 'store_false':
        is_flag = True
        flag_value = False

    elif option.action == 'append':
        multiple = True

    elif option.action == 'callback':
        # TODO Adjust signature.
        callback = option.callback

    elif option.action == 'help':
        return None

    op = click.Option(
        # The option names (and spelling).
        option._long_opts + option._short_opts,

        help=option.help,
        metavar=option.metavar,
        nargs=option.nargs,
        default=option.default,

        is_flag=is_flag,
        flag_value=flag_value,
        multiple=multiple,
        callback=callback,
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
        param = option_to_click(option)
        if param:
            params.append(param)

    # Add a click argument to gobble up all of the positional arguments.
    # (In optparse, these are not declared: it's entirely up to the
    # application to handle the list of strings that are passed.)
    params.append(click.Argument(['args'], nargs=-1))

    # Handle the callback and translate to the `optparse` arguments.
    def shim_callback(**kwargs):
        # Get the positional arguments.
        args = kwargs.pop('args')

        # Turn the rest of the arguments into a namespace object.
        opts = optparse.Values()
        for key, value in kwargs.items():
            setattr(opts, key, value)

        callback(opts, args)

    # Construct the Click command object.
    command = click.Command(params=params, callback=shim_callback, **kwargs)
    return command
