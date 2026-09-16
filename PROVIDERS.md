# Provider operations

The current provider boundary is deliberately conservative. An adapter may transfer content through an official FTP/FTPS/SFTP route, generate a provider-compatible CSV/IPTC artifact, and record the result. It must not scrape, click a contributor portal, bypass MFA, or infer that a transfer equals a submitted asset.

## Current implementation

- `mock`: fully automated fake upload, metadata submit and approved status; used by tests and local smoke checks.
- `Shutterstock`: official FTPS transfer adapter; final Submit page action is `MANUAL_REQUIRED`.
- `Pond5`: documented official FTP transfer + Apply CSV export target; portal submit is `MANUAL_REQUIRED`.
- `Adobe Stock`: documented qualified-account SFTP transfer + CSV export target; contributor portal submit/status is `MANUAL_REQUIRED`.
- `Alamy`: documented FTP transfer with embedded IPTC target; contributor management/status is `MANUAL_REQUIRED`.
- `Storyblocks`: documented sFTP footage transfer + CSV export target; releases and portal submit are `MANUAL_REQUIRED`.

See the dated capability matrix and official links in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Provider limits are configuration and documentation inputs, not hardcoded assumptions.
