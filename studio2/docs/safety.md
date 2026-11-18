# Safety and limits

Moved from the [front page](../../README.md).

## Safety

The agent runs commands as you, like a coding agent in a terminal. A project's own back end
runs as you too.

- **Approvals.** Risky shell commands ask first: `sudo`, installing software, `git push`,
  changing or deleting things outside the project, connecting to another machine, and
  similar. File writes outside the project are refused. Making an app live and buying
  generated media ask every time, even in a project set to "Allow everything". These rules
  read the command text. They catch ordinary mistakes; they are not a sandbox.
- **What the agent can reach.** Its shell does not see the secrets the studio was started
  with: variables whose name looks like a key, token, secret or password are blank for it. A
  project's back end gets no keys at all and reaches models and attached databases through
  the studio. On Bedrock, Vertex and Foundry the agent needs the cloud's credentials, so use a
  role limited to model calls.
- **Where keys live.** In the system keychain, or in an owner-only file where there is none.
  Not in the config file, not in a project, not in this repository.
- **Never put it on a public address.** By default it listens on this computer only. You can
  add an address on your own private network, behind an access key. There are no user
  accounts and no isolation between projects yet, so whoever can open the studio can run
  commands on the machine.
- **One trusted person per machine,** until accounts and a container per project exist. The
  agent runs as the same user as the studio, so an agent that set out to could call the
  studio's own interface on this computer and answer its own approval card.
- **Your sign-in to a project's app.** With "Let the agent use my sign-in" ticked in a
  project's menu (on for apps made in the studio, off for an app you brought, refused on a
  shared studio), the agent's own browser opens that project's pages with your session, so it
  sees what you see and anything it clicks there it clicks as you. The studio keeps the session
  in memory only, forgets it when the studio stops or you untick the option, keeps only the
  cookies that app itself set, and never puts a value in a file, a log or what the agent is
  told. It cannot stop the app's own pages from showing what your browser holds, and the agent
  writes those pages. Untick it for an app whose account matters.
- **Checks are loose on purpose.** Verify content says a figure in a deck, document, workbook or
  PDF is "found" when that number appears in a saved source, a data file or a checked page
  figure, not that it is the right number for that sentence. Rendered files are checked in
  LibreOffice; nothing here can open them in PowerPoint, Word or Excel.
- **Spending.** A paid model account has no limit inside the studio for the agent's own
  requests; set one at the provider. What a project's app spends on models and what generated
  media costs are capped per day.

## What it cannot do yet

- **Linux:** the installer and the keychain fallback are written, not run.
- **Phones and tablets:** the layouts exist and were checked in a desktop browser at phone and
  tablet sizes. They have not been tried on a real device.
- **Accounts, isolation and hosting:** none. It is for one person on their own machine.
- **Other agent programs** (Codex, Gemini and others): not built. Other models run only
  through translation, as described in [models.md](models.md).
- **Real provider keys:** no request has been made with an Anthropic, OpenAI, OpenRouter,
  Azure OpenAI or Gemini key against the provider itself, or on Bedrock, Vertex or Foundry.
  `studio login` has not been run as part of a check either.
- **The newest features** (figures checked inside Office files and PDFs, the app seen signed
  in, diagrams, conversions, very large documents) were each tried with real requests on one
  Mac. Not tried: the signed-in look over a private network address, in Safari or on an app you
  brought; a deck of more than 40 slides, a large workbook or a long Word document made by a
  real request; section writers working in parallel.
- **Bringing an app:** only Next.js and Vite apps have been attached for real; other
  frameworks are recognised but untried. An agent request has been run on an app attached by
  hand, not yet on one attached from the dialog. An app whose second process needs its own
  packages cannot be set up from the dialog. In an app with a dev server, text cannot be
  edited in place: select it and ask.
- **Making it live:** the studio never pushes to a remote and only runs the recipe's commands
  on this machine. Going back does not undo data or database changes. No real app has been
  shipped with it, and a real agent has not yet drafted a recipe (the run used the scripted
  agent).
- **A restart ends running requests.** The studio is a background process, not a managed
  service.
- **Cost:** a request on a gateway you run yourself shows its cost only when the gateway is LiteLLM
  with a database (the studio reads its spend log); on any other gateway no cost is shown. Only
  OpenRouter's figures are the provider's own; the rest are calculated from a price list and say so.
  Nothing caps what the agent's requests spend.
- **Databases:** PostgreSQL has been used for real; Redshift and MySQL are untested; MongoDB
  is not supported.
- **Office files** have not been opened in real PowerPoint, Word or Excel.
- **Voice input** needs a transcription command on the machine and has not been tried with a
  real microphone.
- **Checked numbers:** a match means the words are in the saved source, not that the source
  is right.
- **Picking skills** takes time before a request starts: 1.6 to 3.9 seconds when it was
  measured, on a library of 510 skills.
