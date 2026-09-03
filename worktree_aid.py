"""
Command line tool to easily add, remove, and change directories for git
worktrees. Prompts user with list of worktrees using fuzzy finder.
"""

import getpass
import os
import shlex
import string
import subprocess
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROG = Path(__file__).stem.replace('_', '-')
ENVVAR = '_' + PROG.replace('-', '_').upper()
HOME = Path.home()

# Default git commit hash length to display in worktree list. Can be changed
# using command line option.
DEF_HASH_LEN = 7

# Default command name (shell function) this program is installed as. Can be
# changed using command line option.
DEF_CMD = 'wt'

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

QUIET_RETURN = 3  # Return code to indicate quiet exit from shell function

# Template for the shell code injected into user's shell session
SHELLCODE = """
!cmd() {
    local !envvar=""
    export !envvar
    !envvar=$(!prog!args "$@")
    local r=$?

    if [ $r -ne 0 ]; then
        if [ $r -eq !QUIET_RETURN ]; then
            return 0
        fi
        return $r
    fi

    cd -- "$!envvar"
}
"""

# List of internal command classes, populated by @Command decorator
commands = []


def shell_func_name_valid(name: str) -> bool:
    "Return True if shell function name is valid"
    if not name or name[0] in string.digits:
        return False

    valids = set(string.ascii_letters + string.digits + '_')
    return all(c in valids for c in name)


def init_code(cmd: str) -> str:
    "Return shell init code as string"

    # We need to change the template delimiter because the standard
    # delimiter "$" is too common in regular shell code .
    class CTemplate(string.Template):
        delimiter = '!'

    arglist = cmd.split(maxsplit=1)
    if len(arglist) > 1:
        cmd, opts = arglist
        args = f' {opts}'
    else:
        args = ''

    if not shell_func_name_valid(cmd):
        sys.exit(f'error: invalid shell command name "{cmd}".')

    return CTemplate(SHELLCODE.strip()).substitute(
        envvar=ENVVAR,
        cmd=cmd,
        prog=shlex.quote(sys.argv[0]),
        args=args,
        QUIET_RETURN=QUIET_RETURN,
    )


def run(
    cmd: Sequence[str],
    *,
    stdin: str | None = None,
    stdout: Any = subprocess.PIPE,
    ignore_error: bool = False,
) -> str | None:
    "Run command and return stdout"
    capture = stdout == subprocess.PIPE
    try:
        res = subprocess.run(cmd, stdout=stdout, text=capture, input=stdin)
    except Exception as e:
        sys.exit(f'error: failed to run command "{cmd}": {e}')

    if res.returncode != 0:
        if ignore_error:
            return None

        sys.exit(res.returncode)

    return res.stdout.strip() if capture and res.stdout else ''


def get_title(desc: str, name: str) -> str:
    "Return single title line from command description"
    res = []
    for line in desc.splitlines():
        if line := line.strip():
            res.append(line)
            if line.endswith('.'):
                return ' '.join(res)

    sys.exit(f'Must end {name} command description with a full stop.')


def unexpanduser(path: Path) -> Path:
    "Return path name, with $HOME replaced by ~ (opposite of Path.expanduser())"
    if path.parts[: len(HOME.parts)] != HOME.parts:
        return path

    return Path('~', *path.parts[len(HOME.parts) :])


def path_as_displayed(path: Path, args: Namespace) -> str:
    "Return displayed path"
    if args.relative:
        return os.path.relpath(path)
    elif not args.no_user:
        return str(unexpanduser(path))

    return str(path)


def generate_new_name(excludes: set[str]) -> str:
    "Generate a new worktree name"
    from coolname import generate_slug

    for _ in range(100 + len(excludes)):
        if (name := generate_slug(2)) not in excludes:
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


def validate_name(name: str) -> None:
    "Ensure worktree name is valid"
    if any(c in name for c in ' \t\r\n'):
        sys.exit(f'error: worktree name "{name}" can not contain whitespace.')

    if '\\' in name:
        sys.exit(f'error: worktree name "{name}" can not contain "\\".')

    if name.startswith(('-', '.')):
        sys.exit(f'error: worktree name "{name}" can not start with "-" or ".".')


def rm_parents(path: Path) -> None:
    "Remove empty parent directories of worktree path"
    for depth, p in enumerate(path.parents):
        if not p or not p.is_dir() or p.samefile(HOME):
            break

        # Stop removing parent directories if any files exist in this directory
        if any(p.iterdir()):
            break

        try:
            p.rmdir()
        except Exception:
            break

        # Limit removing parent directories
        if depth >= 2:
            break


