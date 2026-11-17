"""Approval rules for shell commands (CONTRACT.md, "Approvals"): a table of command lines and the
answer each must get. Misses and false alarms are held equally: each line here is one or the other."""

import pytest

from studio2_server.risk import risky_command

ASKS = [
    # A `cd`, `pushd` or `git -C` out of the project: the rest of the line is judged from there.
    "cd .. && rm -rf *",
    "cd ~ && rm -rf Documents",
    "cd && rm -rf Documents",
    "cd ../.. ; mv a b",
    "pushd .. && touch x",
    "cd .. && echo x > notes.txt",
    "cd .. && git commit -am x",
    "git -C ../other reset --hard",
    # git's own options before the subcommand do not hide it.
    "git -C ../other push",
    "git -c a=b push origin main",
    "git --git-dir=../x/.git --work-tree=../x push",
    # find and xargs delete what find found.
    "find ~ -name x -delete",
    "find .. -name '*.log' -exec rm {} \\;",
    "find ~/Downloads -type f -print0 | xargs -0 rm -f",
    # `>|` writes like `>`.
    "printf x >| ~/a",
    # A substitution followed by `..` can climb out.
    "rm -rf $(pwd)/..",
    "rm -rf `pwd`/../x",
    "echo x > $(pwd)/../a",
    # Downloads written outside the project.
    "curl -o ~/bin/x https://example.com/x",
    "curl -sSLo ~/bin/x https://example.com/x",
    "curl --output=/etc/x https://example.com/x",
    "curl --output-dir ~/Downloads -O https://example.com/x",
    "wget -O ~/.bashrc https://example.com/x",
    "wget --output-document=../x https://example.com/x",
    "wget -P ~/Downloads https://example.com/x",
    "cd .. && wget https://example.com/x",
    # git's own path arguments outside the project.
    "git clone https://github.com/a/b ~/dev/y",
    "git clone --depth 1 https://github.com/a/b ../y",
    "git worktree add ../wt",
    "git worktree add -b feat ../wt main",
    "git init ~/newrepo",
    "git archive -o ~/Desktop/src.zip HEAD",
    "git archive --output=../src.zip HEAD",
    # Settings that make git run a program (or read other settings), in any scope.
    "git config filter.x.clean 'sh -c x'",
    "git config core.fsmonitor 'touch MARK'",
    "git config --local core.hooksPath hooks",
    "git config -f .git/config alias.st '!sh -c x'",
    "git config --file=.git/config core.sshCommand 'sh x'",
    "git config --global core.pager 'sh x'",
    "git config core.editor vim",
    "git config core.askPass ./x",
    "git config core.gitProxy ./x",
    "git config set diff.foo.textconv cat",
    "git config diff.foo.command ./x",
    "git config diff.external ./x",
    "git config merge.ours.driver true",
    "git config --add include.path ../x",
    "git config includeIf.gitdir:~/w/.path ../x",
    "git config credential.helper store",
    "git config gpg.program ./x",
    "git config url.https://example.com/.insteadOf https://github.com/",
    "git config protocol.ext.allow always",
    "git -c a=b config core.editor vim",
    # Writing into the project's own .git folder.
    "echo x >> .git/config",
    "printf x > .git/hooks/pre-commit",
    "cp hook.sh .git/hooks/pre-commit",
    "chmod +x .git/hooks/pre-commit",
    "sed -i '' s/a/b/ .git/config",
    "tee .git/info/attributes <<'EOF'\n* filter=x\nEOF",
    "cd .git && echo x > config",
    "ln -s ../../hook.sh .git/hooks/post-checkout",
    "curl -o .git/hooks/pre-push https://example.com/x",
]

