---
name: ship-change
description: Finish a vkb-hotas change and publish it - make sure README.md and CLAUDE.md cover the diff, apply the API_VERSION rule, run syntax/privacy prechecks, commit with a descriptive message and the required attribution, and push to GitHub over SSH. Use this whenever the user says "update docs, commit, and push", "commit this", "push it", "make sure everything is documented", or similar, for this repository.
---

# Ship a vkb-hotas change

Commit and push only when the user asks. Every step below exists because skipping it caused a
real problem: undocumented behaviour, a garbled README sentence pushed to GitHub, or a page and
server out of sync.

## 1. Docs match the diff

Run `git diff --stat` and read the actual changes. Then check both audiences:
- **README.md (users):** new features, how to use them, and anything an existing user has to do
  after updating. Examples: restart HOTAS Mapper; existing stick maps don't get new default views
  automatically; a new option in `/etc/default/vkb-hotas`.
- **CLAUDE.md (future Claude sessions):** architecture and data-format changes, any new
  `API_VERSION` and what it introduced (keep the History list), design decisions the user made and
  why, and new gotchas. Keep it about things that need several files to understand.

If you edit prose, re-read the edited paragraph in full afterwards. Python `str.replace` edits on
Markdown have split sentences before.

## 2. The API_VERSION rule

The mapper page is re-read from disk on every request, so after a reinstall an old running server
can serve a new page. Bump `API_VERSION` in `mapper/vkb-mapper.py`, **and** the page's
`if (l.api !== N)` check, whenever the page relies on new server behaviour or a new data format.
Examples: new layout fields, new drawings the server must accept, a changed pin format. An old
server would drop or rewrite what it doesn't know when it saves. Add the new number to the
History line in CLAUDE.md.

## 3. Prechecks

```bash
.claude/skills/ship-change/scripts/precheck.sh
```

- **Syntax:** all Python and shell, plus the udev rule template.
- **Privacy:** personal paths and tokens in tracked *and new* files.
- **API_VERSION:** server and page must agree.
- **Doc reminders:** flags README/CLAUDE.md if unchanged.

Fix anything that fails before committing. A README/CLAUDE.md "unchanged" note is a prompt to
think, not an error.

If the diff touches `install.sh`, `uninstall.sh`, anything under `service/`, or moves or renames
files, also run the `test-installer` sandbox
(`.claude/skills/test-installer/scripts/sandbox_install.sh`). The user's next `sudo ./install.sh`
is otherwise the first real test.

## 4. Commit

- Stage deliberately (`git add -A` is fine when `git status` shows only intended files).
- Write a subject line that says what users get, and a body with bullet points grouped by area
  (feature, server, docs).
- End the message with the attribution lines the session tells you to use (the system reminder's
  `Co-Authored-By` line). Don't invent your own.
- Use a heredoc (`git commit -F - <<'EOF'`) so quotes and special characters survive.

## 5. Push

`git push` (origin is SSH: `git@github.com:reagentking/vkb-hotas.git`).
- **If SSH fails with "Host key verification failed",** the environment lacks GitHub's host key.
  Don't add it yourself; push once over HTTPS with the GitHub CLI's credentials instead:
  `git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push https://github.com/reagentking/vkb-hotas.git main`.
  The user can fix SSH by running `ssh -T git@github.com` themselves.
- **Never force-push.** If something wrong was already pushed, fix it in a follow-up commit.

## 6. Report

- the commit hash and subject;
- what the docs now cover, especially anything you added;
- that local `main` matches `origin/main` (check with `git status -sb`);
- whether the user needs to reinstall (`sudo ./install.sh`, which they run themselves) and restart
  HOTAS Mapper to get the change.
