# Framework 1.2.2 rollout (#954)

Sam authorized merging and deploying the framework fix. This app vendors server 1.2.2, built from framework commit
67ee645 (merged as aee6202), and opts into `aiSpendView: 'app'`. Its current view definition and grant repair remain.
No data migration, permission expansion, UI change or paid AI call is part of this rollout.

Acceptance: repository checks pass on a disposable VM; live health is good; anonymous API access remains refused;
the app-owned monthly spend view and cave_spend read grant remain intact; a rollback image is retained.

Rollback: restore the prior image in the existing compose service, preserving volumes and environment. The prior
server already uses database side data, so rollback does not return to file-based sessions.

Validation and live release evidence are recorded on the PR and in the Den rollout item.

Pre-deploy validation: Node 24.22.2 on a disposable Proxmox VM; typecheck, 91 tests and build passed. No paid AI call. Framework regression and PostgreSQL controls passed in PR67.
