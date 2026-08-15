# Security

Please do not disclose vulnerabilities in public GitHub issues.

Report suspected vulnerabilities privately to **unicartbytekena@gmail.com** with reproduction steps and relevant non-secret request/response details.

Never include passwords, API keys, OAuth tokens, database URLs, payment credentials, session tokens, or private keys in a report.

## Repository policy

- Real `.env` files and databases are ignored.
- Production secrets are supplied through the hosting environment.
- CI runs the repository security preflight on every push and pull request.
- Exposed credentials must be revoked or rotated; deleting a value from the latest commit alone is not sufficient.
