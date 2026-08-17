"""
Command line tool to easily add, remove, and change directories for git
worktrees. Prompts user with list of worktrees using fuzzy finder.
"""

import getpass
import os
import shlex
import shutil
import subprocess
import sys
from argparse import SUPPRESS, ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

PROG = Path(__file__).stem.replace('_', '-')
HOME = Path.home()
HASH_LEN = 7

# Default command name (shell function) this program is installed as. Can be
# changed using command line option.
DEFCMD = 'wt'

# Default fuzzy finder. Can be changed using command line option.
DEFAULT_FUZZY = 'fzf'

# Relative path template from top level repo dir to directory for newly created
# worktrees. Can be changed using command line option. Can use the following
# placeholders:
# {worktree} = worktree name (compulsory somwhere)
# {repo} = top-level repo name
# {user} = current user name
# {home} = current user home directory
PATH = '../worktrees/{repo}/{worktree}'

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


def get_title(desc: str, name: str) -> str:
    "Return single title line from command description"
    res = []
    for line in desc.splitlines():
        line = line.strip()
        res.append(line)
        if line.endswith('.'):
            return ' '.join(res)

    sys.exit(f'Mwoust end {name} command description with a full stop.')


def unexpanduser(path: Path) -> Path:
    "Return path name, with $HOME replaced by ~ (opposite of Path.expanduser())"
    if path.parts[: len(HOME.parts)] != HOME.parts:
        return path

    return Path('~', *path.parts[len(HOME.parts) :])


def relpath(path: Path) -> str:
    "Return path relative to current working directory"
    return os.path.relpath(path)


def generate_new_name(exists: set[str]) -> str:
    "Generate a new worktree name"
    from coolname import generate_slug

    for _ in range(100 + len(exists)):
        if (name := generate_slug(2)) not in exists:
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
    if hasattr(args, 'parser'):
        args.parser.print_help(args._stdout)
    else:
        args._opt.print_help(args._stdout)


def validate_name(name: str) -> None:
    "Ensure worktree name is valid"
    if ' ' in name:
        sys.exit(f'error: worktree name "{name}" can not contain spaces.')

    if '\\' in name:
        sys.exit(f'error: worktree name "{name}" can not contain "\\".')

    if name.startswith(('-', '.')):
        sys.exit(f'error: worktree name "{name}" can not start with "-" or ".".')


def copyfile(src: Path, tgt: Path, stdout: Any, args: Namespace) -> None:
    "Copy a file from src worktree to target worktree"
    sfile = relpath(src) if args.relative else str(src)
    tfile = relpath(tgt) if args.relative else str(tgt)

    if src.exists() or src.is_symlink():
        if stdout:
            print(f'Copying "{sfile}" to "{tfile}"', file=stdout)

        if tgt.is_dir() and not tgt.is_symlink():
            shutil.rmtree(tgt)

        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tgt, follow_symlinks=False)
    elif tgt.is_symlink() or tgt.exists():
        if stdout:
            print(f'Removing "{tfile}"', file=stdout)

        if tgt.is_dir() and not tgt.is_symlink():
            shutil.rmtree(tgt)
        else:
            tgt.unlink()


def copyfiles(src: Path, tgt: Path, stdout: Any, args: Namespace) -> None:
    "Copy git changes from src worktree to target worktree"
    cmd = ['git', '-C', str(src), 'status', '--porcelain']
    if args.ignored:
        cmd.extend(['--ignored', '-uall'])

    for line in run(cmd).splitlines():
        if not (line := line.strip()):
            continue

        status, rest = line.split(maxsplit=1)

        if status in ('R', 'C') and ' -> ' in rest:
            for file in rest.split(' -> ', maxsplit=1):
                file = file.strip().strip('"')
                copyfile(src / file, tgt / file, stdout, args)
        else:
            file = rest.strip('"')
            copyfile(src / file, tgt / file, stdout, args)


