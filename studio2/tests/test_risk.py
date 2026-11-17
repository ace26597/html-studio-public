"""Approval rules for shell commands (CONTRACT.md, "Approvals"): a table of command lines and the
answer each must get. Misses and false alarms are held equally: each line here is one or the other."""

import shlex

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


# Commands handed to another program to run are judged by the same rules (CONTRACT.md, "Approvals",
# commands inside commands).
WRAPPED_ASKS = [
    "bash -c 'cd .. && rm -rf *'",
    "sh -c 'rm -rf ~/Documents'",
    "zsh -c 'git push origin main'",
    "bash -lc 'brew install jq'",
    "bash -e -c 'kill 123'",
    "/bin/bash -o pipefail -c 'rm -rf ../x'",
    "cd .. && bash -c 'rm -rf *'",
    "cd .. && sh -c \"echo x > notes.txt\"",
    "eval 'rm -rf ~/x'",
    "eval \"cd .. && rm -rf *\"",
    "eval git push",
    "env FOO=1 git push",
    "env -i PATH=/usr/bin rm -rf ~/x",
    "env -u HOME rm -rf ../x",
    "env -S 'rm -rf ../x'",
    "nohup sh -c 'pm2 restart all' &",
    "time rm -rf ../x",
    "timeout 10 bash -c 'kill 1'",
    "nice -n 5 rm -rf ~/x",
    "command bash -c 'docker ps'",
    "exec sh -c 'ssh host'",
    "find . -name x | xargs sh -c 'rm -rf ../y'",
    "find .. -name '*.log' -exec sh -c 'rm \"$1\"' _ {} \\;",
    "find ~ -name '*.log' -print0 | xargs -0 sh -c 'rm -f \"$@\"' _",
    "echo $(rm -rf ~/x)",
    "echo \"$(git push)\"",
    "x=`rm -rf ../y`",
    "echo \"$(echo $(rm -rf ~/x))\"",
    "bash <<< 'rm -rf ~/x'",
    "sh <<< \"cd .. && rm -rf *\"",
    "echo 'rm -rf ~/x' | sh",
    "echo 'git push' | bash",
    "printf 'cd ..\\nrm -rf *\\n' | bash",
    "bash -c \"sh -c 'eval \\\"rm -rf ~/x\\\"'\"",
]
UNREADABLE_ASKS = [
    'bash -c "$CMD"',
    "sh -c $CMD",
    'bash -c "npm test && $NEXT"',
    'bash -c "echo $(cat plan.txt)"',
    'eval "$CMD"',
    "eval $CMD",
    'eval "$(pyenv init -)"',
    "echo \"$PAYLOAD\" | sh",
    "base64 -d <<< cm0gLXJmIH4= | sh",
    "cat script.sh | bash",
    "python3 make.py | sh",
    "bash <(cat x.sh)",
    "source <(./setup --print)",
    "bash <<< \"$CMD\"",
]
# Five shells, each inside the last: deeper than the rules read.
TOO_DEEP = "bash -c 'sh -c \"bash -c ls\"'"
for _ in range(3):
    TOO_DEEP = "bash -c " + shlex.quote(TOO_DEEP)
