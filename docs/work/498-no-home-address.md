# #498 Deal Hunter: no home-network address in the public code

Tier: **green**. Priority: high. Repo: sambena/deal-hunter (**public**).

## Outcome
The repository shows no private network address. The API help text in the app describes how to reach the API without naming one: it uses the address the page itself was loaded from, or a placeholder like `http://<this-server>:<port>`.

## Today
`static/app.js` (the API help section, around line 1519) has a literal private LAN address in the example URL.

## Do
- Build the example URL from `location` (protocol and host) plus the API port, or use a placeholder. Then the help is right for any install.
- Check the whole repository, including docs, tests and config examples, for any other private addresses, hostnames or internal paths, and remove or replace them.

## Do not
Do not rewrite git history; that's the owner's decision. Don't change behaviour.

## Acceptance
- A search of the working tree for private address ranges (10.x, 172.16-31.x, 192.168.x) and internal hostnames finds nothing except a deliberate placeholder or test fixture. List what you searched for in the PR, without repeating the address.
- The API help shows a correct URL in the real page (dev run). `pytest` passes.

## Note for the owner
The old address stays in git history. Removing it from history means rewriting the repo; that's the owner's call, and with a private LAN address the risk is low.