def rm_parents(path: Path) -> None:
    "Remove empty parent directories of worktree path"
    for p in path.parents:
        if not p.is_dir() or p.samefile(HOME):
            break

        # Stop removing parent directories if any files exist in this directory
        if any(p.iterdir()):
            break

        try:
            p.rmdir()
        except Exception:
            break


@dataclass
class Tree:
    "Data for an individual worktree"

    path: Path
    path_display: str
    head: str = ' ' * HASH_LEN
    branch: str = ''


class Trees:
    "Class to manage the collection of worktrees"

    def __init__(self, args: Namespace) -> None:
        "Get worktrees"
        trees = []
        tree = None
        cwdparts = Path.cwd().resolve().parts
        phere = pindex = -1
        for line in run(('git', 'worktree', 'list', '--porcelain')).splitlines():
            if not (line := line.strip()):
                continue

            if len(fields := line.split(maxsplit=1)) < 2:
                continue

            field, value = fields

            if field == 'worktree':
                path = Path(value)

                if args.relative:
                    path_display = relpath(path)
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
                    tree.branch = value.split('/', maxsplit=2)[-1]

        if not trees:
            sys.exit('error: no worktrees found.')

        if pindex > 0:
            trees = [trees[pindex]] + trees[:pindex] + trees[pindex + 1 :]
            self.toplevel = trees[1]
        else:
            self.toplevel = trees[0]

        self.trees = trees
        self.current = trees[0]
        self.args = args

    def get_trees(self) -> list[str]:
        "Fetch string list of worktrees"
        width = max(len(t.path_display) for t in self.trees)
        trees = []
        for t in self.trees:
            branch = f'[{t.branch}]' if t.branch else 'detached'
            trees.append(f'{t.path_display:{width}} {t.head} {branch}')

        return trees

    def get_tree(self, name: str) -> Tree | None:
        "Return worktree with given name, or None if not found"
        if name == '/':
            # Shortcut to top-level worktree
            tree = self.toplevel
        elif name == '.':
            # Shortcut to current worktree
            tree = self.current
        else:
            tree = None
            for t in self.trees:
                if t.branch == name:
                    tree = t
                    break

                if t.path.name == name:
                    tree = t

        return tree

    def prompt(self) -> Tree | None:
        "Prompt user to select a worktree using fuzzy finder"
        if not (trees := self.get_trees()):
            sys.exit('error: no worktrees to remove.')
        line = run(shlex.split(self.args.fuzzy), stdin='\n'.join(trees)).strip()
        if not line or line not in trees:
            return None

        return self.trees[trees.index(line)]

    def get_or_ask_tree(self, name: str) -> Tree | None:
        "Return worktree with given name, or ask user for name"
        if name:
            if not (tree := self.get_tree(name)):
                sys.exit(f'error: no worktree found with name "{name}".')
        else:
            # If no worktree name is given, prompt user to select one
            tree = self.prompt()

        return tree

    def create_worktree(self, name: str) -> Path:
        "Create a new worktree and branch with the given name"

        # Get set of existing branch names
        blist = run(('git', '--no-pager', 'branch', '--list')).splitlines()
        branches = {b.split(maxsplit=1)[-1] for b in blist}

        if not name:
            # If no name is given, generate a new name that does not conflict
            # with existing worktrees or branches
            excludes = {t.path.name for t in self.trees} | branches
            excludes.update(b.split('/', maxsplit=1)[0] for b in branches if '/' in b)
            name = generate_new_name(excludes)
        else:
            validate_name(name)

        if '{worktree}' not in (pathstr := self.args.path):
            sys.exit(
                f'error: -P/--path "{pathstr}" must contain "{{worktree}}" placeholder.'
            )

        worktree = name.replace('/', '-')

        try:
            pathstr = pathstr.format(
                repo=self.toplevel.path.name,
                worktree=worktree,
                user=getpass.getuser(),
                home=str(HOME),
            )
        except Exception as e:
            sys.exit(f'error: failed to format -P/--path "{pathstr}": {e}')

        path = Path(pathstr).expanduser()
        path = (self.toplevel.path / path).resolve()

        cmd = ['git', 'worktree', 'add', str(path)]
        if self.args.detach:
            cmd.append('--detach')
        else:
            if name not in branches:
                cmd.append('-b')
            cmd.append(name)

        run(cmd, stdout=self.args._stdout)
        return path

    def remove_worktree(self, name: str, tree: Tree | None = None) -> Path | None:
        "Remove the worktree and branch with the given name"
        if not tree and not (tree := self.get_tree(name)):
            sys.exit(f'error: no worktree found with name "{name}".')

        if tree == self.toplevel:
            print(
                f'warning: not removing top-level worktree "{tree.path}"',
                file=sys.stderr,
            )
            return None

        if tree == self.current:
            # Change to the top-level worktree directory before deleting this worktree
            # because we are removing the current directory
            os.chdir(newpath := self.toplevel.path)

            # Recompute the relative path to the worktree from the new current directory
            if self.args.relative:
                tree.path_display = relpath(tree.path)
        else:
            newpath = None

        cmd = ['git', 'worktree', 'remove']
        if self.args.force:
            cmd.append('--force')

        cmd.append(str(tree.path))
        run(cmd, stdout=self.args._stdout)

        print(f'Removed worktree "{tree.path_display}"', file=self.args._stdout)

        # Also remove parent directories of the worktree if they are empty
        rm_parents(tree.path)

        if tree.branch and not self.args.keep_branch:
            cmd = ('git', 'branch', '-D' if self.args.force else '-d', tree.branch)
            run(cmd, stdout=self.args._stdout, ignore_error=True)

        return newpath


