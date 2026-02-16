"""Kredo CLI — Typer app for identity, attestation, and trust management."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

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


# --- Entry point for typer ---

def _cli():
    app()


if __name__ == "__main__":
    _cli()
