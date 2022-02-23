from argparse import ArgumentParser
import logging

from .. import commands, version, defaults, _smcpp
from ..log import init_logging

def init_subparsers(subparsers_obj):
    from .. import commands
    ret = {}
    kwds = {cls.__name__.lower(): cls
            for cls in commands.command.ConsoleCommand.__subclasses__()}
    for kwd in sorted(kwds):
        cls = kwds[kwd]
        p = subparsers_obj.add_parser(kwd, help=cls.__doc__)
        ret[kwd] = cls(p)
    return ret


def main():
    init_logging()
    logger = logging.getLogger(__name__)
    logger.debug("SMC++ " + version.version)
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True
    cmds = init_subparsers(subparsers)
    args = parser.parse_args()
    cmds[args.command].main(args)
