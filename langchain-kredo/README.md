# langchain-kredo

LangChain integration for the [Kredo](https://aikredo.com) agent attestation protocol.

One line of code. Signed attestation. Done.

## Install

```bash
pip install langchain-kredo
```

## One-Liner

```python
from langchain_kredo import attest

# That's it. Resolves name, looks up skill, signs, submits.
attest("jim", "incident-triage", "Triaged 3 incidents correctly in SOC exercise")

# With a URL — auto-detected as evidence artifact
attest("jim", "code-review", "https://github.com/org/repo/pull/47")

# With explicit proficiency (1-5, default 3)
attest("jim", "threat-hunting", "Found lateral movement in 4 minutes", proficiency=5)
```

Set `KREDO_PRIVATE_KEY` env var (hex seed) and go. Subject resolved by name or pubkey. Skill resolved by reverse taxonomy lookup — just say `"incident-triage"`, it finds the domain.

**Key handling:** Your signing key is a 32-byte Ed25519 seed. Store it as an environment variable, never hardcode it. Generate one with `kredo identity create` or any Ed25519 library.

## Trust Gate

Policy enforcement for agent pipelines:

```python
from langchain_kredo import KredoSigningClient, KredoTrustGate

client = KredoSigningClient(signing_key="your-hex-seed")
gate = KredoTrustGate(client, min_score=0.3, block_warned=True)

# Check trust
result = gate.check("ed25519:agent-pubkey")
# result.passed, result.score, result.skills, result.attestor_count

# Select best agent for a task (ranks by reputation + diversity + domain proficiency)
best = gate.select_best(candidates, domain="security-operations", skill="incident-triage")

# Build-vs-buy: delegate or self-compute?
delegate = gate.should_delegate(candidates, domain="code-generation", self_proficiency=2)

# Decorator
@gate.require(min_score=0.7)
def sensitive_operation(pubkey: str):
    ...
```

## LangChain Tools

Four tools for agent toolboxes. Read-only tools are safe for autonomous LLM use. The submit tool requires human approval by default.

```python
from langchain_kredo import KredoCheckTrustTool, KredoSearchAttestationsTool

# Safe for LLM agents — read-only
tools = [
    KredoCheckTrustTool(client=client),
    KredoSearchAttestationsTool(client=client),
]
```

| Tool | Name | LLM-Safe | Purpose |
|------|------|----------|---------|
| `KredoCheckTrustTool` | `kredo_check_trust` | Yes | Check agent reputation + skills + warnings |
| `KredoSearchAttestationsTool` | `kredo_search_attestations` | Yes | Find agents by skill/domain/proficiency |
| `KredoSubmitAttestationTool` | `kredo_submit_attestation` | **No** | Sign and submit skill attestation |
| `KredoGetTaxonomyTool` | `kredo_get_taxonomy` | Yes | Browse valid domains/skills |

**Warning:** `KredoSubmitAttestationTool` signs and submits irreversible cryptographic claims. By default it returns a preview for human approval. Only set `require_human_approval=False` if your pipeline has an explicit confirmation mechanism.

## Callback Handler

Tracks chain execution, builds attestation evidence automatically:

```python
from langchain_kredo import KredoCallbackHandler

handler = KredoCallbackHandler()
chain.invoke(input, config={"callbacks": [handler]})

for record in handler.get_records():
    if record.success_rate >= 0.9:
        client.attest_skill(
            subject_pubkey="ed25519:...",
            domain="security-operations",
            skill="incident-triage",
            proficiency=3,
            context=record.build_evidence_context(),
            artifacts=record.build_artifacts(),
        )
```

Collects evidence but never auto-submits. You decide when and what to attest.

## Client

Full signing-aware client for when you need more control:

```python
client = KredoSigningClient(
    signing_key=sk,           # SigningKey, bytes, hex string, or env var
    name="my-agent",
    agent_type="agent",
)

# Read
profile = client.get_profile("ed25519:...")
my_profile = client.my_profile()  # your own profile
results = client.search(domain="security-operations")

# Write
client.register()
client.attest_skill(
    subject_pubkey="ed25519:...",
    domain="security-operations",
    skill="incident-triage",
    proficiency=4,
    context="Demonstrated expert-level triage in SOC exercise",
)
```

## Development

```bash
cd langchain-kredo
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