ORDINARY = [
    "cd src && npm test",
    "cd src && rm -rf build",
    "cd src/app && cd .. && rm -rf cache",
    "cd /tmp && rm -rf scratch",
    "pushd src && touch x && popd",
    "rm -rf node_modules",
    "git status",
    "git add -A && git commit -m x",
    "git checkout -- index.html",
    "git reset --hard",
    "git stash",
    "git -C src status",
    "git -c color.ui=never log --oneline",
    "find . -name '*.pyc' -delete",
    "find src -name '*.tmp' -exec rm {} +",
    "find . -name '*.o' | xargs rm -f",
    "cat > notes.md <<'EOF'\ncd .. && rm -rf *\ngit push\nEOF",
    "python - <<EOF\nimport os\nprint(os.getcwd())\nEOF",
    "printf x >| out.txt",
    "rm -rf $(pwd)/dist",
    "ln -sf /etc/passwd x",
    "ln -s ../shared/config.json config.json",
    "echo $(date) > log.txt",
    "ls .. && cat ../README.md",
    # Everyday work a review swept through: none of it asks.
    "npm ci && npm run build",
    "pnpm install && pnpm test",
    "yarn test --watch=false",
    "uv sync && uv run pytest -q",
    "pytest -q tests/",
    "(cd src && make)",
    "cd src && cd ../ && ls",
    "cd src; cd -; ls",
    "mkdir -p a/b && mv a/b c",
    "cp -r src/app src/app2",
    "ln -s src/app current",
    "sed -i '' 's/a/b/' src/x.js",
    "tar -xzf a.tgz -C vendor",
    "unzip -o a.zip -d vendor",
    'rm -rf "build output"',
    "rm -rf {w}/dist",
    'cp a.txt "{w}/src/b c.txt"',
    "find . -name '*.log' -print0 | xargs -0 rm -f",
    # Downloads into the project.
    "curl -o data.json https://example.com/data",
    "curl -sSL https://example.com/x.js -o src/x.js",
    "curl -O https://example.com/a.zip",
    "curl -o /tmp/x https://example.com/x",
    "wget -O vendor/lib.js https://example.com/x",
    "wget -q https://example.com/a.zip",
    "wget -O - https://example.com/x | head",
    # git writing its own files inside the project.
    "git clone https://github.com/a/b vendor/b",
    "git clone https://github.com/a/b",
    "git worktree add .worktrees/x",
    "git init",
    "git archive -o dist/src.zip HEAD",
    # Ordinary settings, and reading any setting.
    "git config user.name Sam",
    "git config user.email sam@example.com",
    "git config --local pull.rebase false",
    "git config core.autocrlf false",
    "git config init.defaultBranch main",
    "git config --get core.editor",
    "git config get alias.st",
    "git config core.pager",
    "git config --list",
    "git config --unset core.pager",
    # Reading the .git folder.
    "cat .git/config",
    "ls .git/hooks",
    "git rev-parse --git-dir",
    # A message given to git as a here-document is text, whatever words it holds.
    "git commit -F - <<'EOF'\nkill stale workers\ndocker compose is optional now\nEOF",
    "git tag -a v1 -F - <<'EOF'\nsudo is no longer needed\nEOF",
    "git notes add -F - <<EOF\nssh host was replaced\nEOF",
]


@pytest.mark.parametrize("command", ASKS)
def test_these_ask_first(tmp_path, command):
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert risky_command(command, workspace), command


@pytest.mark.parametrize("command", ORDINARY)
def test_ordinary_work_in_the_project_does_not_ask(tmp_path, command):
    workspace = tmp_path / "project"
    (workspace / "src" / "app").mkdir(parents=True)
    command = command.replace("{w}", str(workspace))
    assert risky_command(command, workspace) is None, command


def test_a_home_path_is_judged_by_where_it_leads(tmp_path, monkeypatch):
    # An app at ~/project: the same path written with ~ or $HOME is inside it.
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    for command in ("cp ~/project/src/a.js ~/project/src/b.js", "rm -rf ~/project/dist",
                    "rm -rf $HOME/project/dist", "echo x > ${HOME}/project/a.txt", "cd ~/project/src && rm -rf x"):
        assert risky_command(command, workspace) is None, command
    for command in ("rm -rf ~/other", "rm -rf ~", "echo x > $HOME/.zshrc", "rm -rf ~/project/../other",
                    "rm -rf ~root/x", "rm -rf $HOME/project/.."):
        assert risky_command(command, workspace), command


def test_settings_that_run_a_program_say_so(tmp_path):
    reason = risky_command("git config filter.x.clean 'sh -c x'", tmp_path)
    assert "run a program" in reason
    assert "run a program" in risky_command("printf x > .git/hooks/pre-commit", tmp_path)


def test_ln_is_judged_by_where_the_link_is_made(tmp_path):
    assert risky_command("ln -sf index.html ~/link", tmp_path)
    assert risky_command("ln -s a ../b", tmp_path)


# In an in-place project the folder is the person's own app and its .git is their repository
# (the studio keeps its versions in a separate history.git): git that can discard or rewrite work asks.
IN_PLACE_ASKS = [
    # Overwrite files in the working tree.
    "git checkout -- .", "git checkout .", "git checkout -- src/app.js", "git checkout HEAD -- src/app.js",
    "git checkout main index.html", "git checkout index.html", "git checkout -f main", "git checkout -B x",
    "git switch -C x", "git switch --discard-changes main", "git switch -f main",
    "git restore src/app.js", "git restore -W src/app.js", "git restore --staged --worktree src/app.js",
    "git restore -SW src/app.js", "git clean -fd",
    # Move the branch, or drop commits, stashes and what recovers them.
    "git reset --hard", "git reset --keep HEAD~1", "git reset --merge", "git reset HEAD~1",
    "git reset --soft HEAD~2", "git stash drop", "git stash clear", "git stash pop",
    "git branch -D old", "git branch -f main HEAD~3", "git branch -M new", "git branch --delete --force old",
    "git rebase main", "git commit --amend -m x", "git push", "git pull", "git pull --rebase",
    "git merge feature", "git cherry-pick abc123", "git revert HEAD",
    "git reflog expire --expire=now --all", "git gc --prune=now", "git tag -d v1", "git prune",
    "git -c a=b stash drop", "cd src && git checkout -- .", "git status && git reset --hard",
]
IN_PLACE_ORDINARY = [
    "git status", "git log --oneline -5", "git diff", "git diff --stat HEAD~1", "git show HEAD",
    "git ls-files", "git rev-parse HEAD", "git branch", "git branch -a", "git branch --list",
    "git stash list", "git add -A", "git commit -m x", "git blame index.html",
    # git refuses on its own to lose anything in these.
    "git checkout -b feature/x", "git checkout -b fix origin/main", "git switch -c feature/y",
    "git switch main", "git checkout feature", "git restore --staged a.js", "git restore --staged .",
    "git stash", "git stash push -m wip", "git stash save wip", "git stash -u",
    "git branch -d old", "git branch -m new", "git pull --ff-only",
    # A plain reset of the index only unstages: no commit moves and the files are untouched.
    "git reset", "git reset HEAD", "git reset -- a.js", "git reset HEAD a.js", "git reset index.html",
    "git gc", "git tag v1", "git fetch",
]