class Command:
    commands: ClassVar = []

    @classmethod
    def add(cls, command: Any) -> None:
        "Add command class to list of commands"
        cls.commands.append(command)


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
        '-P',
        '--path',
        default=PATH,
        help='directory path template for newly added worktrees, default="%(default)s". '
        'Can use {worktree}, {repo}, {user}, and {home} placeholders. '
        'Must contain {worktree} at least.',
    )
    opt.add_argument(
        '-R',
        '--relative',
        action='store_true',
        help='display worktree paths relative instead of absolute',
    )
    opt.add_argument(
        '-r',
        action='store_true',
        help='toggle -R/--relative option for one-off command only',
    )
    opt.add_argument(
        '-U',
        '--no-user',
        action='store_true',
        help='do not substitute "~" for user home directory',
    )
    opt.add_argument(
        '-u',
        action='store_true',
        help='toggle -U/--no-user option for one-off command only',
    )
    opt.add_argument(
        '-F',
        '--fuzzy',
        default=DEFAULT_FUZZY,
        help='fuzzy finder program, default="%(default)s"',
    )
    opt.add_argument(
        '-V', '--version', action='store_true', help='show program version and exit'
    )
    opt.add_argument(
        '-h', '--help', action='store_true', help='show help message and exit'
    )
    opt.add_argument('-_', action='store_true', help=SUPPRESS)
    cmd = opt.add_subparsers(title='Commands')

    # Add each command ..
    for cls in Command.commands:
        name = cls.__name__

        if hasattr(cls, 'doc'):
            desc = cls.doc.strip()
        elif cls.__doc__:
            desc = cls.__doc__.strip()
        else:
            sys.exit(f'Must define a docstring for command class "{name}".')

        title = get_title(desc, name)
        cmdopt = cmd.add_parser(
            name, description=desc, aliases=name[0], help=title, add_help=False
        )

        # Set up this commands own arguments, if it has any
        if hasattr(cls, 'init'):
            cls.init(cmdopt)

        # Add the help option for this command
        cmdopt.add_argument(
            '-h', '--help', action='store_true', help='show help message and exit'
        )

        # Set the function to call
        cmdopt.set_defaults(func=cls.run, parser=cmdopt)

    args = opt.parse_args()
    args._opt = opt

    if args.r:
        args.relative = not args.relative

    if args.u:
        args.no_user = not args.no_user

    # Note that '_' is a hidden option and only set when this program is
    # invoked from the shell function
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
    elif args.help or 'func' not in args or '-h' in sys.argv or '--help' in sys.argv:
        print_help(args)
    elif out := args.func(args):
        print(out)
        shell_return = 0

    # Code checkers like us to explicitly close files we open
    if args._:
        args._stdout.close()

    return shell_return


