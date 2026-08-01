"""
Linux command line tool to conveniently add, remove, and change directories for
git worktrees. Prompts user to select worktree using fuzzy finder if no worktree
name is given.
"""

from __future__ import annotations

import getpass
import os
import shlex
import subprocess
import sys
from argparse import SUPPRESS, ArgumentParser, Namespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROG = Path(__file__).stem.replace('_', '-')
HOME = Path.home()
HASH_LEN = 7

# Default command name (shell function) this program is installed as. Can be
# changed using command line option.
DEFCMD = 'wt'

# Default fuzzy finder. Can be changed using command line option.
DEFAULT_FUZZY = 'fzf'

# Relative path from top level repo dir to directory for newly created
# worktrees. Can be changed using command line option. Can use {user}, {repo},
# and {home} as placeholders for the current user name, repo name, and home
# directory.
BASEDIR = '../worktrees/{repo}'

# Template for the shell code injected into user's shell session
SHELLCODE = """
!cmd() {
    local d
    d=$(!prog -_ "$@")
    local r=$?

    if [ $r -ne 0 ]; then
        if [ $r -eq 2 ]; then
            return 0
        fi
        return $r
    fi

    cd -- "$d"
}
"""


def init_code(cmd: str) -> str:
    "Return shell init code as string"
    from string import Template

    # We need to change the template delimiter because the standard
    # delimiter "$" is too common in regular shell code .
    class CTemplate(Template):
        delimiter = '!'

    prog = sys.argv[0]
    arglist = cmd.split(maxsplit=1)
    if len(arglist) > 1:
        cmd, opts = arglist
        prog += f' {opts}'

    return CTemplate(SHELLCODE.strip()).substitute(cmd=cmd, prog=prog)


def run(
    cmd: Sequence[str],
    *,
    stdin: str | None = None,
    stdout: Any = subprocess.PIPE,
    ignore_error: bool = False,
) -> str:
    "Run command and return stdout"
    capture = stdout == subprocess.PIPE
    try:
        res = subprocess.run(cmd, stdout=stdout, text=capture, input=stdin)
    except Exception as e:
        sys.exit(f'error: failed to run command "{cmd[0]}": {e}')

    if not ignore_error and res.returncode != 0:
        sys.exit(res.returncode)

    if capture:
        return res.stdout.strip()

    return ''


def get_title(desc: str) -> str:
    "Return single title line from command description"
    res = []
    for line in desc.splitlines():
        line = line.strip()
        res.append(line)
        if line.endswith('.'):
            return ' '.join(res)

    sys.exit('Must end description with a full stop.')


def unexpanduser(path: Path) -> Path:
    "Return path name, with $HOME replaced by ~ (opposite of Path.expanduser())"
    if path.parts[: len(HOME.parts)] != HOME.parts:
        return path

    return Path('~', *path.parts[len(HOME.parts) :])


def generate_new_name(exists: Callable[[str], Any]) -> str:
    "Generate a new worktree name"
    from coolname import generate_slug

    for _ in range(100):
        if not exists(name := generate_slug(2)):
            return name

    sys.exit('error: failed to generate a new worktree name.')


def print_version(args: Namespace) -> None:
    "Print program version"
    from importlib import metadata

    try:
        version = metadata.version(PROG)
    except Exception:
        version = '?'

    print(version, file=args._stdout)


def print_help(args: Namespace) -> None:
    "Print program help message"
    if 'parser' in args:
        args.parser.print_help(sys.stderr)
    else:
        args._opt.print_help(sys.stderr)


@dataclass
class Tree:
    "Data for an individual worktree"

    path: Path
    path_display: str
    head: str = ''
    branch: str = ''