WRAPPED_ORDINARY = [
    "git status",
    "npm install",
    "npm test",
    'python -c "print(1)"',
    "python3 -c \"import json,sys;print(json.load(sys.stdin)['pid'])\"",
    'bash -c "npm test"',
    "sh -c 'ls -la'",
    "zsh -lc 'npm run build'",
    "bash -c 'cd src && rm -rf build'",
    "sh -c 'echo $1' _ hello",
    "bash -c \"echo $HOME\"",
    "env NODE_ENV=test npm test",
    "env -i PATH=/usr/bin:/bin python3 server.py",
    "time pytest -q",
    "time curl -s https://example.com/x",
    "nohup python3 -m http.server 8765 >/dev/null 2>&1 &",
    "timeout 30 npm test",
    "nice -n 10 make",
    "command -v node",
    "exec uv run uvicorn server:app",
    'echo "$(date)"',
    "echo $(date +%s) > stamp.txt",
    "S=$(mktemp -d); ls $S",
    'id=$(python3 -c "import json;print(1)") && curl -s -X DELETE http://127.0.0.1:8331/api/cities/$id',
    'for i in $(seq 1 20); do s=$(curl -s http://127.0.0.1:8331/app); echo "$s" | grep -q running && break; sleep 1; done',
    'pid=$(pgrep -f server.py); for p in $pid $(pgrep -P $pid); do ps -o rss= -p $p; done',
    'for f in *.csv; do echo "$f $(sed -n 2p $f | cut -c1-30) $(wc -l < $f)"; done',
    "echo `date`",
    "echo 'ls -la' | sh",
    "printf 'npm test\\n' | bash",
    "cat <<'EOF' | bash\nnpm test\nls\nEOF",
    "bash <<'EOF'\nnpm test\nEOF",
    "bash <<< 'npm test'",
    "bash build.sh",
    "sh ./scripts/test.sh --fast",
    "find . -name '*.sh' -exec sh -c 'shellcheck \"$1\"' _ {} \\;",
    "find . -name '*.o' | xargs sh -c 'rm -f \"$@\"' _",
    "ls | xargs -I{} sh -c 'echo {}'",
    "eval 'echo hello'",
    "git log --format='%h %s' | head",
    "echo 'run bash -c later' > notes.txt",
    "git commit -m 'use bash -c and eval less'",
    "grep -rn 'eval(' src | wc -l",
    "uv run python -c 'import sys; print(sys.version)'",
    "env | cut -d= -f1 | sort",
    "bash -c 'sh -c \"bash -c ls\"'",
]


@pytest.mark.parametrize("command", WRAPPED_ASKS)
def test_a_wrapped_command_is_judged_by_the_same_rules(tmp_path, command):
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert risky_command(command, workspace), command


@pytest.mark.parametrize("command", UNREADABLE_ASKS + [TOO_DEEP])
def test_a_wrapped_command_that_cannot_be_read_asks(tmp_path, command):
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert risky_command(command, workspace), command


@pytest.mark.parametrize("command", WRAPPED_ORDINARY)
def test_everyday_commands_do_not_start_asking(tmp_path, command):
    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    assert risky_command(command, workspace) is None, command


def test_an_unreadable_wrapped_command_says_why(tmp_path):
    assert "cannot be read" in risky_command('bash -c "$CMD"', tmp_path)
    assert "cannot be read" in risky_command("cat x.sh | sh", tmp_path)
    assert "too deeply" in risky_command(TOO_DEEP, tmp_path)
    # The inner command's own reason is given.
    assert "outside the project" in risky_command("cd .. && bash -c 'rm -rf *'", tmp_path)


