# Lead-Gen Prospecting Agent — Design (not implemented)

Status: **design only**. Nothing in this repo prospects, collects contacts, or sends outreach yet.
This document describes how a future agent would find businesses that match a brand's
Ideal Customer Profile (ICP) and record their **publicly listed business** contact details,
with the source of every field, while staying compliant with India's DPDP Act 2023,
the US CAN-SPAM Act and the EU/UK GDPR.

> Not legal advice. Before launch, have counsel review the lawful-basis assessment (LIA),
> the privacy notice, and the retention schedule for each market you target.

## 1. Goals and non-goals

**Goals**
- Turn a Brand Profile (`data/brands/<id>/profile.json`: services, personas, location, target market)
  into an ICP, then into a ranked list of matching **businesses** (organisations, not consumers).
- Collect only **business** contact points the business itself publishes: generic role inboxes
  (info@, sales@, hello@), main phone number, website contact form URL, and official social pages.
- Record provenance for every field (source URL, retrieval time, method) so each fact is auditable
  and can be refreshed or deleted.
- Make consent, opt-out and suppression first-class. No outreach to anyone who has opted out, ever.

**Non-goals**
- No personal or consumer data: no home addresses, personal mobiles, personal Gmail/Yahoo addresses,
  dates of birth, or profiles of individuals as private persons.
- No guessing or pattern-generating emails (e.g. `firstname.lastname@`), and no buying or importing third-party lists without documented provenance.
- No scraping behind logins, paywalls, CAPTCHAs, or anything robots.txt or the site's terms disallow.
- No automated sending in v1. The agent produces a reviewed list and drafts. A human approves every send.

## 2. Architecture

```
Brand Profile ──► ICP Builder ──► Discovery ──► Enrichment ──► Compliance Gate ──► Prospect Store ──► Review UI ──► (human) Outreach
                                     │              │                 │                                     │
                                     └─ Sources ────┘          Suppression list ◄──── opt-outs / bounces ◄──┘
```

| Component | Responsibility |
|---|---|
| `icp.py` | Derive ICP from the profile: industries/categories, geography (city/radius), size signals, must-have/must-not keywords. Editable in the UI. |
| `discovery.py` | Find candidate businesses through sanctioned sources (§3). Output: name, website, category, location, source. |
| `enrich.py` | Visit the business's **own** website (reuse `brand_research.crawl_site`: robots.txt, same domain, capped pages, crawl-delay) and extract published business contacts with provenance. |
| `compliance.py` | Classify each contact (business/role vs personal), apply jurisdiction rules, check suppression, assign lawful basis, and block anything that fails. |
| `store.py` | `data/leads/<brand_id>/prospects.jsonl` plus SQLite index; append-only audit log. |
| UI: "Prospects" page | Review, score, approve/reject, export (CSV with provenance columns), and opt-out handling. |

All components take injectable `fetch`/`llm`/`clock`, the same pattern as `brand_research.py`, so tests run with no network.

## 3. Discovery sources (in priority order)

1. **Official APIs under their terms:** Google Places API (New) Text Search by category plus location
   (the same key as Brand Setup, `GOOGLE_PLACES_API_KEY`), and OpenStreetMap Overpass for categories and areas.
   Respect each API's quotas, attribution and caching limits (Places content may not be cached beyond what the ToS allow, so store `place_id` and refresh).
2. **Public business registries and directories that permit reuse:** for example MCA/GST lookups in India, or chambers of commerce. Use them only where the terms allow automated access.
3. **The business's own website**, as linked from 1 or 2.

Sources excluded: LinkedIn and other platforms whose terms prohibit scraping; any people-search sites.

## 4. Data model

```json
{
  "prospect_id": "uuid",
  "brand_id": "acme-dental",
  "business": {"name": "", "category": "", "website": "", "city": "", "country": "IN", "place_id": ""},
  "contacts": [
    {"type": "email|phone|form|social", "value": "info@example.in",
     "classification": "role_business|named_business|personal",
     "source_url": "https://example.in/contact", "source_method": "website_crawl|places_api|registry",
     "retrieved_at": "2026-10-12T10:00:00+05:30", "robots_allowed": true}
  ],
  "icp_score": 0.82, "icp_reasons": ["category match: dental lab", "within 15 km"],
  "jurisdiction": "IN|US|EU|UK|OTHER",
  "lawful_basis": "legitimate_interest_b2b|consent|none",
  "consent": {"status": "none|granted|withdrawn", "captured_at": null, "method": null, "evidence": null},
  "status": "new|approved|rejected|contacted|opted_out|bounced",
  "retention_until": "2027-04-12",
  "audit": [{"at": "", "actor": "agent|user:<id>", "event": "created|approved|contacted|opt_out|deleted", "detail": ""}]
}
```

The **suppression list** (`data/leads/suppression.jsonl`) is global across brands. It stores normalised email, domain-level entries, phone numbers in E.164, and a SHA-256 hash variant so that deleted prospects stay suppressed without keeping their raw data. Each entry has a reason (`opt_out|bounce|complaint|legal_request|manual`) and a timestamp. Entries never expire.

## 5. Compliance controls

