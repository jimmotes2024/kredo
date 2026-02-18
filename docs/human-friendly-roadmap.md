# Kredo Human-Friendly Roadmap

## For Vanguard: Instructions to Make Kredo More Accessible to Non-Technical Users

**Package:** `kredo` v0.4.0 on PyPI
**Current State:** Fully functional CLI + API client + SQLite store, but interaction model is entirely flag-based CLI commands requiring exact taxonomy strings, raw Ed25519 pubkeys, and manual JSON handling.
**Goal:** Make Kredo usable by humans (security analysts, team leads, CISOs) who want to attest agent work without memorizing CLI flags or pubkey strings.

---

## Priority 1: `kredo init` — First-Run Onboarding

**Problem:** New users must know to run `kredo identity create --name "Jim" --type human` before anything works. No guidance, no discoverability.

**What to build:**

Add a `kredo init` command that walks the user through first-time setup in one guided flow:

1. Ask for their name (Rich prompt)
2. Ask if they're a human or agent (Rich selection)
3. Ask if they want to set a passphrase (explain why, default yes for humans)
4. Generate the Ed25519 keypair
5. Show the pubkey in a Rich Panel with a clear message: "This is your identity. Share this with agents and collaborators."
6. Ask if they want to register with the Discovery API (default yes, explain what it does in plain language)
7. If yes, call `client.register()` and confirm
8. Ask if they want to set up IPFS (default no, explain it's optional for permanence)
9. Print a "You're ready!" summary panel with next-step suggestions

**File to modify:** `kredo/cli.py`

**Add this command to the main `app`:**

```python
@app.command("init")
def init_cmd(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """First-time setup — create your identity and get started."""
    from rich.prompt import Prompt, Confirm

    console.print(Panel(
        "[bold]Welcome to Kredo[/bold]\n\n"
        "Kredo lets you and your AI agents certify each other's skills\n"
        "with cryptographically signed, evidence-backed attestations.\n\n"
        "Let's set up your identity.",
        title="🔐 Kredo Setup",
        border_style="blue",
    ))

    store = _get_store(db)

    # Check if identity already exists
    existing = store.list_identities()
    if existing:
        console.print(f"[yellow]You already have {len(existing)} identity(ies).[/yellow]")
        if not Confirm.ask("Create another identity?", default=False):
            store.close()
            return

    # Step 1: Name
    name = Prompt.ask("[bold]Your name[/bold] (or agent name)")

    # Step 2: Type
    console.print("\nAre you a human or an AI agent?")
    console.print("  [bold]1.[/bold] Human")
    console.print("  [bold]2.[/bold] Agent")
    type_choice = Prompt.ask("Choose", choices=["1", "2"], default="1")
    attestor_type = AttestorType.HUMAN if type_choice == "1" else AttestorType.AGENT

    # Step 3: Passphrase
    passphrase = None
    if attestor_type == AttestorType.HUMAN:
        console.print("\n[dim]A passphrase encrypts your private key on disk.[/dim]")
        if Confirm.ask("Set a passphrase?", default=True):
            passphrase = Prompt.ask("Passphrase", password=True)
            passphrase_confirm = Prompt.ask("Confirm passphrase", password=True)
            if passphrase != passphrase_confirm:
                console.print("[red]Passphrases don't match. Skipping encryption.[/red]")
                passphrase = None

    # Step 4: Generate
    identity = generate_keypair(name, attestor_type, store, passphrase)
    console.print()
    console.print(Panel(
        f"[bold green]Identity created![/bold green]\n\n"
        f"  Name:   {identity.name}\n"
        f"  Type:   {identity.type.value}\n"
        f"  Pubkey: {identity.pubkey}\n\n"
        f"[dim]Share your pubkey with collaborators so they can attest your work.[/dim]",
        title="Your Kredo Identity",
        border_style="green",
    ))

    # Step 5: Discovery API registration
    console.print()
    console.print("[dim]The Discovery API lets others find you and see your reputation.[/dim]")
    if Confirm.ask("Register with the Discovery API?", default=True):
        client = _get_client()
        try:
            client.register(
                pubkey=identity.pubkey,
                name=identity.name,
                agent_type=identity.type.value,
            )
            console.print("[green]✓ Registered with Discovery API[/green]")
        except KredoAPIError as e:
            console.print(f"[yellow]Registration skipped: {e.message}[/yellow]")

    # Step 6: Done
    console.print()
    console.print(Panel(
        "[bold]You're ready![/bold]\n\n"
        "  [bold]kredo attest --interactive[/bold]  →  Attest someone's work\n"
        "  [bold]kredo lookup[/bold]                →  View your reputation\n"
        "  [bold]kredo contacts list[/bold]         →  See known collaborators\n"
        "  [bold]kredo taxonomy domains[/bold]      →  Browse skill categories\n",
        title="Next Steps",
        border_style="blue",
    ))
    store.close()
```

---

## Priority 2: Interactive Attestation Mode

**Problem:** Creating an attestation currently requires a single command with 8+ flags, exact taxonomy strings, and a raw pubkey. Example:

```
kredo attest skill --subject ed25519:a3f8... --domain security-operations \
  --skill incident-triage --proficiency 4 --context "Collaborated on..."
```

**What to build:**

Add an `--interactive` / `-i` flag to the `attest` command. When used, it replaces the flags with a step-by-step Rich interactive flow.

**Implementation approach:**

Modify the `attest` command in `cli.py`. When `--interactive` is passed, bypass the flag requirements and instead:

```python
# Add to the attest command signature:
#   interactive: bool = typer.Option(False, "--interactive", "-i", help="Guided attestation flow")

# Interactive flow pseudocode:

# Step 1: Pick attestation type
# Show a numbered list: 1. Skill  2. Intellectual Contribution  3. Community Contribution
# Use Rich Prompt with choices

# Step 2: Pick the subject
# Query the store for known_keys and local identities
# Show a numbered table: Name | Pubkey (truncated) | Last Seen
# Let user pick by number OR paste a raw pubkey
# If they type a name not in the list, ask for the pubkey and register it

# Step 3: Pick domain
# Load taxonomy with get_domains()
# Show numbered list with human-readable labels from get_domain_label()
# e.g. "1. Security Operations  2. Code Generation  3. Data Analysis..."

# Step 4: Pick specific skill (filtered by domain)
# Load skills with get_skills(domain)
# Show numbered list

# Step 5: Rate proficiency
# Show visual scale:
#   1 ░░░░░ Novice     — Aware of the skill, attempted with guidance
#   2 █░░░░ Competent  — Completed the task independently
#   3 ██░░░ Proficient — Completed efficiently, handled edge cases
#   4 ███░░ Expert     — Deep knowledge, improved the process
#   5 ████░ Authority  — Others should learn from this agent

# Step 6: Describe the evidence
# Prompt: "What did you work on together? Describe the task and outcome."
# (free text, Rich Prompt)

# Step 7: Artifacts (optional)
# Prompt: "Link any evidence (URLs, chain IDs, commit hashes). Comma-separated, or press Enter to skip."

# Step 8: Outcome (optional)
# Prompt: "What was the outcome? (e.g., successful_resolution, shipped, resolved)"

# Step 9: Confirmation panel
# Show a Rich Panel summarizing everything before signing
# Ask: "Sign and save this attestation? [Y/n]"

# Step 10: Sign, save, show result with evidence score
```

**Key design rules for the interactive flow:**
- Every step should have a default or be skippable where possible
- Show the attestation as a formatted Rich Panel before signing so the user can review
- After signing, offer to submit to the Discovery API and optionally pin to IPFS
- Use color and visual structure — this is the user's first impression of Kredo

---

## Priority 3: Contacts System

**Problem:** Users must copy/paste 64-character hex pubkeys. The `known_keys` table exists but has no CLI surface.

**What to build:**

Add a `contacts` sub-app to the CLI:

```python
contacts_app = typer.Typer(help="Manage known agents and collaborators")
app.add_typer(contacts_app, name="contacts")
```

**Commands:**

### `kredo contacts add`
```
kredo contacts add --name "Vanguard" --pubkey ed25519:a3f8... --type agent
```
Interactive version: `kredo contacts add -i` → prompts for name, pubkey, type

### `kredo contacts list`
Show a Rich table of all known keys:
```
┌─────────────┬──────────────────────────┬───────┬────────────┐
│ Name        │ Pubkey                   │ Type  │ Last Seen  │
├─────────────┼──────────────────────────┼───────┼────────────┤
│ Vanguard    │ ed25519:a3f8...b2c1      │ agent │ 2026-02-14 │
│ SOC-Agent-1 │ ed25519:7d2e...f9a0      │ agent │ 2026-02-10 │
│ Jim         │ ed25519:1b4c...e8d3 (me) │ human │ —          │
└─────────────┴──────────────────────────┴───────┴────────────┘
```

### `kredo contacts remove`
```
kredo contacts remove "Vanguard"
```

### Integration with other commands
Everywhere the CLI currently accepts `--subject <pubkey>`, also accept `--subject "Vanguard"` (name lookup). Modify `_get_signing_identity` and add a `_resolve_subject` helper:

```python
def _resolve_subject(identifier: str, store: KredoStore) -> str:
    """Resolve a name or pubkey to a pubkey string."""
    if identifier.startswith("ed25519:"):
        return identifier
    # Search known_keys and identities by name
    row = store.find_key_by_name(identifier)
    if row:
        return row["pubkey"]
    console.print(f"[red]Unknown contact: {identifier}[/red]")
    console.print("Add them with: kredo contacts add --name '...' --pubkey '...'")
    raise typer.Exit(1)
```

**Store addition needed** — add to `KredoStore`:

```python
def find_key_by_name(self, name: str) -> Optional[dict]:
    """Find a known key or identity by name (case-insensitive)."""
    # Check identities first
    row = self._conn.execute(
        "SELECT pubkey, name, type FROM identities WHERE LOWER(name) = LOWER(?)",
        (name,)
    ).fetchone()
    if row:
        return dict(row)
    # Then known_keys
    row = self._conn.execute(
        "SELECT pubkey, name, type FROM known_keys WHERE LOWER(name) = LOWER(?)",
        (name,)
    ).fetchone()
    return dict(row) if row else None

def list_contacts(self) -> list[dict]:
    """List all known keys with last seen dates."""
    rows = self._conn.execute(
        "SELECT pubkey, name, type, first_seen, last_seen FROM known_keys ORDER BY last_seen DESC"
    ).fetchall()
    return [dict(r) for r in rows]

def remove_contact(self, name_or_pubkey: str) -> bool:
    """Remove a known key by name or pubkey."""
    if name_or_pubkey.startswith("ed25519:"):
        self._conn.execute("DELETE FROM known_keys WHERE pubkey = ?", (name_or_pubkey,))
    else:
        self._conn.execute("DELETE FROM known_keys WHERE LOWER(name) = LOWER(?)", (name_or_pubkey,))
    self._conn.commit()
    return self._conn.total_changes > 0
```

---

## Priority 4: `kredo me` — Quick Self-Status

**Problem:** Checking your own status requires knowing the right commands and your own pubkey.

**What to build:**

A `kredo me` command that shows your identity, local stats, and network reputation in one view:

```python
@app.command("me")
def me_cmd(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Show your identity, local stats, and network reputation."""
    store = _get_store(db)
    default = store.get_default_identity()
    if not default:
        console.print("[yellow]No identity found. Run: kredo init[/yellow]")
        store.close()
        return

    # Local identity panel
    console.print(Panel(
        f"  Name:   [bold]{default['name']}[/bold]\n"
        f"  Type:   {default['type']}\n"
        f"  Pubkey: [dim]{default['pubkey']}[/dim]",
        title="🔐 Your Identity",
        border_style="blue",
    ))

    # Local stats
    attestations_given = store.search_attestations(attestor_pubkey=default["pubkey"])
    attestations_received = store.search_attestations(subject_pubkey=default["pubkey"])
    console.print(f"\n  Attestations given:    {len(attestations_given)}")
    console.print(f"  Attestations received: {len(attestations_received)}")

    # Try network lookup (best-effort, don't fail)
    try:
        client = _get_client()
        profile = client.get_profile(default["pubkey"])
        console.print()
        _render_profile(profile)
    except Exception:
        console.print("\n  [dim]Network profile: not available (offline or not registered)[/dim]")

    store.close()
```

---

## Priority 5: Better Output Formatting Throughout

**Problem:** Attestation output is terse programmer text. Evidence scores are bare floats.

**Changes to make across the CLI:**

### Evidence score display
Replace `Evidence score: 0.73` with:

```
Evidence Quality: ███████░░░ 73%
  Specificity:    ████████░░ 80%
  Verifiability:  ██████░░░░ 60%
  Relevance:      ██████████ 100%
  Recency:        █████░░░░░ 52%
```

Add this helper:

```python
def _render_evidence_score(score) -> None:
    """Render an EvidenceScore as visual bars."""
    def bar(value: float, width: int = 10) -> str:
        filled = int(value * width)
        return "█" * filled + "░" * (width - filled)

    console.print(f"\n  Evidence Quality: {bar(score.composite)} {score.composite:.0%}")
    console.print(f"    Specificity:    {bar(score.specificity)} {score.specificity:.0%}")
    console.print(f"    Verifiability:  {bar(score.verifiability)} {score.verifiability:.0%}")
    console.print(f"    Relevance:      {bar(score.relevance)} {score.relevance:.0%}")
    console.print(f"    Recency:        {bar(score.recency)} {score.recency:.0%}")
```

### Attestation confirmation display
After creating an attestation, show a Rich Panel:

```
╭─ Attestation Created ──────────────────────────────────────────╮
│                                                                 │
│  ID:          a1b2c3d4-...                                      │
│  Type:        Skill Attestation                                 │
│  Subject:     Vanguard (ed25519:a3f8...)                        │
│  Skill:       Security Operations / Incident Triage             │
│  Proficiency: ████░ Expert (4/5)                                │
│  Evidence:    ███████░░░ 73%                                    │
│  Signed by:   Jim (ed25519:1b4c...)                             │
│  Expires:     2027-02-17                                        │
│                                                                 │
╰─────────────────────────────────────────────────────────────────╯
```

### Truncate pubkeys everywhere
Add a helper and use it in all table and text output:

```python
def _short_key(pubkey: str, length: int = 12) -> str:
    """Truncate a pubkey for display, keeping prefix."""
    if len(pubkey) <= length + 10:
        return pubkey
    return pubkey[:length] + "..." + pubkey[-4:]
```

---

## Priority 6: Human-Readable Attestation Export

**Problem:** `kredo export <id>` outputs raw JSON. Fine for machines, meaningless to a person.

**What to build:**

Add a `--human` flag to the export command that renders a formatted, readable version:

```
kredo export a1b2c3d4 --human
```

Output:

```
═══════════════════════════════════════════════════
 KREDO SKILL ATTESTATION
═══════════════════════════════════════════════════

 I, Jim (human), attest that:

   Vanguard (agent)

 demonstrated EXPERT-level proficiency in:

   Security Operations → Incident Triage

 Evidence:
   "Collaborated on the February 14 phishing incident
    chain analysis. Vanguard correctly identified the
    initial vector, extracted 12 IOCs, and produced
    a triage report within 4 minutes."

   Artifacts:
   • chain:abc123
   • output:ioc-report-def456

   Outcome: successful_resolution

 Issued:  2026-02-14
 Expires: 2027-02-14
 Signature: ✓ Valid (ed25519)

═══════════════════════════════════════════════════
```

Also add a `--format` option: `json` (default), `human`, `markdown`. The markdown version would be useful for sharing in Slack, Teams, or documentation.

---

## Priority 7: Error Messages That Help

**Problem:** Current errors like `"pubkey must start with 'ed25519:'"` are technically correct but don't tell the user what to do.

**Improve error messages throughout:**

| Current Error | Better Error |
|---|---|
| `pubkey must start with 'ed25519:'` | `Invalid public key format. Keys look like: ed25519:a3f8b2... — get one from your collaborator or run 'kredo contacts list'` |
| `Unknown domain: 'security'` | `Unknown domain: 'security'. Did you mean 'security-operations'? Run 'kredo taxonomy domains' to see all options.` |
| `No default identity` | `No identity found. Run 'kredo init' to create one (takes 30 seconds).` |
| `behavioral_warning requires evidence context >= 100 characters` | `Warnings need strong evidence. Your context is only X characters — please write at least 100 characters describing what happened and why it's harmful.` |

**Implementation:** Add a `_friendly_error` wrapper or modify the Pydantic validators in `models.py` to include actionable guidance.

---

## Priority 8: `kredo quickstart` Tutorial

**What to build:**

A built-in interactive tutorial that creates a demo attestation end-to-end:

```
kredo quickstart
```

Flow:
1. "Let's create a practice attestation to see how Kredo works."
2. Creates a temporary demo identity if none exists
3. Creates a second demo identity (the "subject")
4. Walks through the interactive attestation flow with pre-filled suggestions
5. Shows the signed attestation
6. Verifies the signature
7. Shows the evidence score with explanations
8. "That's it! In real use, your subject would be a real agent or colleague."
9. Offers to delete the demo data or keep it

---

## Summary: Implementation Order

| Priority | Feature | Effort | Impact |
|----------|---------|--------|--------|
| 1 | `kredo init` onboarding | Small | High — removes the blank-screen problem |
| 2 | Interactive attestation (`--interactive`) | Medium | High — makes the core action accessible |
| 3 | Contacts system | Medium | High — eliminates pubkey copy-paste |
| 4 | `kredo me` self-status | Small | Medium — instant gratification |
| 5 | Better output formatting | Small | Medium — makes output meaningful |
| 6 | Human-readable export | Small | Medium — shareability |
| 7 | Friendly error messages | Small | Medium — reduces frustration |
| 8 | `kredo quickstart` tutorial | Medium | Medium — lowers learning curve |

**Note to Vanguard:** All of these changes are additive — they don't break the existing CLI interface. Every flag-based command continues to work exactly as it does today. The interactive modes are opt-in. This means agents can still use the programmatic CLI while humans get the guided experience.

**Dependencies:** No new packages needed. Everything uses Rich (already installed) and Typer (already installed). The `rich.prompt` module handles interactive input. The `rich.panel`, `rich.table`, and `rich.text` modules handle formatting.
