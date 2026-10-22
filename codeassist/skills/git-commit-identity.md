---
name: git-commit-identity
description: Establish and verify git commit identity before the first push of a project, so contributors are attributed correctly and never collide with real GitHub accounts.
slash: commit-identity
---

# Git Commit Identity Setup

Before the FIRST push of any project, establish the commit identity so GitHub
attributes contributions correctly and no real account is accidentally claimed.

## Why
GitHub matches a commit to an account by the **author email**. A generic or
colliding identity (e.g. a name that is a real GitHub handle, or GitHub's
`@users.noreply.github.com` pattern) can attribute your commits to someone else,
showing up as an unwanted contributor. Fixing it after push requires an amend +
force-push; doing it up front avoids all of that.

## Workflow

1. **Get the identity.** Ask the user for:
   - Display name they want shown (e.g. their GitHub login or real name).
   - Email linked to their GitHub account (the one used on their other commits).
   - If they have a preferred default, use it; otherwise confirm before setting.

2. **Set it — both local and global:**
   ```bash
   # local: persists with the code on the mounted volume (survives container rebuild)
   git config user.name  "<NAME>"
   git config user.email "<GITHUB-LINKED-EMAIL>"
   # global: default for all projects in this container (re-applied each session)
   git config --global user.name  "<NAME>"
   git config --global user.email "<GITHUB-LINKED-EMAIL>"
   git config --global init.defaultBranch main
   ```

3. **Never use colliding identities.** Reject:
   - Names that could be a real person's GitHub handle (`CodeAssist`, `admin`, `assistant`).
   - GitHub's no-reply pattern (`*@users.noreply.github.com`) — not uniquely theirs.
   - Empty/unset identity.

4. **Verify before first push:**
   ```bash
   git log --format='%an <%ae>' 2>/dev/null | sort | uniq -c
   ```
   Confirm only the intended contributor(s) appear. For a brand-new repo this is
   just the initial commit; for an existing repo, ensure no stray generic authors.

5. **Remediation if a wrong identity was already pushed:**
   - Tip commit: `git commit --amend --author="<NAME> <EMAIL>"` (or rewrite history
     for older commits with `git rebase --interactive --root`).
   - Push safely: `git push --force-with-lease origin <branch>`.
   - Confirm via API that only intended contributors remain:
     `gh api repos/{owner}/{repo}/contributors -q '.[].login'`
   - Note: GitHub's contributor list caches; a fresh query may lag the push by minutes.

## Persistence note
Global config lives in `/root` inside the container and may be lost if the
container is recreated from an image. The per-repo local config persists on the
mounted volume. Re-running this skill each session re-applies the global default,
so always run it at project start even if you set it globally before.

## Optional: dedicated AI-agent identity
If tool-generated commits should be credited separately, use a unique, non-colliding
identity (e.g. `project-agent+<name>@<domain>`) so it never clashes with a real
account and stays distinct from the human's commits.