class Branches:
    "Data for local and remote branches"

    def __init__(self) -> None:
        self.local = set()
        self.remote = set()
        self.remote_labels = set()

        cmd = ('git', '--no-pager', 'branch', '-al', r'--format=%(refname) %(symref)')
        lines = run(cmd) or ''

        for line in lines.splitlines():
            refname, _, symref = line.strip().partition(' ')
            if not refname or symref:
                continue

            fields = refname.split('/', 2)
            if len(fields) == 3 and fields[0] == 'refs':
                match fields[1]:
                    case 'heads':
                        self.local.add(fields[2])
                    case 'remotes':
                        remote, _, branch = fields[2].partition('/')
                        if remote and branch:
                            self.remote_labels.add(remote)
                            self.remote.add(branch)

    def labels(self) -> set[str]:
        "Return all names in all branch paths"
        labels = self.remote_labels.copy()
        labels.update(b for p in self.remote for b in p.split('/'))
        labels.update(b for p in self.local for b in p.split('/'))
        return labels


class Tree:
    "Data for an individual worktree"

    def __init__(self, path: Path, args: Namespace) -> None:
        self.path = path
        self.path_display = path_as_displayed(path, args)
        self.path_user = str(path if args.no_user else unexpanduser(path))
        self.head = ''
        self.branch = ''
        self.attrs = set()


class Trees:
    "Class to manage the collection of worktrees"

    def __init__(self, args: Namespace) -> None:
        "Get worktrees"
        try:
            cwd = Path.cwd().resolve()
        except Exception:
            sys.exit('error: failed to resolve current working directory.')

        cwdparts = cwd.parts
        trees = []
        tree = None
        phere = pindex = -1
        hash_len = args.hash_len
        out = run(('git', 'worktree', 'list', '--porcelain', '-z')) or ''
        for line in out.split('\0'):
            if not line:
                continue

            field, _, value = line.partition(' ')
            value = value.rstrip('\r\n')
            match field:
                case 'worktree':
                    if value:
                        path = Path(value)
                        plen = len(path.parts)
                        if path.parts == cwdparts[:plen] and plen > phere:
                            phere = plen
                            pindex = len(trees)

                        trees.append(tree := Tree(path, args))
                case 'HEAD':
                    if tree and value and hash_len >= 0:
                        if hash_len == 0:
                            hash_len = len(value)
                        tree.head = value[:hash_len]
                case 'branch':
                    if tree and value:
                        tree.branch = value.split('/', maxsplit=2)[-1]
                case _:
                    if tree and field:
                        tree.attrs.add(field)

        if not trees:
            sys.exit('error: no worktrees found.')

        if pindex > 0:
            trees = [trees[pindex]] + trees[:pindex] + trees[pindex + 1 :]
            self.toplevel = trees[1]
        else:
            self.toplevel = trees[0]

        self.trees = trees
        self.current = trees[0] if pindex >= 0 else None
        self.fuzzy = args.fuzzy
        self.hash_len = hash_len

    def get_trees(self) -> list[str]:
        "Fetch string list of worktrees"
        hash_len = self.hash_len
        width = max(len(t.path_display) for t in self.trees)
        trees = []
        for t in self.trees:
            line = [f'{t.path_display:{width}}']
            if hash_len > 0:
                line.append(f'{t.head:{hash_len}}')

            if t.branch:
                line.append(f'[{t.branch}]')
            elif not any(a not in t.attrs for a in ('detached', 'bare')):
                line.append('detached')

            if t.attrs:
                line.append(' '.join(sorted(t.attrs)))

            trees.append(' '.join(line))

        return trees

    def get_tree(self, name: str) -> Tree:
        "Return worktree with given name, or None if not found"
        # Check for shortcut to top-level worktree
        if name == '/':
            return self.toplevel

        # Check for shortcut to current worktree
        if name == '.':
            if not self.current:
                sys.exit('error: not inside any worktree.')

            return self.current

        # If name starts with a slash/tilde, then assume it is a path to the worktree
        if name and name[0] in ('/', '~'):
            path = Path(name).expanduser().resolve(strict=False)
            if not path.is_dir():
                sys.exit('Worktree directory "{name}" does not exist')

            for tree in self.trees:
                if tree.path.samefile(path):
                    return tree

            sys.exit('Worktree directory "{name}" not found')

        for tree in self.trees:
            if tree.branch == name:
                return tree

        for tree in self.trees:
            if tree.path.name == name:
                return tree

        sys.exit(f'error: no worktree found with name "{name}".')

    def prompt(self) -> Tree | None:
        "Prompt user to select a worktree using fuzzy finder"
        if not (trees := self.get_trees()):
            sys.exit('error: no worktrees present.')

        try:
            cmd = shlex.split(self.fuzzy)
        except Exception as e:
            sys.exit(f'error: failed to parse fuzzy command "{self.fuzzy}": {e}')

        stdin = '\n'.join(trees)
        out = run(cmd, stdin=stdin, ignore_error=True) or ''
        line = out.strip()

        if not line or line not in trees:
            return None

        return self.trees[trees.index(line)]

    def add_worktree(self, name: str, args: Namespace) -> Path:
        "Create a new worktree and branch with the given name"
        if '{worktree}' not in (pathstr := args.path):
            sys.exit(
                f'error: -P/--path "{pathstr}" must contain "{{worktree}}" placeholder.'
            )

        br = Branches()

        if name:
            validate_name(name)
        else:
            # If no name is given, generate a new name that does not conflict
            # with existing branches or worktrees
            excludes = br.labels() | {t.path.name for t in self.trees}
            excludes.update(n for t in self.trees if (n := t.path.parent.name))
            name = generate_new_name(excludes)

        try:
            pathstr = pathstr.format(
                repo=self.toplevel.path.name,
                worktree=name.replace('/', '-'),
                user=getpass.getuser(),
                home=str(HOME),
            )
        except Exception as e:
            sys.exit(f'error: failed to format -P/--path "{pathstr}": {e}')

        path = Path(pathstr).expanduser()
        path = (self.toplevel.path / path).resolve()

        if path.exists():
            dpath = path_as_displayed(path, args)
            sys.exit(f'error: worktree path "{dpath}" already exists.')

        cmd = ['git', 'worktree', 'add', str(path)]
        if args.detach:
            cmd.append('--detach')
        else:
            # Create a new branch unless a local branch, or potentially
            # trackable remote branch, with the same name already exists
            if name not in (br.local | br.remote):
                cmd.append('-b')
            cmd.append(name)

        run(cmd, stdout=args._stdout)
        return path

    def remove_worktree(self, tree: Tree, br: Branches, args: Namespace) -> Path | None:
        "Remove the given worktree and branch"
        if tree == self.toplevel:
            print(
                f'warning: not removing top-level worktree "{tree.path_user}"',
                file=sys.stderr,
            )
            return None

        if tree == self.current:
            # Change to the top-level worktree directory before deleting this worktree
            # because we are removing the current directory
            os.chdir(newpath := self.toplevel.path)
        else:
            newpath = None

        cmd = ['git', 'worktree', 'remove']
        if args.force:
            cmd.append('--force')

        cmd.append(str(tree.path))
        if run(cmd, stdout=args._stdout, ignore_error=True) is None:
            return None

        print(f'Removed worktree "{tree.path_user}"', file=args._stdout)

        # Also remove parent directories of the worktree if they are empty
        rm_parents(tree.path)

        if tree.branch and not args.keep_branch and tree.branch in br.local:
            cmd = ('git', 'branch', '-D' if args.force else '-d', tree.branch)
            run(cmd, stdout=args._stdout, ignore_error=True)

        return newpath


