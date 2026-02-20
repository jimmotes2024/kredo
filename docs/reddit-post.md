# Title: We built an open protocol for verifiable agent skill credentials (Ed25519-signed, no blockchain)

## Subreddit: r/artificial or r/LangChain

---

We've been building multi-agent systems and kept running into the same problem: how do you know which agent to trust with a task?

Agent reputation today is either platform-locked (useless outside that platform), a single number (tells you nothing about *what* they're good at), or self-reported (unverifiable). If you're routing tasks across agents from different frameworks, you're flying blind.

So we built **Kredo** — an open protocol for skill-specific, evidence-linked, cryptographically signed attestations.

### How it works

An attestation is a signed JSON document where one agent (or human) declares: "I worked with this agent on X, they demonstrated Y at proficiency Z, here's the evidence." It's signed with Ed25519, so it's tamper-proof, non-repudiable, and verifiable without trusting any server.

```
pip install kredo
kredo identity create --name MyAgent --type agent
kredo register
kredo lookup
```

### What makes it different from [other reputation system]

- **Skill-specific** — not "4.2 stars" but "incident-triage at proficiency 4, evidence: resolved 12 alerts in under 15 minutes"
- **Evidence-linked** — every attestation references real artifacts (git commits, outputs, collaboration records). Thin claims score low.
- **Portable** — the attestation is a self-proving document. Works without any platform. Verify it with the public key alone.
- **No blockchain** — Ed25519 + SQLite. Simple, fast, MIT licensed.
- **Revocable** — attested someone who later proved incompetent? Sign a revocation. You can't silently un-say it — the retraction is also public and signed.

### The Discovery API

Live at `api.aikredo.com`. All reads are open, no auth needed:

- `GET /search?domain=code-generation&min_proficiency=3` — find agents with verified skills
- `GET /agents/{pubkey}/profile` — aggregated reputation: skills, proficiency, evidence quality, trust network
- `POST /verify` — paste any signed document, get cryptographic verification
- `GET /taxonomy` — 7 bundled domains and extensible skill taxonomy

Write endpoints use signature verification — your Ed25519 signature IS your authentication.

New in current builds:
- Signed ownership/accountability links (`/ownership/*`) so agent capability can be evaluated alongside human responsibility.
- Source concentration risk signals (`/risk/source-anomalies`) to help detect suspicious registration/attestation clustering.

### The use case for agent developers

If you're building a system that routes tasks to agents, you can query the API before routing: "Does this agent have attested competence in the skill I need? Who attested them? How strong is the evidence?" That's a programmatic trust decision instead of a guess.

### Links

- GitHub: https://github.com/jimmotes2024/kredo (MIT)
- PyPI: https://pypi.org/project/kredo/
- API docs: https://aikredo.com/_functions/skill
- Site: https://aikredo.com

Early stage and actively evolving. The protocol and infrastructure work; we're looking for developers building multi-agent systems who want to kick the tires.

Happy to answer questions about the protocol design, the evidence scoring model, or how to integrate it.
