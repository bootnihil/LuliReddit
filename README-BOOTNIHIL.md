# Ilay BN — one-time setup

This is a source-based experimental workaround, **not a verified r/all fix**. The upstream app already has an `AllFeedSdui` GraphQL query; the patch uses it if website r/all returns HTTP 200 with zero posts. If Reddit has also disabled that GraphQL operation, another solution will be needed.

The workflow builds a **separate app** (`com.bennybar.luli_for_reddit.bootnihil`) and labels its launcher **Ilay BN**. It does not touch the original Ilay app or its data. Sign in to the separate app again via website; your existing app's session cannot automatically migrate.

## First run (Windows PowerShell)

1. Extract this ZIP anywhere.
2. In PowerShell run `& "<EXTRACTED-FOLDER>\setup.ps1"` (replace the folder path).
3. If GitHub CLI is not installed, install it with `winget install --id GitHub.cli -e` and restart PowerShell.
4. If not signed into GitHub CLI, run `gh auth login`, then rerun setup.
5. Visit https://github.com/bootnihil/LuliReddit/actions . GitHub may require enabling Actions for a fresh fork, then run **Ilay BN — patched APK** manually.

The script clones to `C:\Dev\android\apps\Ilay\LuliReddit`, creates a **persistent local keystore** in `C:\Dev\android\_secrets\Ilay`, uploads its encrypted values to GitHub Actions Secrets, and commits the workflow and patch script into your fork's `main` branch. It never deletes or overwrites an existing repository or existing keystore. Keep the private signing keystore backed up; changing it later would break in-place updates.

GitHub Actions checks upstream every day. It publishes a fresh APK only when an upstream app version changes (or the patch revision changes). Existing releases are left untouched. Workflow stops if its source expectations no longer match upstream, rather than shipping an unreviewed build. APKs are found in your fork's GitHub Releases; Ilay BN's in-app updater also points to your fork.

## Risks and limitations

- No guarantees that Reddit's `r/all` data is available through GraphQL.
- This unofficial website-login mode may violate Reddit's terms or cause rate limiting/account restrictions; use at your own risk.
- CI requires Android SDK 37 and upstream's Gradle dependencies, which are downloaded at build time. Initial build is unverified until Actions reports success.
- Changes to upstream's layout can break the patch. Workflows fail closed and require adaptation.
- The local signing key is critical. Keep a backup. Do not commit it into git.
- If you already have an existing cloned fork under the target path with local changes, setup will stop rather than overwrite them.
