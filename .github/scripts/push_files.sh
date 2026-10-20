#!/usr/bin/env bash
# 사용: push_files.sh "커밋 메시지" 파일1 [파일2 ...]
# 지정한 파일만 커밋·푸시한다. 그사이 다른 커밋(사용자 푸시, 다른 워크플로)이 올라와
# push 가 거절되면 받아서 다시 올린다(최대 4회). 충돌 시 이번 실행의 결과를 우선한다.
set -u
MSG="$1"; shift
git config user.name "dealondo-bot"
git config user.email "actions@users.noreply.github.com"
for f in "$@"; do git add -- "$f" 2>/dev/null || true; done   # 없는 파일이 섞여도 나머지는 올린다
if git diff --staged --quiet; then
  echo "변경 없음 — 커밋 생략"
  exit 0
fi
git commit -q -m "$MSG"
for i in 1 2 3 4; do
  if git push -q; then
    echo "푸시 완료 (${i}번째 시도)"
    exit 0
  fi
  echo "푸시 거절 — 원격 변경을 받아 다시 시도 (${i})"
  git pull -q --rebase --autostash -X theirs origin "${GITHUB_REF_NAME:-main}" || { git rebase --abort 2>/dev/null; sleep 5; }
done
echo "::warning::푸시 4회 실패 — 다음 실행에서 다시 반영됩니다"
exit 0
