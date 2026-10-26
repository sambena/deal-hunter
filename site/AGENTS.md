# Deal Hunter working agreements

- This repository is private until it is proven, but write it as if it were public: no internal hosts, addresses,
  server paths, ids, secrets or anyone's personal data in code, docs, tests or PR text. Use placeholders and `.env`.
- Sign-in, sessions, security headers, phone pairing and AI settings belong to `@cougarcave/server` and
  `@cougarcave/ui`. Change them there (cougar-cave-ui), not here.
- Every route says what it needs (`signedIn`, `browserOnly`); every state-changing request needs `X-Cave-Request: 1`.
- Errors are `{ "error": <code>, "message": <text> }`. Never show a stack trace or a secret.
- Unit tests (Vitest) for every route and component, with a refusal beside a valid control.
