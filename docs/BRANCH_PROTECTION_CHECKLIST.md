# Branch Protection Checklist

Use this for `main` in GitHub:

- Open repository `Settings` -> `Branches` -> `Add branch protection rule`.
- Branch name pattern: `main`.
- Enable `Require a pull request before merging`.
- Enable `Require approvals` and set at least `1`.
- Enable `Dismiss stale pull request approvals when new commits are pushed`.
- Enable `Require status checks to pass before merging`.
- Select the CI check from this repo workflow (job from `.github/workflows/ci.yml`).
- Enable `Require branches to be up to date before merging`.
- Enable `Require conversation resolution before merging`.
- Enable `Restrict who can push to matching branches` (optional but recommended).
- Enable `Do not allow bypassing the above settings`.
- Save changes.

