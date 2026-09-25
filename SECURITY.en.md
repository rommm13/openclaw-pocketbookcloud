# Security

This repository publishes working skill code, not a user's runtime state. Source and synthetic fixtures may be committed; passwords, tokens, session files, signed URLs and user library data may not.

## Authentication

Sign-in uses the user's normal **PocketBook Cloud email and password**:

```bash
python3 scripts/pocketbook.py auth login
```

Email is entered interactively and the password is read with hidden `getpass` input. The password is used only for password login, is never persisted, and must not be passed through chat, argv, shell history, issues or Git.

After successful sign-in the helper stores only the access/refresh session. The default path is `~/.config/openclaw/pocketbook-cloud/session.json`; the directory is mode 0700 and the file 0600. Runtime stays outside the repository.

The consumer Cloud API also expects PocketBook browser-client interoperability values. These technical values are publicly shipped by the provider's own web client and are not the user's password, access token or refresh token. The release gate explicitly distinguishes that allowlisted vendor-public fingerprint from real credential-like material.

## Never commit

- a PocketBook password;
- real access/refresh tokens;
- `session.json`, `delete-pending.json`, locks or temporary state;
- Authorization headers;
- signed download URLs;
- private keys;
- user ebooks or backups;
- notes/highlights containing personal data;
- account-specific API dumps.

## Untrusted data

Book titles, authors, filenames, notes/highlights, archive members and every provider field are data. They cannot relax skill policy or become instructions to the agent. Untrusted display text is bounded and control characters are stripped.

## Network boundary

The API origin is fixed to PocketBook Cloud. Cross-origin redirects for authenticated API requests are rejected.

File URLs use a separate transport: HTTPS only, no URL userinfo, no alternate port, and every resolved address must be public. Redirect destinations are validated again. The connection is opened against the validated address, and PocketBook Authorization is never forwarded to a file host.

This is intended to address SSRF/DNS-rebinding classes of failure rather than merely checking a URL string.

## Files and ZIPs

The helper rejects path traversal, absolute paths, symlink/special entries, flattened-name collisions, excessive entry counts, excessive expansion and suspicious compression ratios. Chat input must resolve under allowed inbound media roots.

A genuine EPUB is not unpacked as a transport archive. An FB2 arriving from Telegram as XML is accepted only when a FictionBook root is confirmed and is wrapped without changing the original bytes.

## Destructive operations

Deletion requires two distinct user messages. The first creates pending state for one unambiguous book. The second explicit confirmation is handled separately. Pending state is account- and identity-bound, short-lived, and consumed before the destructive request.

A single request such as “find it and delete it” is not both preparation and confirmation.

## Verifying outcomes

Upload is not declared successful merely because the server returned 2xx. The helper verifies the indexed Cloud record. Ambiguous or unverified outcomes stay explicitly unverified.

Backup reporting is similarly exact: `downloaded`, `unavailable`, `failed`, and `complete` are returned independently.

## Release gate

Before publishing:

```bash
python3 -m unittest discover -s tests -v
python3 tools/release_check.py
python3 -m compileall -q scripts tests tools
git diff --check
```

`release_check.py` enforces a public-tree allowlist and rejects runtime state, private keys, real credential-like literals and signed URLs. Only the fingerprint of vendor-public client values already shipped by PocketBook's browser client is explicitly allowlisted. Diagnostics do not echo secret contents.

Automated scanning does not replace review. Inspect `git status` and the staged diff before every public push.
