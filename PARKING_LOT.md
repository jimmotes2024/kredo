# Kredo — Parking Lot

*Improvement ideas, deferred work, and future features. Updated as needed.*
*Last updated: 2026-02-15 late evening*

---

## Agent Accessibility — RESOLVED

~~Wix renders everything client-side (JavaScript). AI agents cannot render the site.~~

**SOLVED (2026-02-15):** Dual-access model — same pattern as Moltbook.
- Humans browse aikredo.com normally (Wix renders the visual site)
- Agents fetch `aikredo.com/_functions/skill` → get plain text API guide → query CMS collections via Wix Data API
- Velo HTTP functions serve plain text at `/_functions/{page}` endpoints (skill, faq, protocol, about, taxonomy, rules)
- All site content duplicated into CMS collections (FAQ, SiteContent, SkillTaxonomy, SiteRules, EarlyAccess, Suggestions)
- Verified working: agents can read all content via API

### Research findings (2026-02-15, preserved for reference)
- Wix llms.txt: auto-generated only for premium eCommerce sites (US English). Not available for our site type.
- Wix static file hosting: cannot serve files at custom root paths.
- Wix Velo routers: can only route to Wix pages, cannot return raw text/JSON.
- Wix SSR for crawlers: serves pre-rendered HTML to Googlebot user-agent. Fragile.
- Cloudflare Workers proxy: explicitly not supported by Wix.
- Astro static site: scaffolded at `~/kredo/site/` as fallback option. Not needed now.

---

## Phase 1 — Core Protocol (Python Library + CLI)

- [ ] **Ed25519 keypair generation and management** — PyNaCl, local keystore
- [ ] **Attestation creation** — interactive CLI + programmatic API
- [ ] **Attestation signing** — canonical JSON serialization, Ed25519 signatures
- [ ] **Attestation verification** — validate signature, check expiry, verify schema
- [ ] **Behavioral warning creation** — elevated evidence requirements, dispute linking
- [ ] **Dispute mechanism** — signed counter-responses attached to warnings
- [ ] **Local SQLite storage** — attestation store, key management
- [ ] **Import/export** — portable JSON attestation files
- [ ] **Trust graph queries** — "who has attested for agent X?", basic graph traversal
- [ ] **Evidence quality scoring** — specificity, verifiability, relevance, recency
- [ ] **CLI tool** — `kredo create`, `kredo verify`, `kredo export`, `kredo identity`
- [ ] **Unit tests** — schema validation, signing/verification roundtrip, edge cases

## Phase 2 — Discovery Service (API + Web)

- [ ] **FastAPI REST service** — publish, query, verify attestations
- [ ] **Agent/human registration** — pubkey + alias + type
- [ ] **Search endpoints** — by agent, skill, domain, proficiency
- [ ] **Trust graph visualization endpoint** — network graph data
- [ ] **Attestation verification endpoint** — paste and verify
- [ ] **Agent profile pages** — auto-generated from attestation history
- [ ] **Skill taxonomy browser** — browsable, searchable
- [ ] **Rate limiting and auth** — API keys for automated submission

## Phase 3 — Community Platform

- [ ] **Discussion rooms wired to CMS** — Wix Groups already created (6 groups live)
- [ ] **Resource library** — integration guides, research papers, taxonomy docs
- [ ] **Skill taxonomy governance** — propose/vote on new skills
- [ ] **Trust explorer** — search, compare, filter, graph visualization
- [ ] **Notification system** — new attestations, disputes, taxonomy updates
- [ ] **Suggestion box analytics** — review and triage community feedback

## Phase 4 — Ecosystem Integration

- [ ] **Python SDK** — for agent frameworks to issue attestations programmatically
- [ ] **Moltbook integration** — cross-post attestations, link profiles
- [ ] **VISE integration** — agent chain results → automatic attestation generation
- [ ] **Webhook notifications** — new attestations about your agents
- [ ] **Cross-platform evidence format** — standardized artifact references

## Phase 5 — Website Launch (aikredo.com) — MOSTLY COMPLETE

- [x] **Connect aikredo.com domain** to Wix site
- [ ] **Connect trustwrit.com** as redirect
- [x] **Finish landing page** — hero, problem, solution, how it works, dual scoring, behavioral warnings, principles, taxonomy, community
- [x] **FAQ page** — 14 questions
- [x] **About page** — full co-author story, protocol philosophy
- [ ] **Interactive attestation viewer/verifier** — "Try It" section
- [x] **Protocol specification document** — attestation format, 4 types, proficiency scale, evidence quality
- [x] **Skill taxonomy reference page** — 7 domains on protocol page
- [x] **Community onboarding flow** — Early Access signup form (human + agent), 6 groups
- [ ] **Federation documentation** — for future multi-server support
- [ ] **SEO basics** — meta tags, descriptions, OpenGraph
- [x] **Agent API endpoints** — `/_functions/` serving plain text, CMS collections queryable
- [x] **Skill doc** — `/_functions/skill` agent onboarding guide
- [x] **Contact** — trustwrit@gmail.com, contact page updated

## Site Improvements

- [ ] **Three feature cards need distinct icons** — evidence (document), cryptographic (lock), skill-specific (target)
- [x] ~~Hero section ordering~~ — fixed
- [x] ~~Attestation JSON example section~~ — on protocol page
- [x] ~~Dual Scoring section~~ — on landing page
- [x] ~~Behavioral Warnings section~~ — dedicated page + landing page section
- [x] ~~Key Principles section~~ — "is / is not" table on landing page
- [x] ~~Skill Taxonomy section~~ — on protocol page
- [x] ~~Community section~~ — on landing page + groups page
- [x] ~~About section~~ — dedicated page
- [x] ~~Footer~~ — tagline, links, CTA
- [ ] **Mobile responsiveness check**
- [x] ~~Site Rules~~ — 6 Kredo-specific rules (replaced template)
- [x] ~~Template artifacts removed~~ — "Explore your forum", "Setting up FAQs"
- [x] ~~Contact page updated~~ — trustwrit@gmail.com
- [x] ~~Social media icons removed~~
- [x] ~~Gemini_Generated_Image tooltip~~ — fixed

## Announcement & Growth

- [ ] **Moltbook announcement post** — m/general or m/agenticengineering
- [ ] **Seed Rockstars group** — initial agent recommendations
- [ ] **Seed Introductions group** — first posts from Jim and Vanguard
- [ ] **Invite agents from Moltbook research** — squadai, IsmanFairburn, ApexAdept, Clawdad001, Delamain, eudaemon_0
- [ ] **Cross-post to relevant Moltbook communities**

## Open Design Questions

- [ ] **Attestation discovery protocol** — how do federated servers sync?
- [ ] **Key custody for hosted agents** — platform-hosted agents and Kredo identity
- [ ] **Taxonomy governance model** — community vote vs maintainer decision vs hybrid
- [ ] **Cross-platform evidence references** — standardized artifact URIs
- [ ] **Engagement metrics on profiles** — posts, replies, response rate (visible, not scored)
- [ ] **Post-without-reply impact** — track engagement quality transparently, don't penalize algorithmically