### 5.1 Common to every market
- **Data minimisation:** collect only fields listed in §4. Contacts classified `personal` are discarded at the gate and never stored.
- **Provenance:** a contact without `source_url` + `retrieved_at` cannot be saved.
- **Suppression first:** check before storing, before approving, and again immediately before any send.
- **Opt-out:** every message carries a one-click unsubscribe link (RFC 8058 `List-Unsubscribe` + `List-Unsubscribe-Post`) and a reply-STOP path. Opt-outs are processed automatically and at once (well within CAN-SPAM's 10 business days), and propagate to every brand.
- **Rights requests:** access, correction, erasure and objection. On erasure, delete the prospect and keep only the hashed suppression entry. Log the request and its completion.
- **Retention:** prospects never contacted are deleted after 6 months; contacted but unresponsive after 12 months (configurable per market).
- **Security:** files live under `data/` (gitignored). Encrypt at rest when stored off the box. Access only through the relay token. Exports are watermarked with brand and date.
- **Records:** append-only audit log of creation, source, approvals, sends and opt-outs (GDPR Art. 30-style record of processing).

### 5.2 India: Digital Personal Data Protection Act 2023 (+ DPDP Rules)
- DPDP applies to personal data of individuals; a named employee's work email is personal data.
  Role inboxes (info@) reduce but do not remove exposure. Treat any named contact as personal data.
- The DPDP lawful bases are consent or specified "legitimate uses". Cold marketing does not fit a legitimate use, so **v1 policy for India:** store only role/business contacts and **do not send marketing email to named individuals without consent**. The first contact goes through the business's published contact form or role inbox, with a consent request.
- Consent must be free, specific, informed and unambiguous, with an itemised notice. It must be as easy to withdraw as to give. Record `consent.evidence`.
- Telemarketing/SMS: check the TRAI DND / NCPR preference registry before any call or SMS, and use DLT-registered templates.
- Breach: notify the Data Protection Board and affected principals as the Rules require. Keep an incident runbook.

### 5.3 United States: CAN-SPAM
- Opt-out based: B2B cold email is permitted if every message has accurate From/To/Reply-To and routing, a non-deceptive subject, identification as an ad where applicable, the sender's valid physical postal address, and a clear opt-out that works for at least 30 days after sending.
- Honour opt-outs within 10 business days (we do it immediately). Never sell or transfer opted-out addresses.
- The brand is liable for any vendor sending on its behalf, so the same gate applies to any ESP integration.
- State laws (e.g. CCPA/CPRA for California residents' data) apply to stored data: privacy notice and deletion on request.

### 5.4 EU / UK: GDPR + ePrivacy (PECR in the UK)
- Lawful basis: **legitimate interests** for B2B prospecting, documented by a Legitimate Interests Assessment per brand/campaign (template in `docs/` when built).
- ePrivacy/PECR: in the UK, unsolicited email to **corporate subscribers** is permitted with opt-out; to sole traders and some partnerships it needs consent. EU member states differ (some require prior consent even for B2B, e.g. Germany). **v1 policy:** EU contacts are `consent`-only unless the country is on a reviewed allow-list.
- Art. 14 notice: when data is not collected from the person directly, provide a privacy notice (identity, purposes, source, rights) within one month or at first contact.
- Data subject rights within one month. Cross-border transfer safeguards if data leaves the EEA/UK.

### 5.5 Gate logic (pseudo-code)
```
for contact in prospect.contacts:
    if contact.classification == "personal": drop
    if suppressed(contact) or suppressed(domain): drop, status=opted_out
    if not contact.source_url or not contact.robots_allowed: drop
basis = policy[jurisdiction](contact.classification)   # may return "consent_required"
if basis == "consent_required" and consent.status != "granted": approvable = False (form/consent request only)
```

## 6. Rate limits and politeness

| Layer | Default | Notes |
|---|---|---|
| Website crawl | 1 request per crawl-delay (min 1 s, max 10 s), ≤ 10 pages per domain, 1 concurrent request per domain | Reuse `crawl_site`; obey robots.txt, treating a 403 on robots.txt as disallow-all; identify via `NEXUS_CRAWLER_UA`. |
| Global crawl | ≤ 200 domains/hour per brand | Token bucket in `store.py`. |
| Places API | ≤ 5 QPS, daily budget cap set in config | Exponential backoff on 429/5xx (`NEXUS_BACKOFF_BASE`). |
| Outreach (future) | ≤ 50 new contacts/day per sending domain, warm-up ramp, stop on bounce > 3% or complaint > 0.1% | SPF/DKIM/DMARC required before enabling send. |

## 7. UI and workflow
1. Brand Setup → "Prospects" tab shows the derived ICP. The owner edits and confirms it.
2. "Find prospects" runs discovery and enrichment as a background job, with progress in Logs.
3. Review table: business, score with reasons, contacts with source links, jurisdiction, and lawful-basis badge. Blocked items show why.
4. Approve → export CSV (provenance columns included) or hand off to a future outreach module. Every send needs explicit human approval.
5. "Suppression" panel: add or import opt-outs. Entries cannot be removed except by an admin, with an audit entry.

## 8. Testing plan
- Unit: ICP scoring, contact classification (role vs named vs personal), jurisdiction rules table, suppression matching (case, plus-addressing, domain-level, hashed), retention expiry.
- Integration with mocked fetch: robots disallow honoured, crawl-delay respected, no off-domain requests, provenance required.
- Property test: no record ever leaves the gate with `classification=personal` or a suppressed value.
- Compliance regression fixtures per market (IN/US/EU/UK) asserting approvability.

## 9. Open questions
- Which ESP (if any) for outreach, and should it hold the suppression list as well?
- Per-country EU allow-list: needs a legal review before enabling any B2B legitimate-interest sends.
- Do we need a DPO/Grievance Officer contact for India (Significant Data Fiduciary thresholds are unlikely to apply at this scale)?
