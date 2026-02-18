"""Dead-simple attestation interface. One call. Three args. Done.

    from langchain_kredo import attest

    attest("jim", "incident-triage", "Triaged 3 incidents correctly in SOC exercise")

Subject by name or pubkey. Skill by name (auto-resolves domain).
Signs with KREDO_PRIVATE_KEY env var. Submits to the Discovery API. Returns attestation ID.
"""

from __future__ import annotations

from typing import Optional

from kredo.taxonomy import get_domains, get_skills

from langchain_kredo._client import KredoSigningClient


def _resolve_skill(skill: str) -> tuple[str, str]:
    """Resolve a skill string to (domain, specific).

    Accepts:
        "security-operations/incident-triage"  → exact
        "incident-triage"                      → reverse lookup
    """
    if "/" in skill:
        domain, specific = skill.split("/", 1)
        return domain, specific

    # Reverse lookup: find which domain owns this skill
    matches = []
    for domain in get_domains():
        if skill in get_skills(domain):
            matches.append((domain, skill))

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        options = [f"{d}/{s}" for d, s in matches]
        raise ValueError(
            f"Skill '{skill}' exists in multiple domains: {options}. "
            f"Use 'domain/skill' format."
        )

    # Check if it's a domain name (common mistake)
    if skill in get_domains():
        skills = get_skills(skill)
        raise ValueError(
            f"'{skill}' is a domain, not a skill. "
            f"Pick a specific skill: {skills[:5]}{'...' if len(skills) > 5 else ''}"
        )

    raise ValueError(
        f"Unknown skill '{skill}'. "
        f"Use 'domain/skill' format or run: kredo taxonomy"
    )


def _resolve_subject(
    client: KredoSigningClient, subject: str,
) -> tuple[str, str]:
    """Resolve subject to (pubkey, name). Accepts pubkey or name.

    Raises ValueError on ambiguous names (multiple matches) or network errors.
    """
    if subject.startswith("ed25519:"):
        try:
            profile = client.get_profile(subject)
            return subject, profile.get("name", "")
        except Exception:
            # Pubkey was explicit — proceed even if profile lookup fails
            return subject, ""

    # Search by name — uses public list_agents() method
    agents = client.list_agents(limit=200)
    matches = [
        a for a in agents
        if a.get("name", "").lower() == subject.lower()
    ]

    if len(matches) == 1:
        return matches[0]["pubkey"], matches[0]["name"]
    elif len(matches) > 1:
        pubkeys = [m["pubkey"] for m in matches]
        raise ValueError(
            f"Ambiguous: {len(matches)} agents named '{subject}'. "
            f"Use their pubkey directly: {pubkeys}"
        )

    raise ValueError(
        f"Agent '{subject}' not found on the network. "
        f"Register them first, or use their pubkey: ed25519:..."
    )


def attest(
    subject: str,
    skill: str,
    evidence: str,
    *,
    proficiency: int = 3,
    signer: Optional[str] = None,
) -> str:
    """One-liner attestation. Sign, submit, done.

    Args:
        subject: Agent name ("jim") or pubkey ("ed25519:abc...").
        skill: Skill name ("incident-triage") or full path
               ("security-operations/incident-triage").
        evidence: What happened. Free text or URL.
        proficiency: 1 (novice) to 5 (authority). Default: 3 (competent).
        signer: Hex seed for signing key. Default: KREDO_PRIVATE_KEY env var.

    Returns:
        Attestation ID string.

    Examples:
        >>> attest("jim", "incident-triage", "Triaged 3 incidents correctly")
        'att-abc123...'

        >>> attest("ed25519:ab...", "code-review", "https://github.com/pr/47")
        'att-def456...'
    """
    client = KredoSigningClient(signing_key=signer)

    pubkey, name = _resolve_subject(client, subject)
    domain, specific = _resolve_skill(skill)

    # Detect URL artifacts
    artifacts = []
    if evidence.startswith(("http://", "https://")):
        artifacts = [evidence]

    result = client.attest_skill(
        subject_pubkey=pubkey,
        domain=domain,
        skill=specific,
        proficiency=proficiency,
        context=evidence,
        artifacts=artifacts,
        subject_name=name,
    )

    return result.get("id", result.get("attestation_id", ""))