# Forms a review found that still ran without asking (CONTRACT.md, "Approvals"): the shell reads
# these lines as the command they hide.
HIDDEN_ASKS = [
    # `--` ends the shell's options: the next word is the script.
    "bash -c -- 'rm -rf ~/x'",
    "sh -ec -- 'ssh host'",
    # `$'…'` text is decoded first.
    "bash -c $'rm -rf ~/x'",
    "bash -c $'ss\\x68 host'",
    "bash -c $'\\162m -rf ~/x'",
    # A `(` with no space, and shell keywords, stand before the command.
    "(rm -rf ~/x)",
    "(bash -c 'rm -rf ~/x')",
    "(cd .. && rm -rf x)",
    "true && (git push)",
    "for i in 1; do rm -rf ~/x; done",
    "while true; do ssh host; done",
    "if true; then rm -rf ~/x; fi",
    "if true; then :; else git push; fi",
    "! rm -rf ~/x",
    "{ rm -rf ~/x; }",
    "case a in a) rm -rf ~/x;; esac",
    # The program a line runs comes from a variable or a substitution.
    "x=rm; $x -rf ~/x",
    "$CMD ~/x",
    "$(echo rm) -rf ~/x",
    "`echo rm` -rf ~/x",
    # A backslash at the end of a line joins it to the next, inside a word too.
    "r\\\nm -rf ~/x",
    "ss\\\nh host",
    "rm -rf \\\n  ~/x",
    # Programs that run the command they are given.
    "watch -n1 'ssh host'",
    "watch rm -rf ~/x",
    "script -q /dev/null ssh host",
    "script -c 'ssh host' /dev/null",
    "tmux new -d 'rm -rf ~/x'",
    "tmux new-session -d -s w 'ssh host'",
    "tmux send-keys -t w 'rm -rf ~/x' Enter",
    "screen -dm ssh host",
    "screen -dmS w bash -c 'rm -rf ~/x'",
    "at now <<< 'rm -rf ~/x'",
    "echo 'rm -rf ~/x' | at now",
    "parallel 'ssh {}' ::: a b",
    "setsid rm -rf ~/x",
    # Words piped into xargs are its arguments.
    "echo ~/x | xargs rm -rf",
    "printf '%s\\n' ~/x | xargs rm",
    "echo host | xargs ssh",
    # A variable the same line sets is not an argument the agent cannot change.
    "ARGS='; rm -rf ~/x'; bash -c \"echo $ARGS\"",
    "export ARGS=x; sh -c \"npm test -- $ARGS\"",
    "for f in *; do sh -c \"wc $f\"; done",
    "read l; bash -c \"echo $l\"",
    "bash -c \"echo $(whoami)\"",
]
# Code given to another language that mentions running a program cannot be read.
PROGRAM_TEXT_ASKS = [
    "python3 -c \"import os; os.system('rm -rf ~/x')\"",
    "python -c 'import subprocess; subprocess.run([\"ls\"])'",
    "python3 -I -c 'exec(input())'",
    "uv run python -c 'import os; os.popen(\"ls\")'",
    "perl -e 'system(\"ls\")'",
    "perl -ne 'print `ls`'",
    "ruby -e '`ls`'",
    "ruby -e 'system \"ls\"'",
    "node -e \"require('child_process').execSync('ls')\"",
    "node -e 'const {spawn} = require(\"x\"); spawn(\"ls\")'",
    "awk 'BEGIN{system(\"ls\")}'",
    "awk 'BEGIN{\"ls\" | getline x}'",
    "awk -F, '{print | \"sh\"}' a.csv",
    "php -r 'shell_exec(\"ls\");'",
    "python3 - <<'EOF'\nimport os\nos.system('rm -rf ~/x')\nEOF",
    "node - <<'EOF'\nrequire('child_process').execSync('ls')\nEOF",
]
# Ordinary lines, mostly the studio's own conventions for a project's back end: none asks.
HIDDEN_ORDINARY = [
    'sh -c "python3 server.py --port $PORT"',
    'sh -c "uvicorn app:app --host 127.0.0.1 --port $PORT"',
    'bash -c "echo ${PORT:-8000}"',
    'bash -c "npm test -- $ARGS"',
    'bash -c "cd $(pwd) && npm test"',
    'bash -c "echo `pwd`"',
    'bash -c "curl -s $STUDIO_DATA_URL/x -H \\"X-Studio-Token: $STUDIO_TOKEN\\""',
    "$HOME/.venv/bin/python server.py",
    '"$PWD/node_modules/.bin/vite" build',
    "(cd src && npm test)",
    "(npm test)",
    "for f in *.csv; do wc -l $f; done",
    "if [ -f a ]; then rm -f a; fi",
    "while sleep 1; do curl -s http://127.0.0.1:8301/ && break; done",
    "{ echo a; echo b; } > out.txt",
    "! grep -q x a.txt",
    "case $1 in a) echo a;; esac",
    "echo build | xargs rm -rf",
    "find . -name '*.tmp' | xargs rm -f",
    "ls | xargs -n1 echo",
    "watch -n5 ls",
    "python3 -c 'import platform; print(platform.system())'",
    "python3 -c \"import sqlite3; c = sqlite3.connect('a.db'); print(c.execute('select 1').fetchall())\"",
    "node -e \"console.log(/a/.exec('a'))\"",
    "node -e 'console.log(`x`)'",
    "awk '{print $1}' a.txt",
    "awk -F, 'NR>1{s+=$2} END{print s}' a.csv",
    "perl -pe 's/a/b/' a.txt",
    "python3 - <<'EOF'\nimport re\nwords = re.compile(r'treasure|spawn|secret')\nprint('operating system (any)')\nEOF",
    "python3 make.py",
    # Taken from the real logs (made generic): a program the line names in a variable, and words
    # inside a substitution or an array that only look like a program.
    'S=$(mktemp -d); C="/Applications/Some Browser.app/Contents/MacOS/Some Browser"; "$C" --headless '
    '--screenshot=$S/l.png http://127.0.0.1:8765/index.html 2>/dev/null',
    'R=$(curl -s http://127.0.0.1:8301/api/x); ID=$(echo "$R" | python3 -c "import json,sys;print(1)")',
    'UA="Mozilla/5.0"; B=(-A "$UA" -H "Accept: */*"); curl -s "${B[@]}" https://example.com/x',
    "for f in src/*.md; do d=$(dirname $f); echo \"$(basename $d)\"; done",
]