class Trees:
    "Class to manage the collection of worktrees"

    trees: list[Tree]
    toplevel: Tree
    current: Tree

    @classmethod
    def fetch(cls, args: Namespace) -> None:
        "Get worktrees"
        trees = []
        tree = None
        cwdparts = Path.cwd().parts
        phere = pindex = -1
        for line in run('git worktree list --porcelain'.split()).splitlines():
            if not (line := line.strip()):
                continue

            if len(fields := line.split(maxsplit=1)) < 2:
                continue

            field, value = fields

            if field == 'worktree':
                path = Path(value)

                if args.relative:
                    path_display = os.path.relpath(path)
                elif args.no_user:
                    path_display = str(path)
                else:
                    path_display = str(unexpanduser(path))

                plen = len(path.parts)
                if path.parts == cwdparts[:plen] and plen > phere:
                    phere = plen
                    pindex = len(trees)

                trees.append(tree := Tree(path, path_display))
            elif tree:
                if field == 'HEAD':
                    tree.head = value[:HASH_LEN]
                elif field == 'branch':
                    tree.branch = value.split('/')[-1]

        if not trees:
            sys.exit('error: no worktrees found.')

        if pindex > 0:
            trees = [trees[pindex]] + trees[:pindex] + trees[pindex + 1 :]
            cls.toplevel = trees[1]
        else:
            cls.toplevel = trees[0]

        cls.current = trees[0]
        cls.trees = trees

    @classmethod
    def get_trees(cls) -> list[str]:
        "Fetch string list of worktrees"
        pw = max(len(str(t.path_display)) for t in cls.trees)
        lines = []
        for t in cls.trees:
            tlist = [f'{t.path_display:{pw}}']
            if t.head:
                tlist.append(t.head)
            if t.branch:
                tlist.append(f'[{t.branch}]')
            else:
                tlist.append('detached')

            lines.append(' '.join(tlist))

        return lines

    @classmethod
    def get_tree(cls, text: str) -> Tree | None:
        "Return first tree where branch, then path name, then head matches given text"
        for tree in cls.trees:
            if tree.branch == text:
                return tree

        for tree in cls.trees:
            if tree.path.name == text:
                return tree

        for tree in cls.trees:
            if tree.head == text:
                return tree

    @classmethod
    def create_worktree(cls, name: str, args: Namespace) -> Path:
        "Create a new worktree and branch with the given name"
        if not name:
            name = generate_new_name(cls.get_tree)

        try:
            basedir = args.basedir.format(
                user=getpass.getuser(), repo=cls.toplevel.path.name, home=str(HOME)
            )
        except Exception as e:
            sys.exit(f'error: failed to format basedir: {e}')

        basepath = Path(basedir).expanduser()
        basepath = (cls.toplevel.path / basepath).resolve()
        basepath.mkdir(parents=True, exist_ok=True)
        path = basepath / name

        cmd = 'git worktree add'.split()
        if args.detach:
            cmd.append('--detach')
        run(cmd + [str(path)], stdout=args._stdout)
        return path

    @classmethod
    def remove_worktree(cls, name: str, args: Namespace) -> Path | None:
        "Remove the worktree and branch with the given name"
        if name:
            if not (tree := cls.get_tree(name)):
                sys.exit(f'error: no worktree found with name "{name}".')
        else:
            # If no name is given, remove the current worktree and branch
            tree = cls.current

        # Change to the top-level worktree directory before deleting a worktree
        # and/or branch, in case the current directory is removed.
        os.chdir(cls.toplevel.path)

        cmd = 'git worktree remove'.split()
        if args.force:
            cmd.append('--force')

        run(cmd + [str(tree.path)], stdout=args._stdout)

        path_display = os.path.relpath(tree.path) if args.relative else str(tree.path)
        print(f'Removed worktree "{path_display}"', file=args._stdout)

        if tree.branch and not args.keep_branch:
            cmd = 'git branch'.split() + ['-D' if args.force else '-d']
            run(cmd + [tree.branch], stdout=args._stdout, ignore_error=True)

        return cls.toplevel.path if tree == cls.current else None

    @classmethod
    def prompt(cls, args: Namespace) -> Path | None:
        "Prompt user to select a worktree using fuzzy finder"
        wtrees = cls.get_trees()
        if not wtrees:
            sys.exit('error: no worktrees to remove.')
        line = run(shlex.split(args.fuzzy), stdin='\n'.join(wtrees)).strip()
        if not line or line not in wtrees:
            return None

        return cls.trees[wtrees.index(line)].path


