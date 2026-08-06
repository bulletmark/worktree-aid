# worktree-aid
[![PyPi](https://img.shields.io/pypi/v/worktree-aid)](https://pypi.org/project/worktree-aid/)
[![AUR](https://img.shields.io/aur/version/worktree-aid)](https://aur.archlinux.org/packages/worktree-aid/)

This is a Linux command line tool to conveniently add, remove, and change
directories for [git worktrees][gitw]. A [fuzzy finder][fzf] is used to show
current worktrees and prompt user for the name if not given.

After following the instructions in the [Installation](#installation-or-upgrade)
and [Setup](#setup) sections below, an `wt` shell command/alias is available to
use to manage git worktrees. There are 3 commands typically used:

- `wt add` (or `wt a`) to add a new worktree + branch and automatically cd to
  it. If you don't specify a worktree name, a new unique name will be
  automatically created for you.

- `wt cd` (or `wt c`) to change directory to a specified worktree. You can use
  `/` as a shortcut to the toplevel repository directory. If you don't specify a
  worktree name, then a [fuzzy finder][fzf] will prompt you with a list of
  worktrees to select from and be cd'd to.

- `wt rm` (or `wt r`) to remove a worktree + branch. If you don't specify a
  worktree name, then a [fuzzy finder][fzf] will prompt you with a list of
  worktrees to select from. The current worktree is first in the list and is the
  default selection to remove. If you remove the current worktree then you will
  be automatically cd'd to the toplevel repository directory.

There are some other options and commands available, as described in later
sections. Type `wt` to see an overall help/usage summary, or `wt <command> -h`
to see specific help/usage for any individual command.

The project homepage and latest documentation is at
https://github.com/bulletmark/worktree-aid.

## Usage

Type `wt` or `wt -h` to view the usage summary:

```
usage: wt [--basedir BASEDIR] [--relative] [-r] [--no-user] [-u]
                       [--fuzzy FUZZY] [--version] [-h]
                       {add,a,rm,r,cd,c,ls,l,init} ...

Linux command line tool to conveniently add, remove, and change directories
for git worktrees. Prompts user to select worktree using fuzzy finder if no
worktree name is given.

options:
  --basedir BASEDIR     base directory for newly added worktrees,
                        default="../worktrees/{repo}".
  --relative            display worktree paths relative instead of absolute
  -r                    toggle -R/--relative option for one-off command only
  --no-user             do not substitute "~" for user home directory
  -u                    toggle -U/--no-user option for one-off command only
  --fuzzy FUZZY         fuzzy finder program, default="fzf"
  --version             show program version and exit
  -h, --help            show help message and exit

Commands:
  {add,a,rm,r,cd,c,ls,l,init}
    add (a)             Add new worktree + branch.
    rm (r)              Remove worktree + branch.
    cd (c)              Change worktree directory.
    ls (l)              List worktrees.
    init                Output shell initialization code.
```

Type `wt <command> -h` to see specific help/usage for any
individual command:

### Command `add`

```
usage: wt add [-h] [-d] [-c] [worktree ...]

Add new worktree + branch.

positional arguments:
  worktree      new worktree + branch to add. A name is automatically created
                if not specified.

options:
  -h, --help    show help message and exit
  -d, --detach  add detached worktree only, i.e. without adding a new branch
  -c, --no-cd   do not change directory to new worktree after adding it

aliases: a
```

### Command `rm`

```
usage: wt rm [-h] [-k] [-f] [-a] [worktree ...]

Remove worktree + branch.

positional arguments:
  worktree           worktree + branch name to remove. If not specified then
                     fuzzy finder will prompt with a list of worktrees, with
                     the current worktree as the default selection.

options:
  -h, --help         show help message and exit
  -k, --keep-branch  remove worktree but keep branch
  -f, --force        force removal of worktree + branch even if untracked or
                     unmerged changes exist.
  -a, --all          remove all worktrees

aliases: r
```

### Command `cd`

```
usage: wt cd [-h] [worktree]

Change worktree directory.

positional arguments:
  worktree    Worktree name to change directory to. "/" is a shortcut to the
              toplevel repository. If not specified then fuzzy finder will
              prompt with a list of worktrees.

options:
  -h, --help  show help message and exit

aliases: c
```

### Command `ls`

```
usage: wt ls [-h]

List worktrees.

options:
  -h, --help  show help message and exit

aliases: l
```

### Command `init`

```
usage: wt init [-h] [command]

Output shell initialization code. Must be invoked using `source <(worktree-
aid)` in your shell `~/.bashrc` or `~/.zshrc` initialization file to create
the shell alias/function by which you invoke this program.

positional arguments:
  command     alternative command name, and optional default arguments,
              default="wt"

options:
  -h, --help  show help message and exit
```

## Installation or Upgrade

Python 3.10 or later is required. Install using [`uv tool`][uvtool]:

```sh
$ uv tool install worktree-aid

# To upgrade:
$ uv tool upgrade worktree-aid

# To uninstall:
$ uv tool uninstall worktree-aid
```

Or, on Arch Linux:

```sh
$ yay -S worktree-aid  # or your preferred AUR helper
```

You also need to install a fuzzy finder program such as [`fzf`][fzf] which is
the default used by `worktree-aid`. See [fuzzy finder
installation](#fuzzy-finder-integration) instructions for possible
alternatives.

## Setup

A user who wants to use `worktree-aid` must add the following line to their
`~/.bashrc` (`bash` user) or `~/.zshrc` (`zsh` user). Ensure it is added
after where your PATH is set up so that the command `worktree-aid` can be
found. This creates the `wt` wrapper command in your interactive shell session
as a tiny function.

```sh
source <(worktree-aid init)
```

Then log out and back in again to be able to use the new `wt` function in your
shell.

## Alternative Command Name

You can use an alternative command name instead of the default `wt` if you
prefer. To do this, simply append your desired command name as the first
argument to the `worktree-aid init` option in your shell initialization code.

E.g, to use the command name `wx` rather than the default `wt`, use the
following in your `~/.bashrc` or `~/.zshrc` file:

```sh
source <(worktree-aid init wx)
```

Then log out/in, and then use `wx` command instead of the default `wt`.

## Default Options

You can also set default `worktree-aid` options by appending options in the shell
initialization code, e.g:

```sh
source <(worktree-aid init "wt -R")
```

The above sets `-R` (for relative display of worktree directories) as default
for your `wt` command.

The following options are sensible candidates to set as default options:
`-B/--basedir`, `-R/--relative`, `-U/--no-user`, `-F/--fuzzy`.

## Worktree Base Directory

The `-B/--basedir` option allows you to specify a base directory for newly added
worktrees. It is set to a default as below but you can change this to any
directory you like. It can be absolute or relative where relative paths are
relative to base repository directory.

- Default base directory is `-B ../worktrees/{repo}`.
- E.g. can use `-B ../{repo}.worktrees` which is same as [VS Code] uses by
  default.
- E.g. can use `-B ~/worktrees/{repo}` to put all worktrees within a
  subdirectory of your home directory.

The following place-markers can be used in the definition of the base directory:

- `{repo}`: Substituted with the base name of the repository.
- `{user}`: Substituted with the name of the user.
- `{home}`: Substituted with the home directory of the user (also can use `~`
   at start of a path).

Most likely if you want to set a custom base directory then you will set `-B`
as a [default option](#default-options).

Note that the `--B/--basedir` setting is only relevant when adding a new
worktree using the `add` command. All other commands query your existing
worktrees so will work regardless of how or where the worktrees were created.

## Display as Relative Worktree Directories

The `git worktree list` command displays absolute directory paths, and
`worktree-aid` does also by default, but many users prefer them displayed as
shorter relative paths which `git worktree` does not provide. You
can enable it in `worktree-aid` however, by adding the `-R/--relative` option,
e.g:

```sh
$ wt l
../worktrees/worktree-aid/development 9796714 [development]
../worktrees/worktree-aid/milestone1  bc921b8 [milestone1]
../worktrees/worktree-aid/test        e6d965a [test]
                                      f76b8e0 [main]
```

Most likely you will want to set `-R` as a [default option](#default-options).
Note you can use the `-r` option on a one-off command to temporarily toggle
whatever your default `-R/--relative` option is set as.

## Fuzzy Finder Integration

[`fzf`][fzf] is the default fuzzy finder used by `worktree-aid`, but you can use
any of the popular other command line fuzzy search finders such as [`sk`][skim],
[`tv`][television], or [`fzy`][fzy].

E.g. to use [`sk`][skim], put this in your `~/.bashrc` or `~/.zshrc` file:

```sh
source <(worktree-aid init "wt -F sk")
```

You can also get fancy and add preview options etc to your fuzzy finder command line.
Most likely you will want to set `-F` as a [default option](#default-options).

## License

GPL-3.0-or-later.

[gitw]:  https://git-scm.com/docs/git-worktree
[worktree-aid]: https://github.com/bulletmark/worktree-aid
[PyPI]: https://pypi.org/project/worktree-aid
[AUR]: https://aur.archlinux.org/packages/worktree-aid
[uvtool]: https://docs.astral.sh/uv/guides/tools/#installing-tools
[fzf]: https://github.com/junegunn/fzf
[fzy]: https://github.com/jhawthorn/fzy
[skim]: https://github.com/skim-rs/skim
[television]: https://github.com/alexpasmantier/television
[Zed]: https://zed.dev/
[VS Code]: https://code.visualstudio.com/