@Command.add
class add:
    "Add new worktree + branch."

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            '-d',
            '--detach',
            action='store_true',
            help='add detached worktree only, i.e. without adding a new branch',
        )
        parser.add_argument(
            '-c',
            '--no-cd',
            action='store_true',
            help='do not change directory to new worktree after adding it',
        )
        parser.add_argument(
            'worktree',
            nargs='*',
            help='new worktree + branch to add. A name is automatically created if not specified. '
            'Can also specify an existing branch name to create a new worktree for that branch.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        trees = Trees(args)
        retpath = None
        for name in args.worktree or ['']:
            if (path := trees.create_worktree(name)) and not retpath:
                retpath = path

        return str(retpath) if retpath and not args.no_cd else None


@Command.add
class rm:
    "Remove worktree + branch."

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
            '-a',
            '--all',
            action='store_true',
            help='remove all worktrees',
        )
        parser.add_argument(
            'worktree',
            nargs='*',
            help='worktree + branch name to remove. "." is a shortcut for the current worktree. '
            'If not specified then fuzzy finder will prompt with a list of worktrees, with the '
            'current worktree as the default selection.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        trees = Trees(args)

        if args.all:
            if args.worktree:
                sys.exit('error: cannot specify a worktree name with --all option.')

            names = [
                n for t in trees.trees if t != trees.toplevel and (n := t.path.name)
            ]
        else:
            names = args.worktree or ['']

        retpath = None
        tree = None
        for name in names:
            # If no worktree name is given, prompt user to select one
            if not name and not (tree := trees.prompt()):
                return None

            if (path := trees.remove_worktree(name, tree)) and not retpath:
                retpath = path

            tree = None

        return str(retpath) if retpath else None


@Command.add
class cd:
    "Change worktree directory."

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            'worktree',
            default='',
            nargs='?',
            help='Worktree name to change directory to. "/" is a shortcut to the top-level repository. '
            'If not specified then fuzzy finder will prompt with a list of worktrees.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        trees = Trees(args)
        tree = trees.get_or_ask_tree(args.worktree)
        return str(tree.path) if tree else None


@Command.add
class fetch:
    "Fetch changes from another worktree."

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            '-q',
            '--quiet',
            action='store_true',
            help='suppress output of copied files',
        )
        parser.add_argument(
            '-i',
            '--ignored',
            action='store_true',
            help='also copy ignored files',
        )
        parser.add_argument(
            'worktree',
            default='',
            nargs='?',
            help='Worktree name to fetch changes from. "/" is a shortcut to the top-level repository. '
            'If not specified then fuzzy finder will prompt with a list of worktrees.',
        )

    @staticmethod
    def run(args: Namespace) -> str | None:
        trees = Trees(args)
        if tree := trees.get_or_ask_tree(args.worktree):
            srcpath = tree.path
            if srcpath.samefile(tgtpath := trees.current.path):
                sys.exit(f'error: can not fetch from the same worktree "{srcpath}".')

            stdout = None if args.quiet else args._stdout
            copyfiles(srcpath, tgtpath, stdout, args)

        return None


@Command.add
class ls:
    "List worktrees."

    @staticmethod
    def run(args: Namespace) -> str | None:
        trees = Trees(args)
        for line in reversed(trees.get_trees()):
            print(line, file=args._stdout)

        return None


@Command.add
class init:
    doc = f"""
    Output shell initialization code and set default options.
    Must be invoked using `source <({PROG} init)` in your shell `~/.bashrc` or
    `~/.zshrc` initialization file to create the shell alias/function by which
    you invoke this program. You can also append preferred default options to
    the command name, e.g. `source <({PROG} init \"wt -R\")`.
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