def main() -> int:
    "Main code"
    # Main returns a status code:
    # 0 = Directory written to stdout. Calling script will "cd" to that
    #     worktree directory and return error code for that cd command result.
    # 1 = Error/message already written to stderr via sys.exit(). Calling script
    #     will silently quit and return that error code.
    # 2 = Caller will silently quit and return exit code 0.

    # Parse arguments
    opt = ArgumentParser(description=__doc__, add_help=False)
    opt.add_argument(
        '-B',
        '--basedir',
        default=BASEDIR,
        help='base directory for newly added worktrees, default="%(default)s".',
    )
    opt.add_argument(
        '-R',
        '--relative',
        action='store_true',
        help='display worktree paths relative instead of absolute',
    )
    opt.add_argument(
        '-U',
        '--no-user',
        action='store_true',
        help='do not substitute "~" for home directory',
    )
    opt.add_argument(
        '-F',
        '--fuzzy',
        default=DEFAULT_FUZZY,
        help='fuzzy finder program, default="%(default)s"',
    )
    opt.add_argument(
        '-h', '--help', action='store_true', help='show help message and exit'
    )
    opt.add_argument(
        '-v', '--version', action='store_true', help='show program version and exit'
    )
    opt.add_argument('-_', action='store_true', help=SUPPRESS)
    cmd = opt.add_subparsers(title='Commands')

    # Add each command ..
    for name in globals():
        if not name[0].islower() or not name.endswith('_'):
            continue

        cls = globals()[name]
        name = name[:-1]

        if hasattr(cls, 'doc'):
            desc = cls.doc.strip()
        elif cls.__doc__:
            desc = cls.__doc__.strip()
        else:
            sys.exit(f'Must define a docstring for command class "{name}".')

        title = get_title(desc)
        aliases = cls.aliases if hasattr(cls, 'aliases') else []
        cmdopt = cmd.add_parser(
            name, description=desc, aliases=aliases, help=title, add_help=False
        )
        cmdopt.add_argument(
            '-h', '--help', action='store_true', help='show help message and exit'
        )

        # Set up this commands own arguments, if it has any
        if hasattr(cls, 'init'):
            cls.init(cmdopt)

        # Set the function to call
        cmdopt.set_defaults(func=cls.run, parser=cmdopt)

    args = opt.parse_args()
    args._opt = opt
    if args._:
        try:
            args._stdout = open('/dev/tty', 'w')
        except Exception as e:
            sys.exit(f'error: can not write to terminal in shell function mode: {e}')

        shell_return = 2
    else:
        args._stdout = sys.stdout
        shell_return = 0

    if args.version:
        print_version(args)
    elif args.help or 'func' not in args:
        print_help(args)
    elif out := args.func(args):
        print(out)
        shell_return = 0

    # Code checkers like us to explicitly close files we open
    if args._:
        args._stdout.close()

    return shell_return


# COMMAND
class add_:
    "Add new worktree + branch."

    aliases = ('a',)

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            '-d',
            '--detach',
            action='store_true',
            help='add detached worktree only, i.e. without adding a new branch',
        )
        parser.add_argument(
            'worktree',
            default='',
            nargs='?',
            help='new worktree + branch to add. A name is automatically created if not specified.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        Trees.fetch(args)
        return str(Trees.create_worktree(args.worktree, args))


# COMMAND
class rm_:
    "Remove worktree + branch."

    aliases = ('r',)

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            '-k',
            '--keep-branch',
            action='store_true',
            help='remove worktree but keep branch',
        )
        parser.add_argument(
            '-f',
            '--force',
            action='store_true',
            help='force removal of worktree + branch even if '
            'untracked or unmerged changes exist.',
        )
        parser.add_argument(
            'worktree',
            default='',
            nargs='?',
            help='worktree + branch name to remove. If not specified then '
            'fuzzy finder will prompt with a list of worktrees.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        Trees.fetch(args)
        if not (name := args.worktree):
            if not (path := Trees.prompt(args)):
                return None
            name = path.name

        path = Trees.remove_worktree(name, args)
        return str(path) if path else None


# COMMAND
class cd_:
    "Change directory to specified worktree."

    aliases = ('c',)

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            'worktree',
            default='',
            nargs='?',
            help='Worktree name to change directory to. "/" is a shortcut to base repository/worktree. '
            'If not specified then fuzzy finder will prompt with a list of worktrees.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        Trees.fetch(args)
        if name := args.worktree:
            if name == '/':
                path = Trees.toplevel.path
            elif tree := Trees.get_tree(name):
                path = tree.path
            else:
                sys.exit(f'error: no worktree found with name "{name}".')
        else:
            path = Trees.prompt(args)

        return str(path) if path else None


# COMMAND
class ls_:
    "List current worktrees."

    aliases = ('l',)

    @staticmethod
    def run(args: Namespace) -> str | None:
        Trees.fetch(args)
        trees = Trees.get_trees()
        for line in reversed(trees):
            print(line, file=args._stdout)

        return None


# COMMAND
class init_:
    doc = f"""
    Output shell initialization code.
    Must be invoked using `source <({PROG})` in your shell `~/.bashrc` or
    `~/.zshrc` initialization file to create the shell alias/function by which
    you invoke this program.
    """

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            'command',
            nargs='?',
            default=DEFCMD,
            help='alternative command name, and optional default arguments, default="%(default)s"',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        if args._:
            sys.exit(
                f'Must invoke using "{PROG}", not shell function, to output shell initialization code.'
            )

        return init_code(args.command)


if __name__ == '__main__':
    sys.exit(main())
