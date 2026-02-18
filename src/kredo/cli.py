"""Kredo CLI — Typer app for identity, attestation, and trust management."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kredo import __version__
from kredo.evidence import score_evidence
from kredo.identity import (
    export_public_key,
    generate_keypair,
    get_default_identity,
    list_identities,
    load_signing_key,
    set_default_identity,
)
from kredo.models import (
    AttestationType,
    AttestorType,
    Attestation,
    Attestor,
    Dispute,
    Evidence,
    Proficiency,
    Revocation,
    Skill,
    Subject,
    WarningCategory,
)
from kredo.signing import (
    sign_attestation,
    sign_dispute,
    sign_revocation,
    verify_attestation,
    verify_dispute,
    verify_revocation,
)
from kredo.client import KredoAPIError, KredoClient
from kredo.ipfs import (
    canonical_json_full,
    fetch_document,
    get_provider,
    ipfs_enabled,
    pin_document,
)
from kredo.exceptions import IPFSError
from kredo.store import KredoStore
from kredo.taxonomy import get_domain_label, get_domains, get_skills, set_store as _set_taxonomy_store, invalidate_cache as _invalidate_taxonomy_cache

console = Console()
app = typer.Typer(
    name="kredo",
    help="Kredo — Portable agent attestation protocol",
    no_args_is_help=True,
)

# --- Sub-apps ---
identity_app = typer.Typer(help="Manage identities (Ed25519 keypairs)")
trust_app = typer.Typer(help="Query the trust graph")
taxonomy_app = typer.Typer(help="Browse the skill taxonomy")
contacts_app = typer.Typer(help="Manage known agents and collaborators")
ipfs_app = typer.Typer(help="IPFS pinning for content-addressed attestations")
app.add_typer(identity_app, name="identity")
app.add_typer(contacts_app, name="contacts")
app.add_typer(trust_app, name="trust")
app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(ipfs_app, name="ipfs")


def _get_store(db: Optional[Path] = None) -> KredoStore:
    return KredoStore(db_path=db)


def _get_signing_identity(store: KredoStore, identity_key: Optional[str] = None):
    """Resolve the signing identity — explicit key or default."""
    if identity_key:
        row = store.get_identity(identity_key)
        return row
    default = store.get_default_identity()
    if default is None:
        console.print("[red]No identity found.[/red] Run [bold]kredo init[/bold] to create one (takes 30 seconds).")
        raise typer.Exit(1)
    return default


# --- Version ---

def _version_callback(value: bool):
    if value:
        console.print(f"kredo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", callback=_version_callback, is_eager=True),
):
    pass


# --- Onboarding ---

@app.command("init")
def init_cmd(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """First-time setup — create your identity and get started."""
    from rich.prompt import Confirm, Prompt

    console.print(Panel(
        "[bold]Welcome to Kredo[/bold]\n\n"
        "Kredo lets you and your AI agents certify each other's skills\n"
        "with cryptographically signed, evidence-backed attestations.\n\n"
        "Let's set up your identity.",
        title="Kredo Setup",
        border_style="blue",
    ))

    store = _get_store(db)

    # Check if identity already exists
    existing = store.list_identities()
    if existing:
        console.print(f"\n[yellow]You already have {len(existing)} identity(ies).[/yellow]")
        if not Confirm.ask("Create another identity?", default=False):
            store.close()
            return

    # Step 1: Name
    name = Prompt.ask("\n[bold]Your name[/bold] (or agent name)")

    # Step 2: Type
    console.print("\nAre you a human or an AI agent?")
    console.print("  [bold]1.[/bold] Human")
    console.print("  [bold]2.[/bold] Agent")
    type_choice = Prompt.ask("Choose", choices=["1", "2"], default="1")
    attestor_type = AttestorType.HUMAN if type_choice == "1" else AttestorType.AGENT

    # Step 3: Passphrase (recommend for humans)
    passphrase = None
    if attestor_type == AttestorType.HUMAN:
        console.print("\n[dim]A passphrase encrypts your private key on disk.[/dim]")
        if Confirm.ask("Set a passphrase?", default=True):
            passphrase = Prompt.ask("Passphrase", password=True)
            passphrase_confirm = Prompt.ask("Confirm passphrase", password=True)
            if passphrase != passphrase_confirm:
                console.print("[red]Passphrases don't match. Skipping encryption.[/red]")
                passphrase = None

    # Step 4: Generate keypair
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
        try:
            client = _get_client()
            client.register(
                pubkey=identity.pubkey,
                name=identity.name,
                agent_type=identity.type.value,
            )
            console.print("[green]Registered with Discovery API[/green]")
        except Exception as e:
            console.print(f"[yellow]Registration skipped: {e}[/yellow]")

    # Step 6: Next steps
    console.print()
    console.print(Panel(
        "[bold]You're ready![/bold]\n\n"
        "  [bold]kredo me[/bold]                 View your identity and reputation\n"
        "  [bold]kredo attest skill[/bold]       Attest someone's work\n"
        "  [bold]kredo lookup[/bold]             Look up any agent's profile\n"
        "  [bold]kredo taxonomy domains[/bold]   Browse skill categories\n",
        title="Next Steps",
        border_style="blue",
    ))
    store.close()


# --- Quickstart Tutorial ---

@app.command("quickstart")
def quickstart_cmd(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Interactive tutorial — create a demo attestation in 2 minutes."""
    from rich.prompt import Confirm, Prompt

    console.print(Panel(
        "[bold]Kredo Quickstart[/bold]\n\n"
        "This tutorial walks you through a complete attestation\n"
        "from start to finish using demo identities.\n\n"
        "[dim]Everything happens locally. You can delete the demo data afterward.[/dim]",
        title="Tutorial",
        border_style="blue",
    ))

    store = _get_store(db)

    # Step 1: Create or reuse the user's identity
    console.print()
    console.print("[bold]Step 1: Your Identity[/bold]")
    console.print("─" * 50)

    existing = store.list_identities()
    if existing:
        default = store.get_default_identity()
        if default:
            your_name = default["name"]
            your_pubkey = default["pubkey"]
            console.print(f"  Using your existing identity: [bold]{your_name}[/bold]")
            console.print(f"  Pubkey: [dim]{_short_key(your_pubkey)}[/dim]")
        else:
            your_name = existing[0]["name"]
            your_pubkey = existing[0]["pubkey"]
            console.print(f"  Using identity: [bold]{your_name}[/bold]")
    else:
        console.print("  No identity found — let's create one for the demo.")
        your_name = Prompt.ask("  Your name", default="Demo User")
        identity = generate_keypair(your_name, AttestorType.HUMAN, store)
        your_pubkey = identity.pubkey
        console.print(f"  [green]Identity created:[/green] {your_name}")
        console.print(f"  Pubkey: [dim]{_short_key(your_pubkey)}[/dim]")

    # Step 2: Create a demo subject
    console.print()
    console.print("[bold]Step 2: The Agent You're Attesting[/bold]")
    console.print("─" * 50)
    console.print("  In real use, this would be an AI agent or colleague you've worked with.")
    console.print("  For this demo, we'll create a practice agent.")
    console.print()

    demo_name = "Demo-Agent"
    demo_identity = generate_keypair(demo_name, AttestorType.AGENT, store)
    demo_pubkey = demo_identity.pubkey
    store.register_known_key(demo_pubkey, name=demo_name, attestor_type="agent")
    console.print(f"  [green]Demo agent created:[/green] {demo_name}")
    console.print(f"  Pubkey: [dim]{_short_key(demo_pubkey)}[/dim]")

    # Step 3: Pick a skill
    console.print()
    console.print("[bold]Step 3: What Skill Are You Attesting?[/bold]")
    console.print("─" * 50)
    console.print("  Kredo organizes skills into domains. Let's pick one.")
    console.print()

    domains = get_domains()
    for i, d in enumerate(domains, 1):
        label = get_domain_label(d)
        console.print(f"  [bold]{i}.[/bold] {label}  [dim]({d})[/dim]")

    domain_choice = Prompt.ask("  Choose a domain", choices=[str(i) for i in range(1, len(domains) + 1)], default="1")
    domain = domains[int(domain_choice) - 1]
    console.print(f"  Selected: [bold]{get_domain_label(domain)}[/bold]")

    console.print()
    skills = get_skills(domain)
    for i, s in enumerate(skills, 1):
        console.print(f"  [bold]{i}.[/bold] {s}")

    skill_choice = Prompt.ask("  Choose a skill", choices=[str(i) for i in range(1, len(skills) + 1)], default="1")
    skill = skills[int(skill_choice) - 1]
    console.print(f"  Selected: [bold]{skill}[/bold]")

    # Step 4: Rate proficiency
    console.print()
    console.print("[bold]Step 4: How Good Were They?[/bold]")
    console.print("─" * 50)
    console.print("  Rate the agent's proficiency on a 1-5 scale:")
    console.print()
    console.print("  [bold]1[/bold]  ░░░░░  Novice     — Aware of the skill, attempted with guidance")
    console.print("  [bold]2[/bold]  █░░░░  Competent  — Completed the task independently")
    console.print("  [bold]3[/bold]  ██░░░  Proficient — Completed efficiently, handled edge cases")
    console.print("  [bold]4[/bold]  ███░░  Expert     — Deep knowledge, improved the process")
    console.print("  [bold]5[/bold]  ████░  Authority  — Others should learn from this agent")
    proficiency = int(Prompt.ask("  Rate (1-5)", choices=["1", "2", "3", "4", "5"], default="4"))

    # Step 5: Describe the evidence
    console.print()
    console.print("[bold]Step 5: Describe What Happened[/bold]")
    console.print("─" * 50)
    console.print("  In real attestations, this is where you describe the work.")
    console.print("  Write at least a sentence — evidence quality affects the score.")
    console.print()
    default_context = f"Collaborated on a {get_domain_label(domain).lower()} task involving {skill}. The agent performed well and delivered quality results."
    context = Prompt.ask("  Evidence", default=default_context)

    # Step 6: Sign the attestation
    console.print()
    console.print("[bold]Step 6: Sign the Attestation[/bold]")
    console.print("─" * 50)
    console.print("  Kredo uses Ed25519 digital signatures — the same cryptography")
    console.print("  used by SSH keys and Signal. Your private key signs the claim,")
    console.print("  and anyone can verify it with your public key.")
    console.print()

    id_row = _get_signing_identity(store)
    attestor = Attestor(
        pubkey=id_row["pubkey"],
        name=id_row["name"],
        type=AttestorType(id_row["type"]),
    )
    subject_obj = Subject(pubkey=demo_pubkey, name=demo_name)
    evidence = Evidence(context=context)
    now = datetime.now(timezone.utc)
    skill_obj = Skill(domain=domain, specific=skill, proficiency=Proficiency(proficiency))

    attestation = Attestation(
        type=AttestationType.SKILL,
        subject=subject_obj,
        attestor=attestor,
        skill=skill_obj,
        evidence=evidence,
        issued=now,
        expires=now + timedelta(days=365),
    )

    signing_key = load_signing_key(id_row["pubkey"], store)
    signed = sign_attestation(attestation, signing_key)
    raw_json = signed.model_dump_json(indent=2)
    store.save_attestation(raw_json)

    # Step 7: Show the result
    ev_score = score_evidence(signed.evidence, signed.type)
    prof_label = _PROFICIENCY_LABELS.get(proficiency, f"Level {proficiency}")
    prof_bar = _proficiency_bar(proficiency)

    console.print()
    console.print(Panel(
        f"  ID:          {signed.id}\n"
        f"  Type:        Skill Attestation\n"
        f"  Attestor:    {your_name} ({_short_key(your_pubkey)})\n"
        f"  Subject:     {demo_name} ({_short_key(demo_pubkey)})\n"
        f"  Skill:       {get_domain_label(domain)} / {skill}\n"
        f"  Proficiency: {prof_bar} {prof_label} ({proficiency}/5)\n"
        f"  Evidence:    {_evidence_bar(ev_score.composite)} {int(ev_score.composite * 100)}%\n"
        f"{_evidence_detail(ev_score)}\n"
        f"  Signed:      Yes (Ed25519)",
        title="Attestation Created",
        border_style="green",
    ))

    # Step 8: Verify the signature
    console.print()
    console.print("[bold]Step 7: Verify the Signature[/bold]")
    console.print("─" * 50)
    console.print("  Anyone with your public key can verify this attestation.")
    console.print("  Let's verify it now...")
    console.print()

    try:
        verify_attestation(signed)
        console.print("  [green]Signature verified![/green] This attestation is authentic and untampered.")
    except Exception as e:
        console.print(f"  [red]Verification failed: {e}[/red]")

    # Step 9: Evidence score explanation
    console.print()
    console.print("[bold]Step 8: Understanding Evidence Quality[/bold]")
    console.print("─" * 50)
    console.print("  Kredo scores evidence across four dimensions:")
    console.print()
    console.print(f"  Specificity:    {_evidence_bar(ev_score.specificity, 8)} {int(ev_score.specificity * 100)}%")
    console.print("                  [dim]How detailed and precise is the evidence?[/dim]")
    console.print(f"  Verifiability:  {_evidence_bar(ev_score.verifiability, 8)} {int(ev_score.verifiability * 100)}%")
    console.print("                  [dim]Can someone independently check this claim?[/dim]")
    console.print(f"  Relevance:      {_evidence_bar(ev_score.relevance, 8)} {int(ev_score.relevance * 100)}%")
    console.print("                  [dim]Does the evidence match the claimed skill?[/dim]")
    console.print(f"  Recency:        {_evidence_bar(ev_score.recency, 8)} {int(ev_score.recency * 100)}%")
    console.print("                  [dim]How recent is the interaction?[/dim]")
    console.print()
    console.print(f"  [bold]Overall: {_evidence_bar(ev_score.composite)} {int(ev_score.composite * 100)}%[/bold]")
    console.print()
    console.print("  [dim]Tip: Add artifacts (chain IDs, URLs, commit hashes) and describe[/dim]")
    console.print("  [dim]specific outcomes to improve your evidence score.[/dim]")

    # Step 10: Wrap up
    console.print()
    console.print(Panel(
        "[bold]That's it![/bold]\n\n"
        "You just created a cryptographically signed skill attestation.\n"
        "In real use, your subject would be an actual AI agent or colleague.\n\n"
        "  [bold]kredo attest -i[/bold]          Create a real attestation\n"
        "  [bold]kredo me[/bold]                 View your reputation\n"
        "  [bold]kredo contacts add[/bold]       Add collaborators\n"
        "  [bold]kredo export <id>[/bold]        Share attestations\n"
        "  [bold]kredo lookup[/bold]             Look up anyone's profile\n",
        title="What's Next",
        border_style="blue",
    ))

    # Offer cleanup
    if Confirm.ask("Delete the demo agent and attestation?", default=False):
        # Remove the demo agent from known_keys and identities
        store.remove_contact(demo_pubkey)
        # Remove the demo identity (it's in identities table)
        store._conn.execute("DELETE FROM identities WHERE pubkey = ?", (demo_pubkey,))
        store._conn.commit()
        console.print("  [dim]Demo data cleaned up. Your identity was kept.[/dim]")
    else:
        console.print("  [dim]Demo data kept. You can review it with 'kredo me'.[/dim]")

    store.close()


