# Credentials: what the studio holds, and where

One rule for every secret, whatever it is for. The contract is in
[CONTRACT.md](../CONTRACT.md), "Keys for other services" and "Where a key is kept".

## The rule

- **One store.** A secret is kept in the operating system's keychain, or, where there is none (a
  Linux server without a desktop), in a file only its owner can read:
  `~/.config/studio2/secrets.json`, mode 600. `STUDIO2_SECRETS=keychain|file|memory` forces a choice.
- **Named, not held.** `config.json` names a secret; it never holds one. Nothing secret is in the
  repository, in a project, in a log or in an answer of the API. A key typed into a field is never
  shown again.
- **A service's own variable wins.** When the environment already has the variable a service
  documents for its key, the studio uses that and saves nothing.
- **Nothing leaves in an export.** `.env` files and what an app saved while running are left out of
  every export.

## What there is

| Credential | Used for | Add or change it | Environment variable |
|---|---|---|---|
| Model account key | The model that does the work (an Anthropic key, a gateway, OpenAI, OpenRouter, Azure OpenAI, Gemini) | `studio setup`, the first-run screen, or Library → Models | The one the connection names (`api_key_env`), when you prefer a variable |
| Access key | Signing in from another device | Made by setup when the studio is put on a network address; `studio access-key` prints it | `STUDIO2_ACCESS_KEY` |
| Data source password | A database you attach | Library → Data sources | none |
| Connector secret | A connector (MCP server) that needs a token | Library → the connector | none |
| Service key | A service the studio itself calls. Today: **Tavily**, for web search | Library → Keys for other services, or `studio keys set tavily` | `TAVILY_API_KEY` |

Not held by the studio: the **Claude plan sign-in** (`studio login`; the agent program keeps it) and
the sign-in of a media service's own command-line tool.

## Web search, in order

1. A **connector that searches** (Tavily, Brave, Exa, Perplexity and the like), when one is attached
   to the request: choose "always" for it in the Library and the agent searches with it first.
2. **Tavily with a key**, when one is saved or `TAVILY_API_KEY` is set. If it refuses the key or the
   plan's limit is used up, the public pages answer instead and the result says why.
3. The **public search pages** (no key), as before.

## Commands

```sh
studio keys                 # what is set, and where from (never the key itself)
studio keys set tavily      # asks for the key without echoing it; or --key-file FILE
studio keys remove tavily
```

## Adding another service

Add it to `SERVICES` in `server/studio2_server/services.py` with its label, what it is used for and
the variable the service documents; read it with `services.key(settings, name)`. It then appears in
the Library block, in `studio keys` and in `GET /api/services` by itself. Do not add a second store.