def main() -> int:
    "Main code"
    # Main returns a status code:
    # 0 = Directory written to stdout. Calling script will "cd" to that
    #     worktree directory and return error code for that cd command result.
    # 1 = Error/message already written to stderr via sys.exit(). Calling script
    #     will silently quit and return that error code.
    # 2 = Error/message already written to stderr from argparse. Calling script
    #     will silently quit and return that error code.
    # QUIET_RETURN = Caller will silently quit and return exit code 0.

    # We need to determine if we are running in a shell function.
    running_in_shell = ENVVAR in os.environ

    # Python 3.14 argparse added color help/usage output but has a bug when
    # outputting to a device other than stdout, so we override auto-detection.
    # See https://github.com/python/cpython/issues/156144
    if running_in_shell and sys.version_info[:2] == (3, 14):
        os.environ['FORCE_COLOR'] = '1'

    # Parse arguments
    opt = ArgumentParser(description=__doc__, add_help=False)
    opt.add_argument(
        '-P',
        '--path',
        default=PATH,
        help='directory path template for newly added worktrees, default = "%(default)s". '
        'Can use {worktree}, {repo}, {user}, and {home} placeholders. '
        'Must contain {worktree} at least.',
    )
    opt.add_argument(
        '-r',
        '--relative',
        action='count',
        default=0,
        help='toggle absolute/relative display of worktree paths, default is absolute. '
        'Can be specified on command line again to toggle your default setting.',
    )
    opt.add_argument(
        '-u',
        '--no-user',
        action='count',
        default=0,
        help='toggle substitution of "~" for user home directory, default is to substitute. '
        'Can be specified on command line again to toggle your default setting.',
    )
    opt.add_argument(
        '-F',
        '--fuzzy',
        default=DEFAULT_FUZZY,
        help='fuzzy finder program, default = "%(default)s"',
    )
    opt.add_argument(
        '-H',
        '--hash-len',
        type=int,
        default=DEF_HASH_LEN,
        help='length of git commit hash to display in list, default = %(default)d, '
        '0 = display full hash, -1 = do not display hash',
    )
    opt.add_argument(
        '-V', '--version', action='store_true', help='show program version and exit'
    )
    opt.add_argument(
        '-h', '--help', action='store_true', help='show help message and exit'
    )
    cmd = opt.add_subparsers(title='Commands')

    # Add each command ..
    for cls in commands:
        name = cls.__name__

        if hasattr(cls, 'doc'):
            desc = cls.doc.strip()
        elif cls.__doc__:
            desc = cls.__doc__.strip()
        else:
            sys.exit(f'Must define a docstring for command class "{name}".')

        title = get_title(desc, name)

        aliases = [name[0]]
        if hasattr(cls, 'extra_aliases'):
            aliases.extend(cls.extra_aliases)

        cmdopt = cmd.add_parser(
            name, description=desc, aliases=aliases, help=title, add_help=False
        )

        # Set up this commands own arguments, if it has any
        if hasattr(cls, 'init'):
            cls.init(cmdopt)

        # Add help option for this command. Use a different dest to not
        # overwrite the global help option
        cmdopt.add_argument(
            '-h',
            '--help',
            dest='help_command',
            action='store_true',
            help='show help message and exit',
        )

        # Set the function to call
        cmdopt.set_defaults(func=cls.run, parser=cmdopt)

    args = opt.parse_args()
    args._running_in_shell = running_in_shell

    # Work out the state of the toggle options
    args.relative &= 1
    args.no_user &= 1

    if running_in_shell:
        try:
            args._stdout = open('/dev/tty', 'w')
        except Exception as e:
            sys.exit(f'error: can not write to terminal in shell function mode: {e}')

        shell_return = QUIET_RETURN
    else:
        args._stdout = sys.stdout
        shell_return = 0

    if args.help:
        opt.print_help(args._stdout)
    elif args.version:
        print_version(args)
    elif not hasattr(args, 'func'):
        opt.print_help(args._stdout)
    elif args.help_command:
        args.parser.print_help(args._stdout)
    elif out := args.func(args):
        print(out)
        shell_return = 0

    # Code checkers like us to explicitly close files we open
    if running_in_shell:
        args._stdout.close()

    return shell_return