# --- Self-Status ---

def _short_key(pubkey: str, length: int = 16) -> str:
    """Truncate a pubkey for display, keeping prefix."""
    if len(pubkey) <= length + 10:
        return pubkey
    return pubkey[:length] + "..." + pubkey[-4:]


def _evidence_bar(value: float, width: int = 10) -> str:
    """Render a visual bar for a 0.0-1.0 value."""
    filled = int(value * width)
    return "█" * filled + "░" * (width - filled)


def _evidence_detail(ev_score) -> str:
    """Render 4-dimension evidence breakdown as a compact string."""
    lines = []
    for label, val in [
        ("Specificity", ev_score.specificity),
        ("Verifiability", ev_score.verifiability),
        ("Relevance", ev_score.relevance),
        ("Recency", ev_score.recency),
    ]:
        bar = _evidence_bar(val, 8)
        lines.append(f"    {label:<14} {bar} {int(val * 100)}%")
    return "\n".join(lines)


@app.command("me")
def me_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Show your identity, local stats, and network reputation."""
    store = _get_store(db)
    default = store.get_default_identity()
    if not default:
        console.print("[yellow]No identity found. Run: kredo init[/yellow]")
        store.close()
        return

    pubkey = default["pubkey"]
    name = default["name"]
    agent_type = default["type"]

    # Local identity panel
    console.print(Panel(
        f"  Name:   [bold]{name}[/bold]\n"
        f"  Type:   {agent_type}\n"
        f"  Pubkey: [dim]{pubkey}[/dim]",
        title="Your Identity",
        border_style="blue",
    ))

    # Local stats
    attestations_given = store.search_attestations(attestor_pubkey=pubkey)
    attestations_received = store.search_attestations(subject_pubkey=pubkey)
    contacts = store.list_contacts()

    console.print()
    console.print("[bold]Local Stats[/bold]")
    console.print("─" * 50)
    console.print(f"  Attestations given:    {len(attestations_given)}")
    console.print(f"  Attestations received: {len(attestations_received)}")
    console.print(f"  Known contacts:        {len(contacts)}")

    # Network lookup (best-effort — don't fail if offline/unregistered)
    try:
        client = _get_client(api_url)
        profile = client.get_profile(pubkey)

        if json_output:
            sys.stdout.write(json.dumps(profile, indent=2, default=str) + "\n")
        else:
            console.print()
            console.print("[bold]Network Reputation[/bold]")
            console.print("─" * 50)

            # Reputation score
            trust_analysis = profile.get("trust_analysis", {})
            rep_score = trust_analysis.get("reputation_score")
            if rep_score is not None:
                pct = int(rep_score * 100)
                bar = _evidence_bar(rep_score)
                console.print(f"  Reputation:     {bar} {pct}%")
            else:
                console.print("  Reputation:     [dim]not yet scored[/dim]")

            # Attestation counts
            att_counts = profile.get("attestation_count", {})
            total = att_counts.get("total", 0)
            by_agents = att_counts.get("by_agents", 0)
            by_humans = att_counts.get("by_humans", 0)
            console.print(f"  Attestations:   {total} total ({by_agents} by agents, {by_humans} by humans)")

            # Evidence quality
            ev_quality = profile.get("evidence_quality_avg")
            if ev_quality is not None:
                ev_bar = _evidence_bar(ev_quality)
                console.print(f"  Evidence avg:   {ev_bar} {int(ev_quality * 100)}%")

            # Warnings
            warnings = profile.get("warnings", [])
            active_warnings = [w for w in warnings if not w.get("is_revoked")]
            if active_warnings:
                console.print(f"  [yellow]Warnings:       {len(active_warnings)} active[/yellow]")

            # Ring flags
            ring_flags = trust_analysis.get("ring_flags", [])
            if ring_flags:
                console.print(f"  [yellow]Ring flags:     {len(ring_flags)}[/yellow]")

            # Skills summary
            skills = profile.get("skills", [])
            if skills:
                console.print()
                console.print("[bold]Skills[/bold]")
                console.print("─" * 50)
                for s in skills:
                    domain = s.get("domain", "")
                    specific = s.get("specific", "")
                    max_prof = s.get("max_proficiency", 0)
                    label = _PROFICIENCY_LABELS.get(max_prof, f"Level {max_prof}")
                    bar = _proficiency_bar(max_prof)
                    att_count = s.get("attestation_count", 0)
                    console.print(
                        f"  {domain}/{specific:<30} {bar} {label}  "
                        f"[dim]({att_count} attestation{'s' if att_count != 1 else ''})[/dim]"
                    )

    except Exception:
        console.print()
        console.print("  [dim]Network profile: not available (offline or not registered)[/dim]")
        console.print("  [dim]Register with: kredo register[/dim]")

    console.print()
    store.close()


# --- Identity Commands ---

@identity_app.command("create")
def identity_create(
    name: str = typer.Option(..., "--name", help="Human-readable name"),
    attestor_type: str = typer.Option(..., "--type", help="agent or human"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Encrypt private key"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Generate a new Ed25519 identity."""
    try:
        atype = AttestorType(attestor_type)
    except ValueError:
        console.print(f"[red]Invalid type: {attestor_type}. Use 'agent' or 'human'.[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    if not passphrase and atype == AttestorType.HUMAN:
        console.print("[yellow]Warning: no passphrase for human identity. Private key will be stored unencrypted.[/yellow]")

    identity = generate_keypair(name, atype, store, passphrase)
    console.print(f"[green]Identity created:[/green] {identity.name}")
    console.print(f"  pubkey: {identity.pubkey}")
    store.close()


@identity_app.command("list")
def identity_list(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """List all local identities."""
    store = _get_store(db)
    identities = list_identities(store)
    if not identities:
        console.print("No identities found. Create one with: kredo identity create")
        store.close()
        return

    default = get_default_identity(store)
    table = Table(title="Kredo Identities")
    table.add_column("Default", width=3)
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Public Key")

    for ident in identities:
        is_def = "*" if default and ident.pubkey == default.pubkey else ""
        table.add_row(is_def, ident.name, ident.type.value, ident.pubkey)

    console.print(table)
    store.close()


@identity_app.command("export")
def identity_export(
    pubkey: str = typer.Argument(..., help="Public key to export"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Export the hex-encoded public key for sharing."""
    hex_key = export_public_key(pubkey)
    console.print(hex_key)


@identity_app.command("set-default")
def identity_set_default(
    pubkey: str = typer.Argument(..., help="Public key to set as default"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Set an identity as the default signing identity."""
    store = _get_store(db)
    set_default_identity(pubkey, store)
    console.print(f"[green]Default identity set:[/green] {pubkey}")
    store.close()


# --- Attestation Commands ---

def _build_attestation(
    att_type: AttestationType,
    subject_pubkey: str,
    store: KredoStore,
    identity_key: Optional[str],
    domain: Optional[str],
    skill: Optional[str],
    proficiency: Optional[int],
    warning_category: Optional[str],
    context: str,
    artifacts: str,
    outcome: str,
    interaction_date: str,
    expires_days: int,
) -> Attestation:
    """Build an attestation from CLI args."""
    id_row = _get_signing_identity(store, identity_key)

    attestor = Attestor(
        pubkey=id_row["pubkey"],
        name=id_row["name"],
        type=AttestorType(id_row["type"]),
    )
    subject = Subject(pubkey=subject_pubkey)
    # Register subject as known key
    store.register_known_key(subject_pubkey)

    evidence = Evidence(
        context=context,
        artifacts=[a.strip() for a in artifacts.split(",") if a.strip()] if artifacts else [],
        outcome=outcome,
        interaction_date=datetime.fromisoformat(interaction_date) if interaction_date else None,
    )

    now = datetime.now(timezone.utc)
    skill_obj = None
    warn_cat = None

    if att_type != AttestationType.WARNING:
        if not domain or not skill or proficiency is None:
            console.print("[red]--domain, --skill, and --proficiency are required.[/red]")
            console.print("[dim]Run 'kredo taxonomy domains' to see domains, or use 'kredo attest -i' for a guided flow.[/dim]")
            raise typer.Exit(1)
        skill_obj = Skill(domain=domain, specific=skill, proficiency=Proficiency(proficiency))
    else:
        if not warning_category:
            console.print("[red]--category is required for warnings.[/red]")
            console.print("[dim]Options: spam, malware, deception, data_exfiltration, impersonation[/dim]")
            raise typer.Exit(1)
        warn_cat = WarningCategory(warning_category)

    return Attestation(
        type=att_type,
        subject=subject,
        attestor=attestor,
        skill=skill_obj,
        warning_category=warn_cat,
        evidence=evidence,
        issued=now,
        expires=now + timedelta(days=expires_days),
    )


def _resolve_subject_input(identifier: str, store: KredoStore) -> str:
    """Resolve a name or pubkey to a pubkey string."""
    if identifier.startswith("ed25519:"):
        return identifier
    result = store.find_key_by_name(identifier)
    if result:
        return result["pubkey"]
    console.print(f"[red]Unknown contact: {identifier}[/red]")
    console.print("Add them with: kredo contacts add --name '...' --pubkey '...'")
    raise typer.Exit(1)


def _interactive_attest(store: KredoStore, identity_key: Optional[str], passphrase: Optional[str]):
    """Run the guided interactive attestation flow."""
    from rich.prompt import Confirm, Prompt

    # Step 1: Attestation type
    console.print()
    console.print("[bold]What kind of attestation?[/bold]")
    console.print("  [bold]1.[/bold] Skill          — Demonstrated competence in a specific area")
    console.print("  [bold]2.[/bold] Intellectual    — Ideas that led to concrete outcomes")
    console.print("  [bold]3.[/bold] Community       — Helping others learn, improving shared resources")
    type_choice = Prompt.ask("Choose", choices=["1", "2", "3"], default="1")
    type_map = {"1": AttestationType.SKILL, "2": AttestationType.INTELLECTUAL, "3": AttestationType.COMMUNITY}
    att_type = type_map[type_choice]
    type_labels = {"1": "Skill Attestation", "2": "Intellectual Contribution", "3": "Community Contribution"}
    type_label = type_labels[type_choice]

    # Step 2: Subject
    console.print()
    # Show known contacts + identities as a numbered list
    contacts = store.list_contacts()
    identities = store.list_identities()
    candidates = []
    for ident in identities:
        candidates.append({"pubkey": ident["pubkey"], "name": ident["name"], "type": ident["type"], "source": "identity"})
    for contact in contacts:
        # Skip if already in identities
        if not any(c["pubkey"] == contact["pubkey"] for c in candidates):
            candidates.append({"pubkey": contact["pubkey"], "name": contact["name"], "type": contact["type"], "source": "contact"})

    if candidates:
        console.print("[bold]Who are you attesting?[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("#", width=3)
        table.add_column("Name")
        table.add_column("Type", width=6)
        table.add_column("Pubkey")
        for i, c in enumerate(candidates, 1):
            table.add_row(str(i), c["name"] or "[dim]unnamed[/dim]", c["type"], _short_key(c["pubkey"]))
        console.print(table)
        console.print(f"  [dim]Enter a number (1-{len(candidates)}) or paste a pubkey[/dim]")
        subject_input = Prompt.ask("Subject")
    else:
        console.print("[bold]Who are you attesting?[/bold]")
        console.print("  [dim]No known contacts. Paste a pubkey (ed25519:...)[/dim]")
        subject_input = Prompt.ask("Subject pubkey")

    # Resolve subject
    if subject_input.isdigit() and 1 <= int(subject_input) <= len(candidates):
        subject_pubkey = candidates[int(subject_input) - 1]["pubkey"]
        subject_name = candidates[int(subject_input) - 1]["name"]
        console.print(f"  Selected: [bold]{subject_name or subject_pubkey}[/bold]")
    elif subject_input.startswith("ed25519:"):
        subject_pubkey = subject_input
        subject_name = ""
        # Register as known key
        store.register_known_key(subject_pubkey)
    else:
        # Try name lookup
        result = store.find_key_by_name(subject_input)
        if result:
            subject_pubkey = result["pubkey"]
            subject_name = result["name"]
            console.print(f"  Found: [bold]{subject_name}[/bold] ({_short_key(subject_pubkey)})")
        else:
            console.print(f"[red]Unknown contact: {subject_input}. Use a pubkey or add them as a contact first.[/red]")
            store.close()
            raise typer.Exit(1)

    # Step 3: Domain
    console.print()
    console.print("[bold]Skill domain:[/bold]")
    domains = get_domains()
    for i, d in enumerate(domains, 1):
        label = get_domain_label(d)
        console.print(f"  [bold]{i}.[/bold] {label}  [dim]({d})[/dim]")
    domain_choice = Prompt.ask("Choose", choices=[str(i) for i in range(1, len(domains) + 1)])
    domain = domains[int(domain_choice) - 1]
    console.print(f"  Selected: [bold]{get_domain_label(domain)}[/bold]")

    # Step 4: Specific skill
    console.print()
    skills = get_skills(domain)
    console.print(f"[bold]Specific skill in {get_domain_label(domain)}:[/bold]")
    for i, s in enumerate(skills, 1):
        console.print(f"  [bold]{i}.[/bold] {s}")
    skill_choice = Prompt.ask("Choose", choices=[str(i) for i in range(1, len(skills) + 1)])
    skill = skills[int(skill_choice) - 1]
    console.print(f"  Selected: [bold]{skill}[/bold]")

    # Step 5: Proficiency
    console.print()
    console.print("[bold]Proficiency level:[/bold]")
    console.print("  [bold]1[/bold]  ░░░░░  Novice     — Aware of the skill, attempted with guidance")
    console.print("  [bold]2[/bold]  █░░░░  Competent  — Completed the task independently")
    console.print("  [bold]3[/bold]  ██░░░  Proficient — Completed efficiently, handled edge cases")
    console.print("  [bold]4[/bold]  ███░░  Expert     — Deep knowledge, improved the process")
    console.print("  [bold]5[/bold]  ████░  Authority  — Others should learn from this agent")
    prof_choice = Prompt.ask("Rate (1-5)", choices=["1", "2", "3", "4", "5"], default="3")
    proficiency = int(prof_choice)

    # Step 6: Evidence context
    console.print()
    console.print("[bold]Describe the evidence:[/bold]")
    console.print("  [dim]What did you work on together? What was demonstrated?[/dim]")
    context = Prompt.ask("Evidence")

    # Step 7: Artifacts (optional)
    console.print()
    console.print("[dim]Link any evidence (URLs, chain IDs, commit hashes). Comma-separated, or Enter to skip.[/dim]")
    artifacts_input = Prompt.ask("Artifacts", default="")
    artifacts_list = [a.strip() for a in artifacts_input.split(",") if a.strip()] if artifacts_input else []

    # Step 8: Outcome (optional)
    console.print()
    outcome = Prompt.ask("Outcome", default="")

    # Step 9: Confirmation panel
    prof_label = _PROFICIENCY_LABELS.get(proficiency, f"Level {proficiency}")
    prof_bar = _proficiency_bar(proficiency)
    subject_display = subject_name or _short_key(subject_pubkey)

    preview_lines = [
        f"  Type:        [bold]{type_label}[/bold]",
        f"  Subject:     {subject_display}",
        f"  Skill:       {get_domain_label(domain)} / {skill}",
        f"  Proficiency: {prof_bar} {prof_label} ({proficiency}/5)",
        f"  Evidence:    {context[:80]}{'...' if len(context) > 80 else ''}",
    ]
    if artifacts_list:
        preview_lines.append(f"  Artifacts:   {', '.join(artifacts_list)}")
    if outcome:
        preview_lines.append(f"  Outcome:     {outcome}")

    console.print()
    console.print(Panel(
        "\n".join(preview_lines),
        title="Review Attestation",
        border_style="yellow",
    ))

    if not Confirm.ask("\nSign and save this attestation?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        store.close()
        return

    # Step 10: Sign, save, result
    id_row = _get_signing_identity(store, identity_key)
    attestor = Attestor(
        pubkey=id_row["pubkey"],
        name=id_row["name"],
        type=AttestorType(id_row["type"]),
    )
    subject_obj = Subject(pubkey=subject_pubkey, name=subject_name)
    store.register_known_key(subject_pubkey, name=subject_name)

    evidence = Evidence(
        context=context,
        artifacts=artifacts_list,
        outcome=outcome,
    )
    now = datetime.now(timezone.utc)
    skill_obj = Skill(domain=domain, specific=skill, proficiency=Proficiency(proficiency))

    attestation = Attestation(
        type=att_type,
        subject=subject_obj,
        attestor=attestor,
        skill=skill_obj,
        evidence=evidence,
        issued=now,
        expires=now + timedelta(days=365),
    )

    ev_score = score_evidence(attestation.evidence, attestation.type)
    signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
    signed = sign_attestation(attestation, signing_key)
    raw_json = signed.model_dump_json(indent=2)
    store.save_attestation(raw_json)

    ev_bar = _evidence_bar(ev_score.composite)
    console.print()
    console.print(Panel(
        f"  ID:          {signed.id}\n"
        f"  Type:        {type_label}\n"
        f"  Subject:     {subject_display}\n"
        f"  Skill:       {get_domain_label(domain)} / {skill}\n"
        f"  Proficiency: {prof_bar} {prof_label} ({proficiency}/5)\n"
        f"  Evidence:    {ev_bar} {int(ev_score.composite * 100)}%\n"
        f"  Signed by:   {id_row['name']} ({_short_key(id_row['pubkey'])})",
        title="Attestation Created",
        border_style="green",
    ))

    # Offer to submit to Discovery API
    from rich.prompt import Confirm as ConfirmPost
    if ConfirmPost.ask("Submit to Discovery API?", default=True):
        try:
            client = _get_client()
            result = client.submit_attestation(json.loads(raw_json))
            console.print(f"[green]Submitted to Discovery API[/green]")
            if "evidence_score" in result:
                console.print(f"  Server evidence score: {result['evidence_score']}")
        except Exception as e:
            console.print(f"[yellow]Submission skipped: {e}[/yellow]")
            console.print("[dim]You can submit later with: kredo submit " + signed.id + "[/dim]")

    store.close()


@app.command("attest")
def attest(
    att_type: Optional[str] = typer.Argument(None, help="skill|intellectual|community"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Guided attestation flow"),
    subject: Optional[str] = typer.Option(None, "--subject", help="Subject's public key or name"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Skill domain"),
    skill: Optional[str] = typer.Option(None, "--skill", help="Specific skill"),
    proficiency: Optional[int] = typer.Option(None, "--proficiency", help="1-5"),
    context: Optional[str] = typer.Option(None, "--context", help="Evidence context"),
    artifacts: str = typer.Option("", "--artifacts", help="Comma-separated artifact URIs"),
    outcome: str = typer.Option("", "--outcome", help="Interaction outcome"),
    interaction_date: str = typer.Option("", "--interaction-date", help="ISO date"),
    expires_days: int = typer.Option(365, "--expires-days", help="Days until expiry"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Create and sign a skill, intellectual, or community attestation.

    Use --interactive / -i for a guided flow, or pass all flags directly.
    """
    store = _get_store(db)

    if interactive:
        _interactive_attest(store, identity_key, passphrase)
        return

    # --- Flag-based mode (original behavior) ---
    if att_type is None:
        console.print("[red]Missing attestation type. Use: kredo attest skill|intellectual|community[/red]")
        console.print("[dim]Or try: kredo attest --interactive[/dim]")
        store.close()
        raise typer.Exit(1)

    type_map = {
        "skill": AttestationType.SKILL,
        "intellectual": AttestationType.INTELLECTUAL,
        "community": AttestationType.COMMUNITY,
    }
    if att_type not in type_map:
        console.print(f"[red]Invalid type: {att_type}. Use skill, intellectual, or community.[/red]")
        store.close()
        raise typer.Exit(1)

    if not subject:
        console.print("[red]--subject is required. Pass a pubkey or use --interactive.[/red]")
        store.close()
        raise typer.Exit(1)
    if not context:
        console.print("[red]--context is required. Describe the evidence, or use --interactive.[/red]")
        store.close()
        raise typer.Exit(1)

    # Resolve subject by name if not a pubkey
    resolved_subject = _resolve_subject_input(subject, store)

    attestation = _build_attestation(
        type_map[att_type], resolved_subject, store, identity_key,
        domain, skill, proficiency, None,
        context, artifacts, outcome, interaction_date, expires_days,
    )

    # Score evidence
    ev_score = score_evidence(attestation.evidence, attestation.type)

    # Sign
    id_row = _get_signing_identity(store, identity_key)
    signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
    signed = sign_attestation(attestation, signing_key)

    # Store
    raw_json = signed.model_dump_json(indent=2)
    store.save_attestation(raw_json)

    type_label = att_type.replace("_", " ").title()
    prof_info = ""
    if attestation.skill:
        p = attestation.skill.proficiency.value
        prof_label = _PROFICIENCY_LABELS.get(p, f"Level {p}")
        prof_info = f"\n  Proficiency: {_proficiency_bar(p)} {prof_label} ({p}/5)"

    console.print(Panel(
        f"  ID:       {signed.id}\n"
        f"  Type:     {type_label}\n"
        f"  Subject:  {_short_key(resolved_subject)}"
        f"{prof_info}\n"
        f"  Evidence: {_evidence_bar(ev_score.composite)} {int(ev_score.composite * 100)}%\n"
        f"{_evidence_detail(ev_score)}\n"
        f"  Signed:   {id_row['name']} ({_short_key(id_row['pubkey'])})",
        title="Attestation Created",
        border_style="green",
    ))
    store.close()


@app.command("warn")
def warn(
    subject: str = typer.Option(..., "--subject", help="Subject's public key or contact name"),
    category: str = typer.Option(..., "--category", help="spam|malware|deception|data_exfiltration|impersonation"),
    context: str = typer.Option(..., "--context", help="Evidence context (min 100 chars)"),
    artifacts: str = typer.Option(..., "--artifacts", help="Comma-separated artifact URIs (min 1)"),
    outcome: str = typer.Option("", "--outcome", help="Interaction outcome"),
    interaction_date: str = typer.Option("", "--interaction-date", help="ISO date"),
    expires_days: int = typer.Option(365, "--expires-days", help="Days until expiry"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Create and sign a behavioral warning."""
    store = _get_store(db)

    # Resolve subject by name if not a pubkey
    resolved_subject = _resolve_subject_input(subject, store)

    attestation = _build_attestation(
        AttestationType.WARNING, resolved_subject, store, identity_key,
        None, None, None, category,
        context, artifacts, outcome, interaction_date, expires_days,
    )

    # Score evidence
    ev_score = score_evidence(attestation.evidence, attestation.type)
    if ev_score.composite < 0.3:
        console.print(f"[yellow]Warning: evidence quality score is low ({ev_score.composite:.2f}). Consider adding more artifacts or context.[/yellow]")

    # Sign
    id_row = _get_signing_identity(store, identity_key)
    signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
    signed = sign_attestation(attestation, signing_key)

    # Store
    raw_json = signed.model_dump_json(indent=2)
    store.save_attestation(raw_json)

    console.print(Panel(
        f"  ID:       {signed.id}\n"
        f"  Category: {category}\n"
        f"  Subject:  {_short_key(resolved_subject)}\n"
        f"  Evidence: {_evidence_bar(ev_score.composite)} {int(ev_score.composite * 100)}%\n"
        f"  Signed:   {id_row['name']} ({_short_key(id_row['pubkey'])})",
        title="Behavioral Warning Created",
        border_style="red",
    ))
    store.close()


@app.command("verify")
def verify(
    file: Path = typer.Argument(..., help="JSON file to verify"),
):
    """Verify the Ed25519 signature on an attestation, dispute, or revocation."""
    if not file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    data = json.loads(file.read_text())

    try:
        if "warning_id" in data:
            dispute = Dispute(**data)
            verify_dispute(dispute)
            console.print(f"[green]Dispute signature valid[/green] ({dispute.id})")
        elif "attestation_id" in data:
            revocation = Revocation(**data)
            verify_revocation(revocation)
            console.print(f"[green]Revocation signature valid[/green] ({revocation.id})")
        else:
            attestation = Attestation(**data)
            verify_attestation(attestation)
            console.print(f"[green]Attestation signature valid[/green] ({attestation.id})")
            console.print(f"  Type: {attestation.type.value}")
            console.print(f"  Attestor: {attestation.attestor.pubkey}")
            console.print(f"  Subject: {attestation.subject.pubkey}")
            if attestation.skill:
                console.print(f"  Skill: {attestation.skill.domain}/{attestation.skill.specific} (P{attestation.skill.proficiency.value})")
    except Exception as e:
        console.print(f"[red]Verification failed: {e}[/red]")
        raise typer.Exit(1)


@app.command("revoke")
def revoke(
    attestation_id: str = typer.Argument(..., help="ID of attestation to revoke"),
    reason: str = typer.Option(..., "--reason", help="Reason for revocation"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Revoke a previously issued attestation."""
    store = _get_store(db)
    id_row = _get_signing_identity(store, identity_key)

    rev = Revocation(
        attestation_id=attestation_id,
        revoker=Subject(pubkey=id_row["pubkey"], name=id_row["name"]),
        reason=reason,
    )

    signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
    signed = sign_revocation(rev, signing_key)

    raw_json = signed.model_dump_json(indent=2)
    store.save_revocation(raw_json)

    console.print(f"[green]Attestation revoked:[/green] {attestation_id}")
    console.print(f"  Revocation ID: {signed.id}")
    store.close()


@app.command("dispute")
def dispute(
    warning_id: str = typer.Argument(..., help="ID of behavioral warning to dispute"),
    response: str = typer.Option(..., "--response", help="Your dispute response"),
    artifacts: str = typer.Option("", "--artifacts", help="Comma-separated counter-evidence URIs"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Dispute a behavioral warning with a signed counter-response."""
    store = _get_store(db)
    id_row = _get_signing_identity(store, identity_key)

    evidence = None
    if artifacts:
        evidence = Evidence(
            context=response,
            artifacts=[a.strip() for a in artifacts.split(",") if a.strip()],
        )

    disp = Dispute(
        warning_id=warning_id,
        disputor=Subject(pubkey=id_row["pubkey"], name=id_row["name"]),
        response=response,
        evidence=evidence,
    )

    signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
    signed = sign_dispute(disp, signing_key)

    raw_json = signed.model_dump_json(indent=2)
    store.save_dispute(raw_json)

    console.print(f"[green]Dispute filed:[/green] {signed.id}")
    console.print(f"  Warning: {warning_id}")
    store.close()


def _render_human_export(data: dict) -> str:
    """Render an attestation as a human-readable text card."""
    att_type = data.get("type", "")
    attestor = data.get("attestor", {})
    subject = data.get("subject", {})
    evidence = data.get("evidence", {})
    skill = data.get("skill")

    # Type heading
    type_headings = {
        "skill_attestation": "SKILL ATTESTATION",
        "intellectual_contribution": "INTELLECTUAL CONTRIBUTION ATTESTATION",
        "community_contribution": "COMMUNITY CONTRIBUTION ATTESTATION",
        "behavioral_warning": "BEHAVIORAL WARNING",
    }
    heading = type_headings.get(att_type, "ATTESTATION")
    sep = "=" * 55

    lines = [sep, f" KREDO {heading}", sep, ""]

    # Attribution line
    attestor_name = attestor.get("name") or _short_key(attestor.get("pubkey", ""))
    attestor_type = attestor.get("type", "agent")
    subject_name = subject.get("name") or _short_key(subject.get("pubkey", ""))

    if att_type == "behavioral_warning":
        category = data.get("warning_category", "unknown")
        lines.append(f" I, {attestor_name} ({attestor_type}), report that:")
        lines.append("")
        lines.append(f"   {subject_name}")
        lines.append("")
        lines.append(f" exhibited behavior categorized as: {category.upper()}")
    else:
        lines.append(f" I, {attestor_name} ({attestor_type}), attest that:")
        lines.append("")
        lines.append(f"   {subject_name}")
        lines.append("")
        if skill:
            prof_val = skill.get("proficiency", 0)
            prof_label = _PROFICIENCY_LABELS.get(prof_val, f"Level {prof_val}").upper()
            domain_label = get_domain_label(skill.get("domain", ""))
            specific = skill.get("specific", "")
            lines.append(f" demonstrated {prof_label}-level proficiency in:")
            lines.append("")
            lines.append(f"   {domain_label} -> {specific}")

    # Evidence
    lines.append("")
    lines.append(" Evidence:")
    context = evidence.get("context", "")
    # Wrap context at ~60 chars
    words = context.split()
    current_line = "   \""
    for word in words:
        if len(current_line) + len(word) + 1 > 60:
            lines.append(current_line)
            current_line = "    " + word
        else:
            current_line += (" " if current_line.strip() else "") + word
    if current_line.strip():
        lines.append(current_line + "\"")

    artifacts = evidence.get("artifacts", [])
    if artifacts:
        lines.append("")
        lines.append("   Artifacts:")
        for art in artifacts:
            lines.append(f"   * {art}")

    outcome = evidence.get("outcome", "")
    if outcome:
        lines.append("")
        lines.append(f"   Outcome: {outcome}")

    # Dates
    issued = data.get("issued", "")
    expires = data.get("expires", "")
    if issued and "T" in str(issued):
        issued = str(issued).split("T")[0]
    if expires and "T" in str(expires):
        expires = str(expires).split("T")[0]
    lines.append("")
    lines.append(f" Issued:  {issued}")
    lines.append(f" Expires: {expires}")

    # Signature
    sig = data.get("signature", "")
    if sig:
        lines.append(f" Signature: Valid (ed25519)")
    else:
        lines.append(f" Signature: UNSIGNED")

    lines.append("")
    lines.append(sep)
    return "\n".join(lines)


def _render_markdown_export(data: dict) -> str:
    """Render an attestation as shareable Markdown."""
    att_type = data.get("type", "")
    attestor = data.get("attestor", {})
    subject = data.get("subject", {})
    evidence = data.get("evidence", {})
    skill = data.get("skill")

    type_headings = {
        "skill_attestation": "Skill Attestation",
        "intellectual_contribution": "Intellectual Contribution",
        "community_contribution": "Community Contribution",
        "behavioral_warning": "Behavioral Warning",
    }
    heading = type_headings.get(att_type, "Attestation")

    attestor_name = attestor.get("name") or _short_key(attestor.get("pubkey", ""))
    attestor_type = attestor.get("type", "agent")
    subject_name = subject.get("name") or _short_key(subject.get("pubkey", ""))

    lines = [f"## Kredo {heading}", ""]

    if att_type == "behavioral_warning":
        category = data.get("warning_category", "unknown")
        lines.append(f"**{attestor_name}** ({attestor_type}) reports that **{subject_name}** exhibited behavior categorized as **{category}**.")
    else:
        if skill:
            prof_val = skill.get("proficiency", 0)
            prof_label = _PROFICIENCY_LABELS.get(prof_val, f"Level {prof_val}")
            domain_label = get_domain_label(skill.get("domain", ""))
            specific = skill.get("specific", "")
            lines.append(f"**{attestor_name}** ({attestor_type}) attests that **{subject_name}** demonstrated **{prof_label}** proficiency in **{domain_label} / {specific}**.")
        else:
            lines.append(f"**{attestor_name}** ({attestor_type}) attests to the work of **{subject_name}**.")

    # Evidence
    context = evidence.get("context", "")
    lines.append("")
    lines.append("### Evidence")
    lines.append("")
    lines.append(f"> {context}")

    artifacts = evidence.get("artifacts", [])
    if artifacts:
        lines.append("")
        lines.append("**Artifacts:**")
        for art in artifacts:
            lines.append(f"- `{art}`")

    outcome = evidence.get("outcome", "")
    if outcome:
        lines.append("")
        lines.append(f"**Outcome:** {outcome}")

    # Metadata
    issued = data.get("issued", "")
    expires = data.get("expires", "")
    if issued and "T" in str(issued):
        issued = str(issued).split("T")[0]
    if expires and "T" in str(expires):
        expires = str(expires).split("T")[0]
    sig = data.get("signature", "")

    lines.append("")
    lines.append("---")
    lines.append(f"*Issued: {issued} | Expires: {expires} | Signature: {'Valid (ed25519)' if sig else 'UNSIGNED'}*")

    return "\n".join(lines)


@app.command("export")
def export_cmd(
    attestation_id: str = typer.Argument(..., help="Attestation ID to export"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
    fmt: str = typer.Option("json", "--format", "-f", help="Output format: json, human, markdown"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Export an attestation as JSON, human-readable text, or Markdown."""
    if fmt not in ("json", "human", "markdown"):
        console.print(f"[red]Unknown format: {fmt}. Use json, human, or markdown.[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    json_str = store.export_attestation_json(attestation_id)
    if json_str is None:
        console.print(f"[red]Attestation not found: {attestation_id}[/red]")
        store.close()
        raise typer.Exit(1)

    if fmt == "json":
        result_text = json_str
    else:
        data = json.loads(json_str)
        if fmt == "human":
            result_text = _render_human_export(data)
        else:
            result_text = _render_markdown_export(data)

    if output:
        output.write_text(result_text)
        console.print(f"[green]Exported to:[/green] {output}")
    else:
        sys.stdout.write(result_text + "\n")
    store.close()


@app.command("import")
def import_cmd(
    file: Path = typer.Argument(..., help="JSON file to import"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Import an attestation from a JSON file."""
    if not file.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    json_str = file.read_text()
    att_id = store.import_attestation_json(json_str)
    console.print(f"[green]Imported attestation:[/green] {att_id}")
    store.close()


# --- Trust Commands ---

@trust_app.command("who-attested")
def trust_who_attested(
    pubkey: str = typer.Argument(..., help="Subject's public key"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Show all attestors who have attested for a subject."""
    store = _get_store(db)
    attestors = store.get_attestors_for(pubkey)
    if not attestors:
        console.print("No attestations found for this subject.")
        store.close()
        return

    table = Table(title=f"Attestors for {_short_key(pubkey)}")
    table.add_column("Attestor")
    table.add_column("Type", width=6)
    table.add_column("Count", width=5)
    for a in attestors:
        table.add_row(_short_key(a["attestor_pubkey"]), a["type"], str(a["attestation_count"]))
    console.print(table)
    store.close()


@trust_app.command("attested-by")
def trust_attested_by(
    pubkey: str = typer.Argument(..., help="Attestor's public key"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Show all subjects attested by a given attestor."""
    store = _get_store(db)
    subjects = store.get_attested_by(pubkey)
    if not subjects:
        console.print("No attestations found from this attestor.")
        store.close()
        return

    table = Table(title=f"Attested by {_short_key(pubkey)}")
    table.add_column("Subject")
    table.add_column("Count", width=5)
    for s in subjects:
        table.add_row(_short_key(s["subject_pubkey"]), str(s["attestation_count"]))
    console.print(table)
    store.close()


# --- Taxonomy Commands ---

@taxonomy_app.command("domains")
def taxonomy_domains():
    """List all skill domains."""
    domains = get_domains()
    table = Table(title="Kredo Skill Taxonomy — Domains")
    table.add_column("Domain")
    table.add_column("Skills")
    for d in domains:
        skills = get_skills(d)
        table.add_row(d, ", ".join(skills))
    console.print(table)


@taxonomy_app.command("skills")
def taxonomy_skills(
    domain: str = typer.Argument(..., help="Domain to list skills for"),
):
    """List specific skills within a domain."""
    from kredo.taxonomy import suggest_domain
    try:
        skills = get_skills(domain)
    except Exception:
        suggestion = suggest_domain(domain)
        hint = f" Did you mean [bold]{suggestion}[/bold]?" if suggestion else ""
        console.print(f"[red]Unknown domain: '{domain}'.{hint}[/red]")
        console.print("[dim]Run 'kredo taxonomy domains' to see all options.[/dim]")
        raise typer.Exit(1)

    table = Table(title=f"Skills in {domain}")
    table.add_column("Skill")
    for s in skills:
        table.add_row(s)
    console.print(table)


@taxonomy_app.command("add-domain")
def taxonomy_add_domain(
    domain_id: str = typer.Argument(..., help="Hyphenated slug, e.g. 'vise-operations'"),
    label: str = typer.Option(..., "--label", help="Human-readable label"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Add a custom domain to the taxonomy."""
    import re
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", domain_id):
        console.print("[red]Domain ID must be a hyphenated lowercase slug (e.g. 'vise-operations').[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    _set_taxonomy_store(store)
    id_row = _get_signing_identity(store, identity_key)

    try:
        store.create_custom_domain(domain_id, label, id_row["pubkey"])
        _invalidate_taxonomy_cache()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        store.close()
        raise typer.Exit(1)

    console.print(f"[green]Domain created:[/green] {label} ({domain_id})")

    # Submit to Discovery API
    try:
        from kredo._canonical import canonical_json
        from kredo.identity import load_signing_key
        from nacl.encoding import HexEncoder

        signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
        payload = {"action": "create_domain", "id": domain_id, "label": label, "pubkey": id_row["pubkey"]}
        sig_bytes = signing_key.sign(canonical_json(payload), encoder=HexEncoder)
        signature = "ed25519:" + sig_bytes.signature.decode("ascii")

        client = _get_client(api_url)
        client._request("POST", "/taxonomy/domains", body={
            "id": domain_id, "label": label, "pubkey": id_row["pubkey"], "signature": signature,
        })
        console.print("[green]Submitted to Discovery API[/green]")
    except Exception as e:
        console.print(f"[yellow]API submission skipped: {e}[/yellow]")

    store.close()


@taxonomy_app.command("add-skill")
def taxonomy_add_skill(
    domain: str = typer.Argument(..., help="Domain to add the skill to"),
    skill_id: str = typer.Argument(..., help="Hyphenated slug, e.g. 'chain-orchestration'"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Add a custom skill to an existing domain."""
    import re
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", skill_id):
        console.print("[red]Skill ID must be a hyphenated lowercase slug (e.g. 'chain-orchestration').[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    _set_taxonomy_store(store)
    id_row = _get_signing_identity(store, identity_key)

    try:
        store.create_custom_skill(domain, skill_id, id_row["pubkey"])
        _invalidate_taxonomy_cache()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        store.close()
        raise typer.Exit(1)

    console.print(f"[green]Skill created:[/green] {skill_id} in {domain}")

    # Submit to Discovery API
    try:
        from kredo._canonical import canonical_json
        from kredo.identity import load_signing_key
        from nacl.encoding import HexEncoder

        signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
        payload = {"action": "create_skill", "domain": domain, "id": skill_id, "pubkey": id_row["pubkey"]}
        sig_bytes = signing_key.sign(canonical_json(payload), encoder=HexEncoder)
        signature = "ed25519:" + sig_bytes.signature.decode("ascii")

        client = _get_client(api_url)
        client._request("POST", f"/taxonomy/domains/{domain}/skills", body={
            "id": skill_id, "pubkey": id_row["pubkey"], "signature": signature,
        })
        console.print("[green]Submitted to Discovery API[/green]")
    except Exception as e:
        console.print(f"[yellow]API submission skipped: {e}[/yellow]")

    store.close()


@taxonomy_app.command("remove-domain")
def taxonomy_remove_domain(
    domain_id: str = typer.Argument(..., help="Domain ID to remove"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Remove a custom domain (creator only). Cascades to its skills."""
    store = _get_store(db)
    _set_taxonomy_store(store)
    id_row = _get_signing_identity(store, identity_key)

    try:
        store.delete_custom_domain(domain_id, id_row["pubkey"])
        _invalidate_taxonomy_cache()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        store.close()
        raise typer.Exit(1)

    console.print(f"[green]Domain removed:[/green] {domain_id}")

    # Submit to Discovery API
    try:
        from kredo._canonical import canonical_json
        from kredo.identity import load_signing_key
        from nacl.encoding import HexEncoder

        signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
        payload = {"action": "delete_domain", "domain": domain_id, "pubkey": id_row["pubkey"]}
        sig_bytes = signing_key.sign(canonical_json(payload), encoder=HexEncoder)
        signature = "ed25519:" + sig_bytes.signature.decode("ascii")

        client = _get_client(api_url)
        client._request("DELETE", f"/taxonomy/domains/{domain_id}", body={
            "pubkey": id_row["pubkey"], "signature": signature,
        })
        console.print("[green]Removed from Discovery API[/green]")
    except Exception as e:
        console.print(f"[yellow]API removal skipped: {e}[/yellow]")

    store.close()


@taxonomy_app.command("remove-skill")
def taxonomy_remove_skill(
    domain: str = typer.Argument(..., help="Domain containing the skill"),
    skill_id: str = typer.Argument(..., help="Skill ID to remove"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Remove a custom skill (creator only)."""
    store = _get_store(db)
    _set_taxonomy_store(store)
    id_row = _get_signing_identity(store, identity_key)

    try:
        store.delete_custom_skill(domain, skill_id, id_row["pubkey"])
        _invalidate_taxonomy_cache()
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        store.close()
        raise typer.Exit(1)

    console.print(f"[green]Skill removed:[/green] {skill_id} from {domain}")

    # Submit to Discovery API
    try:
        from kredo._canonical import canonical_json
        from kredo.identity import load_signing_key
        from nacl.encoding import HexEncoder

        signing_key = load_signing_key(id_row["pubkey"], store, passphrase)
        payload = {"action": "delete_skill", "domain": domain, "skill": skill_id, "pubkey": id_row["pubkey"]}
        sig_bytes = signing_key.sign(canonical_json(payload), encoder=HexEncoder)
        signature = "ed25519:" + sig_bytes.signature.decode("ascii")

        client = _get_client(api_url)
        client._request("DELETE", f"/taxonomy/domains/{domain}/skills/{skill_id}", body={
            "pubkey": id_row["pubkey"], "signature": signature,
        })
        console.print("[green]Removed from Discovery API[/green]")
    except Exception as e:
        console.print(f"[yellow]API removal skipped: {e}[/yellow]")

    store.close()


# --- Contacts Commands ---

@contacts_app.command("add")
def contacts_add(
    name: str = typer.Option(..., "--name", help="Human-readable name for this contact"),
    pubkey: str = typer.Option(..., "--pubkey", help="Their public key (ed25519:...)"),
    contact_type: str = typer.Option("agent", "--type", help="agent or human"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Add an agent or human to your local contacts."""
    if not pubkey.startswith("ed25519:"):
        console.print("[red]Invalid public key format.[/red] Keys look like: [bold]ed25519:a3f8b2c1...[/bold]")
        raise typer.Exit(1)
    if contact_type not in ("agent", "human"):
        console.print("[red]Type must be 'agent' or 'human'.[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    store.register_known_key(pubkey, name=name, attestor_type=contact_type)
    console.print(f"[green]Contact added:[/green] {name}")
    console.print(f"  Pubkey: {_short_key(pubkey)}")
    console.print(f"  Type:   {contact_type}")
    store.close()


@contacts_app.command("list")
def contacts_list(
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """List all known contacts."""
    store = _get_store(db)
    contacts = store.list_contacts()
    identities = store.list_identities()
    store.close()

    if not contacts and not identities:
        console.print("[dim]No contacts yet. Add one with: kredo contacts add --name '...' --pubkey '...'[/dim]")
        return

    table = Table(title="Contacts")
    table.add_column("#", width=3, style="dim")
    table.add_column("Name")
    table.add_column("Type", width=6)
    table.add_column("Pubkey")
    table.add_column("Last Seen", width=12)

    n = 0
    for ident in identities:
        n += 1
        table.add_row(
            str(n),
            f"[bold]{ident['name']}[/bold] [dim](you)[/dim]",
            ident["type"],
            _short_key(ident["pubkey"]),
            "",
        )
    for contact in contacts:
        # Skip duplicates already shown as identities
        if any(ident["pubkey"] == contact["pubkey"] for ident in identities):
            continue
        n += 1
        last_seen = contact.get("last_seen", "")
        if last_seen and "T" in last_seen:
            last_seen = last_seen.split("T")[0]
        table.add_row(
            str(n),
            contact["name"] or "[dim]unnamed[/dim]",
            contact.get("type", "agent"),
            _short_key(contact["pubkey"]),
            last_seen,
        )

    console.print(table)


@contacts_app.command("remove")
def contacts_remove(
    name_or_pubkey: str = typer.Argument(..., help="Name or pubkey to remove"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Remove a contact by name or pubkey."""
    store = _get_store(db)
    removed = store.remove_contact(name_or_pubkey)
    store.close()

    if removed:
        console.print(f"[green]Contact removed:[/green] {name_or_pubkey}")
    else:
        console.print(f"[yellow]Contact not found:[/yellow] {name_or_pubkey}")


# --- Network Commands (Discovery API) ---

_PROFICIENCY_LABELS = {
    1: "Novice",
    2: "Competent",
    3: "Proficient",
    4: "Expert",
    5: "Authority",
}


def _get_client(api_url: Optional[str] = None) -> KredoClient:
    return KredoClient(base_url=api_url)


def _proficiency_bar(level: int) -> str:
    """Visual proficiency bar: █████ for 5, █░░░░ for 1."""
    filled = "█" * level
    empty = "░" * (5 - level)
    return filled + empty


@app.command("register")
def register_cmd(
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Register your identity with the Discovery API."""
    store = _get_store(db)
    id_row = _get_signing_identity(store, identity_key)
    store.close()

    client = _get_client(api_url)
    try:
        result = client.register(
            pubkey=id_row["pubkey"],
            name=id_row["name"],
            agent_type=id_row["type"],
        )
        console.print(f"[green]Registered with Discovery API:[/green]")
        console.print(f"  Name: {result.get('name', id_row['name'])}")
        console.print(f"  Type: {result.get('type', id_row['type'])}")
        console.print(f"  Pubkey: {id_row['pubkey']}")
        console.print(f"  API: {client.base_url}")
    except KredoAPIError as e:
        if e.status_code == 429:
            console.print(f"[yellow]Already registered or rate limited.[/yellow] {e.message}")
        else:
            console.print(f"[red]Registration failed: {e}[/red]")
            raise typer.Exit(1)


@app.command("submit")
def submit_cmd(
    attestation_id: str = typer.Argument(..., help="Local attestation ID to submit"),
    pin: bool = typer.Option(False, "--pin", help="Also pin to IPFS (best-effort)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Submit a locally-signed attestation to the Discovery API."""
    store = _get_store(db)
    json_str = store.export_attestation_json(attestation_id)

    if json_str is None:
        console.print(f"[red]Attestation not found locally: {attestation_id}[/red]")
        store.close()
        raise typer.Exit(1)

    attestation = json.loads(json_str)
    client = _get_client(api_url)

    try:
        result = client.submit_attestation(attestation)
        console.print(f"[green]Attestation submitted to Discovery API:[/green]")
        console.print(f"  ID: {result.get('id', attestation_id)}")
        if "evidence_score" in result:
            console.print(f"  Evidence score: {result['evidence_score']}")
        console.print(f"  API: {client.base_url}")
    except KredoAPIError as e:
        console.print(f"[red]Submission failed: {e}[/red]")
        store.close()
        raise typer.Exit(1)

    # Best-effort IPFS pin — doesn't fail the submit
    if pin:
        if not ipfs_enabled():
            console.print("[yellow]IPFS not configured. Set KREDO_IPFS_PROVIDER to enable.[/yellow]")
        else:
            try:
                provider = get_provider()
                cid = pin_document(attestation, "attestation", provider)
                store.save_ipfs_pin(cid, attestation_id, "attestation", provider.name)
                console.print(f"[green]Pinned to IPFS:[/green] {cid}")
            except IPFSError as e:
                console.print(f"[yellow]IPFS pin failed (submit succeeded): {e}[/yellow]")

    store.close()


@app.command("lookup")
def lookup_cmd(
    pubkey: Optional[str] = typer.Argument(None, help="Public key to look up (default: your own)"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override default identity for self-lookup"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Look up an agent's profile and reputation from the Discovery API."""
    if pubkey is None:
        store = _get_store(db)
        id_row = _get_signing_identity(store, identity_key)
        pubkey = id_row["pubkey"]
        store.close()

    client = _get_client(api_url)

    try:
        profile = client.get_profile(pubkey)
    except KredoAPIError as e:
        if e.status_code == 404:
            console.print(f"[yellow]Agent not found on the network: {pubkey}[/yellow]")
            console.print("Register first with: kredo register")
        else:
            console.print(f"[red]Lookup failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        sys.stdout.write(json.dumps(profile, indent=2, default=str) + "\n")
        return

    _render_profile(profile)


def _render_profile(profile: dict) -> None:
    """Render a rich reputation display for an agent profile."""
    name = profile.get("name", "Unknown")
    agent_type = profile.get("type", "agent")
    pubkey = profile.get("pubkey", "")
    registered = profile.get("registered", "")
    if registered and "T" in registered:
        registered = registered.split("T")[0]

    # --- Header panel ---
    header_lines = []
    header_lines.append(f"[bold]{name}[/bold]  [dim]({agent_type})[/dim]")
    header_lines.append(f"[dim]{pubkey}[/dim]")
    if registered:
        header_lines.append(f"Registered: {registered}")
    console.print(Panel("\n".join(header_lines), title="Agent Profile", border_style="blue"))

    # --- Skills ---
    skills = profile.get("skills", [])
    if skills:
        console.print()
        console.print("[bold]Skills[/bold]")
        console.print("─" * 60)
        for s in skills:
            domain = s.get("domain", "")
            specific = s.get("specific", "")
            max_prof = s.get("max_proficiency", 0)
            avg_prof = s.get("avg_proficiency", 0)
            att_count = s.get("attestation_count", 0)
            label = _PROFICIENCY_LABELS.get(max_prof, f"Level {max_prof}")
            bar = _proficiency_bar(max_prof)

            skill_name = f"{domain} / {specific}"
            console.print(
                f"  {skill_name:<45} {bar} {label} ({max_prof}/5)"
            )
            console.print(
                f"  {'':45} [dim]{att_count} attestation{'s' if att_count != 1 else ''}[/dim]"
            )
    else:
        console.print()
        console.print("[dim]No skills attested yet.[/dim]")

    # --- Reputation summary ---
    att_counts = profile.get("attestation_count", {})
    total = att_counts.get("total", 0)
    by_agents = att_counts.get("by_agents", 0)
    by_humans = att_counts.get("by_humans", 0)
    ev_quality = profile.get("evidence_quality_avg")
    warnings = profile.get("warnings", [])
    active_warnings = [w for w in warnings if not w.get("is_revoked")]
    total_disputes = sum(w.get("dispute_count", 0) for w in warnings)

    console.print()
    console.print("[bold]Reputation[/bold]")
    console.print("─" * 60)
    console.print(f"  Attestations: {total} total ({by_agents} by agents, {by_humans} by humans)")
    if ev_quality is not None:
        ev_bar = _evidence_bar(ev_quality)
        console.print(f"  Evidence quality: {ev_bar} {int(ev_quality * 100)}%")
    else:
        console.print("  Evidence quality: [dim]n/a[/dim]")
    console.print(f"  Warnings: {len(active_warnings)}  ·  Disputes: {total_disputes}")

    # --- Trust network ---
    trust = profile.get("trust_network", [])
    if trust:
        console.print()
        console.print("[bold]Trust Network[/bold]")
        console.print("─" * 60)
        for t in trust:
            t_pubkey = t.get("pubkey", "")
            t_type = t.get("type", "agent")
            t_count = t.get("attestation_count_for_subject", 0)
            t_own = t.get("attestor_own_attestation_count", 0)
            own_label = f" ({t_own} attestation{'s' if t_own != 1 else ''} received)" if t_own > 0 else ""
            console.print(
                f"  {_short_key(t_pubkey)} [dim]({t_type})[/dim] — "
                f"{t_count} attestation{'s' if t_count != 1 else ''} for this member{own_label}"
            )
    else:
        console.print()
        console.print("[dim]No trust network yet.[/dim]")

    console.print()


@app.command("search")
def search_cmd(
    subject: Optional[str] = typer.Option(None, "--subject", help="Subject's public key"),
    attestor: Optional[str] = typer.Option(None, "--attestor", help="Attestor's public key"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Skill domain"),
    skill: Optional[str] = typer.Option(None, "--skill", help="Specific skill"),
    att_type: Optional[str] = typer.Option(None, "--type", help="Attestation type"),
    min_proficiency: Optional[int] = typer.Option(None, "--min-proficiency", help="Minimum proficiency (1-5)"),
    include_revoked: bool = typer.Option(False, "--include-revoked", help="Include revoked attestations"),
    limit: int = typer.Option(20, "--limit", help="Max results"),
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Search attestations on the Discovery API."""
    client = _get_client(api_url)

    try:
        result = client.search(
            subject=subject,
            attestor=attestor,
            domain=domain,
            skill=skill,
            att_type=att_type,
            min_proficiency=min_proficiency,
            include_revoked=include_revoked,
            limit=limit,
        )
    except KredoAPIError as e:
        console.print(f"[red]Search failed: {e}[/red]")
        raise typer.Exit(1)

    if json_output:
        sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
        return

    attestations = result.get("attestations", [])
    if not attestations:
        console.print("[dim]No attestations found.[/dim]")
        return

    table = Table(title=f"Search Results ({len(attestations)} attestation{'s' if len(attestations) != 1 else ''})")
    table.add_column("Type", width=12)
    table.add_column("Subject")
    table.add_column("Skill")
    table.add_column("Prof.", width=11)
    table.add_column("Attestor")
    table.add_column("Issued", width=10)

    for att in attestations:
        att_type_val = att.get("type", "")
        type_short = att_type_val.replace("_attestation", "").replace("_contribution", "").replace("behavioral_", "")
        subj = att.get("subject", {})
        subject_display = subj.get("name") or _short_key(subj.get("pubkey", ""))
        attor = att.get("attestor", {})
        attestor_display = attor.get("name") or _short_key(attor.get("pubkey", ""))
        skill_info = ""
        prof_display = ""
        if att.get("skill"):
            skill_info = f"{att['skill'].get('domain', '')}/{att['skill'].get('specific', '')}"
            prof_val = att["skill"].get("proficiency", 0)
            if isinstance(prof_val, int) and 1 <= prof_val <= 5:
                prof_display = f"{_proficiency_bar(prof_val)} {prof_val}"
            else:
                prof_display = str(prof_val)
        issued = att.get("issued", "")
        if issued and "T" in issued:
            issued = issued.split("T")[0]

        table.add_row(type_short, subject_display, skill_info, prof_display, attestor_display, issued)

    console.print(table)


# --- IPFS Commands ---

def _resolve_document(doc_id: str, store: KredoStore) -> tuple[Optional[dict], str]:
    """Look up a document by ID across attestations, revocations, disputes.

    Returns (document_dict, document_type) or (None, "").
    """
    att = store.get_attestation(doc_id)
    if att is not None:
        return att, "attestation"
    rev = store.get_revocation(doc_id)
    if rev is not None:
        return rev, "revocation"
    disp = store.get_dispute(doc_id)
    if disp is not None:
        return disp, "dispute"
    return None, ""


@ipfs_app.command("pin")
def ipfs_pin_cmd(
    doc_id: str = typer.Argument(..., help="Attestation, revocation, or dispute ID to pin"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Pin a Kredo document to IPFS."""
    if not ipfs_enabled():
        console.print("[red]IPFS not configured. Set KREDO_IPFS_PROVIDER to 'local' or 'remote'.[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    doc, doc_type = _resolve_document(doc_id, store)
    if doc is None:
        console.print(f"[red]Document not found: {doc_id}[/red]")
        store.close()
        raise typer.Exit(1)

    try:
        provider = get_provider()
        cid = pin_document(doc, doc_type, provider)
        store.save_ipfs_pin(cid, doc_id, doc_type, provider.name)
        console.print(f"[green]Pinned {doc_type} to IPFS:[/green]")
        console.print(f"  CID: {cid}")
        console.print(f"  Document: {doc_id}")
        console.print(f"  Provider: {provider.name}")
    except IPFSError as e:
        console.print(f"[red]IPFS pin failed: {e}[/red]")
        store.close()
        raise typer.Exit(1)
    store.close()


@ipfs_app.command("fetch")
def ipfs_fetch_cmd(
    cid: str = typer.Argument(..., help="IPFS CID to fetch"),
    verify_sig: bool = typer.Option(True, "--verify/--no-verify", help="Verify Ed25519 signature"),
    import_doc: bool = typer.Option(False, "--import", help="Import into local store"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Fetch a Kredo document from IPFS by CID."""
    if not ipfs_enabled():
        console.print("[red]IPFS not configured. Set KREDO_IPFS_PROVIDER to 'local' or 'remote'.[/red]")
        raise typer.Exit(1)

    try:
        doc = fetch_document(cid)
    except IPFSError as e:
        console.print(f"[red]IPFS fetch failed: {e}[/red]")
        raise typer.Exit(1)

    # Determine document type
    if "warning_id" in doc:
        doc_type = "dispute"
    elif "attestation_id" in doc:
        doc_type = "revocation"
    else:
        doc_type = "attestation"

    # Verify signature if requested
    if verify_sig and doc.get("signature"):
        try:
            if doc_type == "attestation":
                att = Attestation(**doc)
                verify_attestation(att)
            elif doc_type == "revocation":
                rev = Revocation(**doc)
                verify_revocation(rev)
            elif doc_type == "dispute":
                disp = Dispute(**doc)
                verify_dispute(disp)
            console.print(f"[green]Signature valid[/green] ({doc_type})")
        except Exception as e:
            console.print(f"[red]Signature verification failed: {e}[/red]")
            if not import_doc:
                console.print(json.dumps(doc, indent=2))
            raise typer.Exit(1)

    # Import if requested
    if import_doc:
        store = _get_store(db)
        raw = json.dumps(doc)
        if doc_type == "attestation":
            store.save_attestation(raw)
        elif doc_type == "revocation":
            store.save_revocation(raw)
        elif doc_type == "dispute":
            store.save_dispute(raw)
        console.print(f"[green]Imported {doc_type}:[/green] {doc.get('id', cid)}")
        store.close()
    else:
        console.print(json.dumps(doc, indent=2))


@ipfs_app.command("status")
def ipfs_status_cmd(
    doc_id: Optional[str] = typer.Argument(None, help="Document ID to check (omit for all)"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Check IPFS pin status for a document or list all pins."""
    store = _get_store(db)

    if doc_id:
        cid = store.get_ipfs_cid(doc_id)
        if cid is None:
            console.print(f"[dim]No IPFS pin found for: {doc_id}[/dim]")
        else:
            pin = store.get_ipfs_pin(cid)
            console.print(f"[green]Pinned:[/green]")
            console.print(f"  CID: {cid}")
            console.print(f"  Type: {pin['document_type']}")
            console.print(f"  Provider: {pin['provider']}")
            console.print(f"  Pinned at: {pin['pinned_at']}")
    else:
        pins = store.list_ipfs_pins()
        if not pins:
            console.print("[dim]No IPFS pins found.[/dim]")
        else:
            table = Table(title=f"IPFS Pins ({len(pins)})")
            table.add_column("CID")
            table.add_column("Document ID")
            table.add_column("Type")
            table.add_column("Provider")
            table.add_column("Pinned At")
            for p in pins:
                table.add_row(
                    p["cid"],
                    p["document_id"],
                    p["document_type"],
                    p["provider"],
                    p["pinned_at"],
                )
            console.print(table)

    store.close()


# --- Entry point for typer ---

def _cli():
    app()


if __name__ == "__main__":
    _cli()
