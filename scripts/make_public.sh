#!/usr/bin/env bash
# Rebuild the local branch `public`: ONE commit holding exactly the current HEAD tree, with no
# earlier history and a no-reply author address. The working history of this repository
# predates the plugin being self-contained and must not be published; the public repository
# starts from this single commit. Nothing is pushed.
# Also writes .release/ (untracked): the catalog entry and PR text with that commit's id filled in.
#
#   scripts/make_public.sh            # (re)creates branch `public` from HEAD
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || { echo "commit your changes first"; exit 1; }
NAME="${PUBLIC_AUTHOR_NAME:-jrezmo}"
EMAIL="${PUBLIC_AUTHOR_EMAIL:-jrezmo@users.noreply.github.com}"
VERSION="$(sed -n 's/^version: "\(.*\)"/\1/p' plugin.yaml)"
# Dated like HEAD, so the same tree always gives the same commit id (re-running is harmless and
# the id written into .release/ stays the one to push).
WHEN="$(git log -1 --format=%cI HEAD)"
SHA=$(GIT_AUTHOR_NAME="$NAME" GIT_AUTHOR_EMAIL="$EMAIL" GIT_COMMITTER_NAME="$NAME" GIT_COMMITTER_EMAIL="$EMAIL" \
  GIT_AUTHOR_DATE="$WHEN" GIT_COMMITTER_DATE="$WHEN" \
  git commit-tree "HEAD^{tree}" -m "1guy $VERSION: 1guy, one companion surface on top of Hermes

Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>")
git branch -f public "$SHA" >/dev/null
# The catalog entry and the PR text name the release commit, which only exists now.
mkdir -p .release
sed -e "s/0000000000000000000000000000000000000000/$SHA/g" -e '/^# The sha below is a placeholder/,/^# image URLs) to/d' catalog/1guy.yaml >.release/1guy.yaml
sed -e "s/<SHA>/$SHA/g" -e '/^> PR description for the entry/,/^> `.release\/PR.md`/d' docs/PR.md >.release/PR.md
echo "$SHA" >.release/SHA
echo "release files: .release/1guy.yaml (catalog entry), .release/PR.md (PR text), .release/SHA"
echo "public -> $SHA  ($(git rev-list --count public) commit, tree of $(git rev-parse --short HEAD), version $VERSION)"
