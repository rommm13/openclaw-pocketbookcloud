# Architecture

The PocketBook Cloud skill deliberately separates conversational intent from deterministic integration logic.

```text
User
  ↓
OpenClaw + SKILL.md
  ↓ intent, disambiguation, confirmation
scripts/pocketbook.py
  ↓
pocketbook_lib/
  ├─ api.py        API, password login and contract validation
  ├─ state.py      private session state, atomic writes and locks
  ├─ transport.py  safe downloads from untrusted file URLs
  ├─ formats.py    EPUB/FB2/ZIP handling and archive hardening
  ├─ models.py     search, disambiguation, progress and notes
  ├─ operations.py batch flows, backup, chat delivery and deletion
  └─ common.py     errors, limits and shared primitives
  ↓
PocketBook Cloud
```

## Agent/helper boundary

OpenClaw interprets the user's request and owns the conversation. The Python helper does not reason about intent: it validates state, paths, formats and API responses, then executes only the selected operation. Book text, notes, filenames and provider metadata are treated as untrusted data, never as agent instructions.

## Authentication

A new session uses the user's normal PocketBook Cloud email and password. `auth login` asks for the email in the terminal and reads the password through hidden `getpass` input. The password is used only for password login and is never written to disk.

For consumer API compatibility the helper also uses PocketBook browser-client interoperability values. They are publicly shipped by the provider's own web client and are not user credentials.

After login, only the access/refresh session is persisted locally. State stays outside Git; the directory is 0700 and the session file 0600. Refresh is serialized with a lock and written atomically.

## Safe transport

API calls are restricted to the expected HTTPS origin. File downloads use URLs returned by the library, but the PocketBook Authorization header is never forwarded to file hosts.

Before connecting, the helper requires HTTPS, rejects URL userinfo and alternate ports, and resolves the hostname to public addresses only. Private, loopback, link-local and similar addresses are rejected. Redirect destinations are validated again. The connection is pinned to the already validated sockaddr so DNS is not repeated between validation and connect.

## Files and archives

The format layer distinguishes a real EPUB from a transport ZIP, validates FB2/FB2.ZIP, and handles the Telegram edge case where an FB2 document may arrive as XML.

Archive handling rejects traversal, absolute paths, symlinks and special entries, flattened-name collisions, excessive entry counts, expansion limits and suspicious compression ratios. Temporary files are materialized separately and committed atomically; existing targets are not silently overwritten.

## Upload verification

HTTP 2xx is not enough to claim success. After upload, the helper resolves the indexed Cloud record and verifies identity data including hash, filename, byte size and format. If the body was accepted but the resulting record cannot be verified, the operation remains unverified.

## Batch operations

Multi-book transfers begin with preflight: all inputs are validated before side effects, book count and estimated size are calculated, and large batches require a separate user confirmation. Defaults are more than 10 books or more than 100 MiB. The confirmed retry is explicit through `--confirmed`.

## Deletion

Deletion intentionally spans two user messages.

1. `delete prepare` resolves exactly one book and writes short-lived pending state bound to the account and book identity.
2. The agent ends the turn.
3. Only the next explicit user message is passed to `delete confirm`.
4. Before the destructive request, the helper rechecks account and identity, and consumes pending state before network mutation.

Legacy confirmation codes are inert. An uncertain provider result is not promoted into invented success.

## Test boundary

Tests do not require a real account or live API. Contract fixtures pin expected response shapes; hardening tests cover filesystem state, URL transport, archive handling, race-sensitive state and destructive workflows. The public repository adds release-gate regressions.