def Command(command: type) -> type:
    "Decorator to add given command class to list of commands"
    commands.append(command)
    return command


@Command
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
        newpath = None
        for name in args.worktree or ['']:
            if path := trees.add_worktree(name, args):
                newpath = path if path.is_dir() else None

        return str(newpath) if newpath and not args.no_cd else None


@Command
class rm:
    "Remove worktree + branch."

    extra_aliases = ('remove',)

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
                sys.exit('error: cannot specify a worktree name with -a/--all option.')

            deltrees = [t for t in trees.trees if t != trees.toplevel]
        elif not args.worktree:
            if not (tree := trees.prompt()):
                return None
            deltrees = [tree]
        else:
            deltrees = []
            for name in args.worktree:
                if (tree := trees.get_tree(name)) not in deltrees:
                    deltrees.append(tree)

        br = Branches()
        newpath = None

        for tree in deltrees:
            if path := trees.remove_worktree(tree, br, args):
                newpath = path

        return str(newpath) if newpath else None


@Command
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
        tree = trees.get_tree(args.worktree) if args.worktree else trees.prompt()
        return str(tree.path) if tree else None


@Command
class ls:
    "List worktrees."

    extra_aliases = ('list',)

    @staticmethod
    def run(args: Namespace) -> None:
        trees = Trees(args)
        for line in reversed(trees.get_trees()):
            print(line, file=args._stdout)


@Command
class init:
    doc = f"""
    Output shell initialization code and set default options.
    Should be invoked using `source <({PROG} init)` in your shell `~/.bashrc` or
    `~/.zshrc` initialization file to create the shell alias/function by which
    you invoke this program. You can also append preferred default options to
    the command name, e.g. `source <({PROG} init \"wt -r\")`.
    """

    @staticmethod
    def init(parser: ArgumentParser) -> None:
        parser.add_argument(
            'command',
            nargs='?',
            default=DEF_CMD,
            help='alternative command name, and optional default arguments, default = "%(default)s"',
        )

    @staticmethod
    def run(args: Namespace) -> str:
        if args._running_in_shell:
            sys.exit(
                f'Must invoke using "{PROG}", not shell function, to output shell initialization code.'
            )

        return init_code(args.command)


if __name__ == '__main__':
    sys.exit(main())