@pytest.mark.parametrize("command", HIDDEN_ASKS + PROGRAM_TEXT_ASKS)
def test_a_command_the_shell_reads_differently_from_its_text_asks(tmp_path, command):
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert risky_command(command, workspace), command


@pytest.mark.parametrize("command", HIDDEN_ORDINARY)
def test_the_studios_own_variables_and_harmless_forms_do_not_ask(tmp_path, command):
    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    assert risky_command(command, workspace) is None, command


def test_code_in_another_language_that_runs_a_program_says_so(tmp_path):
    assert "another language" in risky_command("perl -e 'system(\"ls\")'", tmp_path)
    assert "cannot be read" in risky_command("$CMD -rf ~/x", tmp_path)
    assert "cannot be read" in risky_command("x=$(which rm); $x -rf ~/x", tmp_path)
    # A program the line sets to plain text is read as that text.
    assert "outside the project" in risky_command("x=rm; $x -rf ~/x", tmp_path)


@pytest.mark.parametrize("command", [
    "printf $'\\u'", "echo $'\\x'", "echo $'\\c'", "echo $'\\U110000'", "echo $'\\UFFFFFFFF'", "echo $'\\777\\u00'",
])
def test_an_escape_the_shell_would_not_decode_does_not_break_the_reading(tmp_path, command):
    # These raised inside the `$'…'` decoder, which ended the request instead of answering.
    assert risky_command(command, tmp_path) is None, command
    assert "outside the project" in risky_command(command + "; rm -rf ~/x", tmp_path)


def test_a_fault_in_the_reading_asks_and_never_lets_the_line_through(tmp_path, monkeypatch):
    from studio2_server import risk

    def broken(*_args, **_kwargs):
        raise ValueError("a reader that broke")

    monkeypatch.setattr(risk, "_risky", broken)
    assert risk.risky_command("ls", tmp_path) == risk.CANNOT_READ
    monkeypatch.setattr(risk, "_risky", lambda *a, **k: (_ for _ in ()).throw(RecursionError()))
    assert risk.risky_command("ls", tmp_path) == risk.TOO_DEEP


def test_a_very_long_line_is_read_in_reasonable_time(tmp_path):
    # 180 KB of minified JSON in an `echo` took 25 seconds, 4,000 blank lines 18: every bracket
    # and every line break started a walk over the rest. The bound is loose on purpose.
    import json
    import time

    blob = json.dumps([{"name": f"item{i}", "tags": ["a", "b"], "n": {"x": i}} for i in range(3600)],
                      separators=(",", ":"))
    code = ";".join(f"const a{i}=(x)=>{{return x+{i}}}" for i in range(2500))
    for command in (f"echo '{blob}' > public/data.json", f"node -e '{code}'", "\n" * 4000 + "ls"):
        started = time.monotonic()
        assert risky_command(command, tmp_path) is None
        assert time.monotonic() - started < 8, f"{len(command)} characters took too long"
    # The folders before a program's name are still read.
    assert risky_command("/usr/local/bin/sudo ls", tmp_path)
    assert risky_command("echo hi; /opt/tools/gh pr list", tmp_path)
