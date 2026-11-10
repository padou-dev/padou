# Contributing to padou

Thanks for helping! padou is a small project: bug reports, test results on your hardware, ideas and pull requests are all welcome.

## Reporting a bug

Open an [issue](https://github.com/padou-dev/padou/issues/new/choose) and include:

- the output of `padou doctor` (it shows your system, model server, model and config problems; it contains no secrets);
- what you typed, what you expected, and what happened (a screenshot helps; `/screenshot` saves one as an SVG);
- the model you use. Many oddities are the model's, not padou's: `padou bench` shows how well a model works with padou.

Security problems: please **don't** open a public issue; see [SECURITY.md](SECURITY.md).

## Setting up

```bash
git clone https://github.com/padou-dev/padou
cd padou
uv sync                          # creates .venv with padou and its dependencies
uv run pytest -q                 # the tests: they need no model server and touch nothing outside temp folders
uv tool install --editable .     # the `padou` command, running straight from your clone
```

You don't need a GPU or a model to work on most of padou: the tests use fake model servers.

## Making a change

- **Keep it small-model friendly.** Every word in the system prompt and every tool description is read on every question by a 4B model on a laptop. Short and precise beats complete.
- **Safety rules live in one place:** `tools.py` (what may be read or run) and `writes.py` (what may be changed). A change there needs tests, including the cases that must be refused.
- **All three systems.** padou runs on Linux, macOS and Windows; differences go through `compat.py`. GitHub Actions runs the tests on all three for every push and pull request.
- **Tests:** add or update tests in `tests/` for what you change. `uv run pytest -q` must pass.
- **Plain language** in messages and docs: padou is used by beginners. Say what happened and what to do next.
- **CHANGELOG.md:** add a line under *Unreleased* for anything a user would notice.

Then open a pull request describing what changed and why, and how you tested it.

## Releasing (maintainers)

### Once: set up PyPI trusted publishing

1. Create an account on [pypi.org](https://pypi.org) and turn on two-factor authentication.
2. Go to *Your projects → Publishing* (for the first release: *Add a new pending publisher*) and fill in: project name `padou`, owner `padou-dev`, repository `padou`, workflow `release.yml`, environment `pypi`.
3. On GitHub: *Settings → Environments → New environment* named `pypi`. Optionally add yourself as a required reviewer, so every upload waits for your OK.

No password or token is stored anywhere: PyPI trusts uploads that come from that workflow in this repository.

### Each release

1. Set the version in `pyproject.toml` and `src/padou/__init__.py`, and turn *Unreleased* in `CHANGELOG.md` into `## X.Y.Z (date)`.
2. Commit, push, and wait for the tests to pass on all three systems.
3. Tag and push: `git tag -a vX.Y.Z -m "padou X.Y.Z" && git push origin vX.Y.Z`
4. On GitHub: *Releases → Draft a new release*, pick the tag, title `padou X.Y.Z`, paste the changelog section, *Publish*. The `release` workflow checks the tag matches the version, runs the tests, builds and uploads to PyPI. Everyone's padou then shows the update notice within a day, and `/update` installs it.

### The AUR package

The package files are in `contrib/aur/`. First time: create an account on [aur.archlinux.org](https://aur.archlinux.org) and add your SSH public key in *My Account*. Then:

```bash
git clone ssh://aur@aur.archlinux.org/padou.git aur-padou
cp contrib/aur/PKGBUILD aur-padou/
cd aur-padou
updpkgsums                         # fills in the checksum of the release tarball
makepkg -si                        # build, run the tests and install it locally, as a check
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO && git commit -m "padou X.Y.Z" && git push
```

For each new release: change `pkgver` (and set `pkgrel=1`) in `PKGBUILD`, and repeat from `updpkgsums`.
