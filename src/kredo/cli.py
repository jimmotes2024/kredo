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
from kredo.store import KredoStore
from kredo.taxonomy import get_domains, get_skills

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
app.add_typer(identity_app, name="identity")
app.add_typer(trust_app, name="trust")
app.add_typer(taxonomy_app, name="taxonomy")


def _get_store(db: Optional[Path] = None) -> KredoStore:
    return KredoStore(db_path=db)


def _get_signing_identity(store: KredoStore, identity_key: Optional[str] = None):
    """Resolve the signing identity — explicit key or default."""
    if identity_key:
        row = store.get_identity(identity_key)
        return row
    default = store.get_default_identity()
    if default is None:
        console.print("[red]No default identity. Create one with: kredo identity create[/red]")
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
            console.print("[red]--domain, --skill, and --proficiency required for this attestation type[/red]")
            raise typer.Exit(1)
        skill_obj = Skill(domain=domain, specific=skill, proficiency=Proficiency(proficiency))
    else:
        if not warning_category:
            console.print("[red]--category required for behavioral warnings[/red]")
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


@app.command("attest")
def attest(
    att_type: str = typer.Argument(..., help="skill|intellectual|community"),
    subject: str = typer.Option(..., "--subject", help="Subject's public key"),
    domain: Optional[str] = typer.Option(None, "--domain", help="Skill domain"),
    skill: Optional[str] = typer.Option(None, "--skill", help="Specific skill"),
    proficiency: Optional[int] = typer.Option(None, "--proficiency", help="1-5"),
    context: str = typer.Option(..., "--context", help="Evidence context"),
    artifacts: str = typer.Option("", "--artifacts", help="Comma-separated artifact URIs"),
    outcome: str = typer.Option("", "--outcome", help="Interaction outcome"),
    interaction_date: str = typer.Option("", "--interaction-date", help="ISO date"),
    expires_days: int = typer.Option(365, "--expires-days", help="Days until expiry"),
    identity_key: Optional[str] = typer.Option(None, "--identity", help="Override signing identity"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", help="Key passphrase"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Create and sign a skill, intellectual, or community attestation."""
    type_map = {
        "skill": AttestationType.SKILL,
        "intellectual": AttestationType.INTELLECTUAL,
        "community": AttestationType.COMMUNITY,
    }
    if att_type not in type_map:
        console.print(f"[red]Invalid type: {att_type}. Use skill, intellectual, or community.[/red]")
        raise typer.Exit(1)

    store = _get_store(db)
    attestation = _build_attestation(
        type_map[att_type], subject, store, identity_key,
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

    console.print(f"[green]Attestation created and signed:[/green] {signed.id}")
    console.print(f"  Type: {signed.type.value}")
    console.print(f"  Evidence score: {ev_score.composite:.2f}")
    store.close()


@app.command("warn")
def warn(
    subject: str = typer.Option(..., "--subject", help="Subject's public key"),
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
    attestation = _build_attestation(
        AttestationType.WARNING, subject, store, identity_key,
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

    console.print(f"[green]Behavioral warning created and signed:[/green] {signed.id}")
    console.print(f"  Category: {category}")
    console.print(f"  Evidence score: {ev_score.composite:.2f}")
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


@app.command("export")
def export_cmd(
    attestation_id: str = typer.Argument(..., help="Attestation ID to export"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Export an attestation as portable JSON."""
    store = _get_store(db)
    json_str = store.export_attestation_json(attestation_id)
    if json_str is None:
        console.print(f"[red]Attestation not found: {attestation_id}[/red]")
        store.close()
        raise typer.Exit(1)

    if output:
        output.write_text(json_str)
        console.print(f"[green]Exported to:[/green] {output}")
    else:
        console.print(json_str)
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

    table = Table(title=f"Attestors for {pubkey[:30]}...")
    table.add_column("Attestor Pubkey")
    table.add_column("Type")
    table.add_column("Count")
    for a in attestors:
        table.add_row(a["attestor_pubkey"], a["type"], str(a["attestation_count"]))
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

    table = Table(title=f"Attested by {pubkey[:30]}...")
    table.add_column("Subject Pubkey")
    table.add_column("Count")
    for s in subjects:
        table.add_row(s["subject_pubkey"], str(s["attestation_count"]))
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
    try:
        skills = get_skills(domain)
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Skills in {domain}")
    table.add_column("Skill")
    for s in skills:
        table.add_row(s)
    console.print(table)


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
    api_url: Optional[str] = typer.Option(None, "--api-url", help="Discovery API URL"),
    db: Optional[Path] = typer.Option(None, "--db", hidden=True),
):
    """Submit a locally-signed attestation to the Discovery API."""
    store = _get_store(db)
    json_str = store.export_attestation_json(attestation_id)
    store.close()

    if json_str is None:
        console.print(f"[red]Attestation not found locally: {attestation_id}[/red]")
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
        raise typer.Exit(1)


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
        console.print(f"  Evidence quality: {ev_quality:.2f}")
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
            # Truncate pubkey for display
            short_key = t_pubkey[:20] + "..." if len(t_pubkey) > 20 else t_pubkey
            own_label = f" ({t_own} attestation{'s' if t_own != 1 else ''} received)" if t_own > 0 else ""
            console.print(
                f"  {short_key} [dim]({t_type})[/dim] — "
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
    table.add_column("Prof.", width=5)
    table.add_column("Attestor")
    table.add_column("Issued", width=10)

    for att in attestations:
        att_type_val = att.get("type", "")
        # Shorten type name
        type_short = att_type_val.replace("_attestation", "").replace("_contribution", "").replace("behavioral_", "")
        subject_name = att.get("subject", {}).get("name") or att.get("subject", {}).get("pubkey", "")[:16] + "..."
        attestor_name = att.get("attestor", {}).get("name") or att.get("attestor", {}).get("pubkey", "")[:16] + "..."
        skill_info = ""
        prof_info = ""
        if att.get("skill"):
            skill_info = f"{att['skill'].get('domain', '')}/{att['skill'].get('specific', '')}"
            prof_val = att["skill"].get("proficiency", 0)
            prof_info = f"{prof_val}/5"
        issued = att.get("issued", "")
        if issued and "T" in issued:
            issued = issued.split("T")[0]

        table.add_row(type_short, subject_name, skill_info, prof_info, attestor_name, issued)

    console.print(table)


# --- Entry point for typer ---

def _cli():
    app()


if __name__ == "__main__":
    _cli()
