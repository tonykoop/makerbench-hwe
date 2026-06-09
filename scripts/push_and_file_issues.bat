@echo off
REM ============================================================================
REM  MakerBench: commit + push the pre-public hardening branch, open a PR, and
REM  file the nine backlog issues. Double-click to run. Requires Git for Windows
REM  and an authenticated GitHub CLI (gh auth status).
REM ============================================================================
setlocal
cd /d "%~dp0.."

echo.
echo [1/5] Removing stale git lock if present...
if exist ".git\index.lock" del /f /q ".git\index.lock"

echo [2/5] Staging and committing changes...
git add -A
git commit -m "chore: pre-public hardening - py3.10 UTC fix, CI matrix, SECURITY/CITATION, issue+PR templates, README tree"

echo [3/5] Pushing branch chore/pre-public-hardening...
git push -u origin chore/pre-public-hardening

echo [4/5] Opening pull request...
gh pr create --title "Pre-public hardening" --body "Fixes the Python 3.10 datetime.UTC import bug, adds a 3.10-3.12 CI matrix, ships SECURITY.md + CITATION.cff + issue/PR templates, and refreshes the README repo-layout tree. Backlog items filed as separate issues."

echo [5/5] Filing the nine backlog issues...
"%ProgramFiles%\Git\bin\bash.exe" "%~dp0file_backlog_issues.sh"

echo.
echo Done. Review the PR and issues on GitHub.
pause
endlocal