def _app(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("x")
    (tmp_path / "index.html").write_text("x")
    return tmp_path


@pytest.mark.parametrize("command", IN_PLACE_ASKS)
def test_in_place_git_that_can_lose_the_persons_work_asks(tmp_path, command):
    workspace = _app(tmp_path)
    assert risky_command(command, workspace, in_place=True), command
    if command.split()[1] not in ("push",):
        # The same command in an ordinary project only touches the studio's own copy.
        assert risky_command(command, workspace, in_place=False) is None, command


@pytest.mark.parametrize("command", IN_PLACE_ORDINARY)
def test_in_place_git_that_only_reads_or_adds_does_not_ask(tmp_path, command):
    assert risky_command(command, _app(tmp_path), in_place=True) is None, command


def test_in_place_is_known_from_the_studio_history_registry(tmp_path):
    from studio2_server import workspace as ws
    ws.register_history(tmp_path, tmp_path / "history.git")
    try:
        assert risky_command("git reset --hard", tmp_path)
    finally:
        ws.register_history(tmp_path, None)
    assert risky_command("git reset --hard", tmp_path) is None


# The studio's own command line and its own settings and state: the agent's shell could otherwise
# change where keys go or what the studio loads, without being asked.
STUDIO_CLI_ASKS = [
    "studio setup --account gateway --base-url http://127.0.0.1:9 --key-env OPENAI_API_KEY --yes",
    "studio keys set brave x", "studio keys list", "studio library add ~/skills", "studio library remove 1",
    "studio litellm stop", "studio access-key", "studio login", "studio stop", "studio start",
    "python -m studio2_server.cli setup --yes", "python3 -m studio2_server.cli keys set x y",
    "/Users/x/html-studio/studio2/.venv/bin/studio setup --yes", ".venv/bin/studio stop",
    "uv run studio setup", "cd src && studio login",
]
STUDIO_CLI_ORDINARY = [
    "studio status", "studio doctor", "studio library", "studio --help", "studio",
    "python -m studio2_server.cli status", ".venv/bin/studio doctor",
    "echo studio setup", "grep -r 'studio setup' docs",
]


@pytest.mark.parametrize("command", STUDIO_CLI_ASKS)
def test_the_studios_own_command_line_asks_when_it_changes_something(tmp_path, command):
    reason = risky_command(command, tmp_path)
    assert reason and "studio's own settings" in reason, command


@pytest.mark.parametrize("command", STUDIO_CLI_ORDINARY)
def test_the_studios_own_command_line_that_only_reads_does_not_ask(tmp_path, command):
    assert risky_command(command, tmp_path) is None, command


def test_the_studios_own_settings_and_state_ask_even_from_inside_the_project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".config" / "studio2").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    data = tmp_path / "studio" / "data"
    workspace = data / "projects" / "p_1" / "workspace"
    (workspace / "src").mkdir(parents=True)
    asks = ["echo x > ~/.config/studio2/config.json", "rm -rf ~/.claude/plugins",
            "cp x.json ~/.claude/plugins/installed_plugins.json", "mv ~/.claude/settings.json old.json",
            "echo x > ../events.jsonl", f"rm -rf {data}/litellm", f"sed -i '' s/a/b/ {data}/data-sources.json",
            f"cd {data} && touch x", "curl -o ~/.config/studio2/config.json https://example.com/x"]
    for command in asks:
        reason = risky_command(command, workspace, studio_places=[data])
        assert reason and "studio's own settings" in reason, command
    # Its own project folder, inside the data folder, is ordinary work.
    for command in ("rm -rf src/dist", "echo x > notes.txt", f"rm -rf {workspace}/dist", "cat ~/.claude/settings.json",
                    "ls ~/.config/studio2", f"cat {data}/projects/p_1/events.jsonl"):
        assert risky_command(command, workspace, studio_places=[data]) is None, command
    # An app brought in place whose folder holds the studio's data (the studio's own repository).
    repo = tmp_path / "studio"
    reason = risky_command("rm -rf data/projects", repo, in_place=True, studio_places=[data])
    assert reason and "studio's own settings" in reason
    assert risky_command("rm -rf build", repo, in_place=True, studio_places=[data]) is None
