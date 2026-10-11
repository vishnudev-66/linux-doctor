# Security & data-safety model

## Principles
1. **Read-only by default.** No repair, deletion, process-kill or firewall change exists in 1.0.
2. **No arbitrary command execution.** `core/runner.py` runs only registered argument lists (no shell), validated placeholders,
   fixed PATH, 10 s default timeout, 512 KB output cap. Unknown keys raise errors (tested).
3. **AI output is untrusted.** It is redacted on the way out, parsed against a strict schema on the way in, and commands are
   display-only. `ai/policy.py` labels commands `read_only` or `review`; mutating verbs/flags are always `review`.
   Logs sent as context are fenced as untrusted data to reduce prompt injection. Remote AI endpoints are blocked by default.
4. **Never root.** Privileged data is reported as "could not inspect", not escalated.

## Database safety (SQLite)
- Directory `0700`, DB/WAL/backups/reports `0600`; umask set while creating files.
- Parameterized SQL only; fixed table names; CHECK + FOREIGN KEY constraints; `trusted_schema=OFF`.
- `secure_delete=ON`, WAL, integrity check on every open; transactions for multi-row writes.
- Evidence/titles are redacted (emails, IPs, MACs, tokens, long secrets, usernames, hostname, /home paths) *before* storage.
- Retention purge (default 90 days), delete-all with VACUUM, online backup API.
- API keys are never stored in the DB or settings in 1.0 (only local Ollama is supported).
- **Not encrypted at rest.** Use full-disk/home-directory encryption (LUKS / fscrypt) if the machine can be physically accessed.

## Reports
HTML escapes all content and carries a restrictive CSP; CSV neutralises formula injection; redaction is on by default.

## Honest limits
Heuristics, not an audit, antivirus or SIEM. A clean result never proves the system is secure. Redaction is best-effort;
review reports before sharing. Report vulnerabilities privately to the maintainers (fill in your contact in the repo).
