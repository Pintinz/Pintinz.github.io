"""Builds the public opportunity pages of pintinz.github.io from the live CareerOS API.

    python tools/build_listings.py                 # writes into the site folder this file lives in
    python tools/build_listings.py --out DIR       # writes into DIR (a copy of the site)
    python tools/build_listings.py --cache FILE    # reuse/save the fetched data (local design work)

Writes, and fully replaces each run:
- jobs/index.html, jobs/in-<country>.html, jobs/<slug>.html
- companies/index.html, companies/<slug>.html
- scholarships/index.html, scholarships/<slug>.html
- sitemap.xml (static pages + guides + every generated page)

Only live listings the API publicly lists are written; a listing that closes disappears on the next
run. Job pages carry schema.org JobPosting markup for Google for Jobs, built only from real fields
(no invented salary, dates or locations). Every page's extra content (company profile, related roles,
preparation links, counts) comes from the same live data, so no two pages are copies.

Standard library only, so it runs in GitHub Actions with no install step. Styles: pages.css.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

API = "https://p01--careeros-api--szyhwlzghlw2.code.run/api/v1"
SITE = "https://pintinz.github.io"
PLAY = "https://play.google.com/store/apps/details?id=com.careeros.careeros"
PACKAGE = "com.careeros.careeros"
UA = "CareerOS-site-builder/1.0"
MAX_JOBS = 3000
MAX_SCHOLARSHIPS = 1000
FOCUS_COUNTRY = "Nigeria"  # listed first in country navigation (primary market)

# Sources spell some countries differently; one hub page per country, keyed by slug.
COUNTRY_NAMES = {"cote-d-ivoire": "Côte d'Ivoire", "ivory-coast": "Côte d'Ivoire", "czech-republic": "Czechia", "czechia": "Czechia"}
# Employers' own test postings ("test - ne pas postuler") are not real openings.
TEST_POSTING = re.compile(r"ne\s+pas\s+postuler|do\s+not\s+apply|nicht\s+bewerben|no\s+postular", re.I)

EMPLOYMENT_TYPES = {
    "FULL_TIME": "FULL_TIME", "PART_TIME": "PART_TIME", "CONTRACT": "CONTRACTOR", "TEMPORARY": "TEMPORARY",
    "INTERNSHIP": "INTERN", "VOLUNTEER": "VOLUNTEER", "GRADUATE_PROGRAM": "FULL_TIME", "APPRENTICESHIP": "INTERN",
}
EMPLOYMENT_LABELS = {
    "FULL_TIME": "Full-time", "PART_TIME": "Part-time", "CONTRACT": "Contract", "TEMPORARY": "Temporary",
    "INTERNSHIP": "Internship", "VOLUNTEER": "Volunteer", "GRADUATE_PROGRAM": "Graduate programme", "APPRENTICESHIP": "Apprenticeship",
}
WORK_MODE_LABELS = {"ONSITE": "On-site", "REMOTE": "Remote", "HYBRID": "Hybrid"}
DEGREE_LABELS = {"UNDERGRADUATE": "Undergraduate", "MASTERS": "Master's", "PHD": "PhD", "OTHER": "Other"}
LEVEL_LABELS = {"ENTRY": "Entry level", "JUNIOR": "Junior", "MID": "Mid level", "SENIOR": "Senior", "LEAD": "Lead", "EXECUTIVE": "Executive"}
FUNDING_LABELS = {"FULL": "Fully funded", "PARTIAL": "Partially funded", "TUITION_ONLY": "Tuition only", "STIPEND_ONLY": "Stipend only"}
QUALIFICATION_LABELS = {
    "NONE": "No formal qualification", "SSCE": "SSCE / O-level", "OND_NCE": "OND / NCE", "HND": "HND",
    "BACHELORS": "Bachelor's degree", "MASTERS": "Master's degree", "DOCTORATE": "Doctorate",
}
SECTOR_LABELS = {
    "TECHNOLOGY": "Technology", "OIL_GAS_ENERGY": "Oil, gas and energy", "SALES_MARKETING": "Sales and marketing",
    "ENGINEERING": "Engineering", "ACCOUNTING_FINANCE": "Accounting and finance", "LOGISTICS_TRANSPORT": "Logistics and transport",
    "HR_ADMIN": "HR and administration", "BANKING_INSURANCE": "Banking and insurance", "HEALTHCARE": "Healthcare",
    "CUSTOMER_SERVICE": "Customer service", "LEGAL": "Legal", "NGO_DEVELOPMENT": "NGO and development",
    "SKILLED_TRADES": "Skilled trades", "MEDIA_CREATIVE": "Media and creative", "CONSTRUCTION_REAL_ESTATE": "Construction and real estate",
    "RESEARCH_SCIENCE": "Research and science", "MANUFACTURING_FMCG": "Manufacturing and FMCG", "CONSULTING": "Consulting",
    "EDUCATION": "Education", "TELECOMS": "Telecoms", "SECURITY": "Security", "HOSPITALITY_TOURISM": "Hospitality and tourism",
    "RETAIL": "Retail", "GOVERNMENT_PUBLIC": "Government and public sector", "AGRICULTURE": "Agriculture",
}
# Monogram tile colours for companies without a logo: deep, brand-compatible hues.
MONOGRAM_HUES = ["#1D5FD1", "#0E7490", "#4338CA", "#047857", "#9A3412", "#7E22CE", "#0F766E", "#B45309"]

ROOT = Path(__file__).resolve().parent.parent
TODAY = datetime.now(timezone.utc).date()
GOOGLE_PLAY_BADGE = "https://play.google.com/intl/en_us/badges/static/images/badges/en_badge_web_generic.png"


# --------------------------------------------------------------------------------------- fetching

def _get(path: str, params: dict | None = None, *, tries: int = 6):
    """GET with backoff (~2.5 min in total) so a brief API restart doesn't fail the daily build."""
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.load(resp)
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def _all(path: str, limit: int) -> list[dict]:
    out: list[dict] = []
    page_no = 1
    while len(out) < limit:
        data = _get(path, {"page": page_no, "page_size": 100, "sort": "newest"})
        items = data.get("items") or []
        out.extend(items)
        if not items or len(out) >= data.get("total", 0):
            break
        page_no += 1
    return out[:limit]


def _details(kind: str, cards: list[dict]) -> list[dict]:
    """Detail records for live cards, 6 requests at a time, in the cards' (newest-first) order."""
    from concurrent.futures import ThreadPoolExecutor

    live = [c for c in cards if not c.get("is_demo") and c.get("availability", "ACTIVE") == "ACTIVE"]

    def one(card: dict) -> dict | None:
        try:
            return _get(f"/{kind}/{urllib.parse.quote(card['slug'])}", tries=2)  # skip, don't stall, if one fails
        except RuntimeError as exc:
            print(f"skip {kind} {card.get('slug')}: {exc}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        return [d for d in pool.map(one, live) if d]


def fetch() -> tuple[list[dict], list[dict]]:
    return _details("jobs", _all("/jobs", MAX_JOBS)), _details("scholarships", _all("/scholarships", MAX_SCHOLARSHIPS))


# --------------------------------------------------------------------------------------- helpers

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()  # Côte -> Cote, Nestlé -> Nestle
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "other"


def canonical_country(value: str) -> str:
    return COUNTRY_NAMES.get(slugify(value), value)


def safe_slug(value: str) -> str:
    """Filesystem-safe page name from the API slug."""
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")[:150] or "listing"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def nice_date(value: str | None, *, short: bool = False) -> str | None:
    dt = parse_dt(value)
    if not dt:
        return None
    return f"{dt.day} {dt.strftime('%b %Y' if short else '%B %Y')}"


def days_left(value: str | None) -> int | None:
    dt = parse_dt(value)
    return (dt.date() - TODAY).days if dt else None


def countdown(value: str | None) -> tuple[str, str]:
    """('Closes in 12 days', tone) from a real deadline; tone is soon/later/none."""
    left = days_left(value)
    if left is None:
        return "No closing date listed", "none"
    if left <= 0:
        return "Closes today", "soon"
    if left == 1:
        return "Closes tomorrow", "soon"
    return f"Closes in {left} days", "soon" if left <= 7 else "later"


def text_to_html(text: str | None) -> str:
    """Plain listing text → paragraphs and bullet lists (everything escaped)."""
    if not text:
        return ""
    if re.search(r"<(p|ul|li|br|strong|h\d)\b", text, re.I):
        text = re.sub(r"<\s*br\s*/?>|</p>|</li>|</h\d>", "\n", text, flags=re.I)
        text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.I)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    out, bullets = [], []

    def flush():
        if bullets:
            out.append("<ul>" + "".join(f"<li>{esc(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        m = re.match(r"^([•\-\*·▪●]|\d+[.)])\s+(.*)$", line)
        if m:
            bullets.append(m.group(2))
        elif len(line) < 70 and line.endswith(":") or (len(line) < 60 and line.isupper()):
            flush()
            out.append(f"<h3>{esc(line.rstrip(':').capitalize() if line.isupper() else line.rstrip(':'))}</h3>")
        else:
            flush()
            out.append(f"<p>{esc(line)}</p>")
    flush()
    return "\n".join(out)


def list_html(items: list[str] | None) -> str:
    items = [i for i in (items or []) if i and str(i).strip()]
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def plain(text: str | None, limit: int = 155) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def app_link(kind: str, slug: str) -> str:
    """Opens the listing in the app when installed, otherwise Google Play (Android intent URL)."""
    fallback = urllib.parse.quote(PLAY, safe="")
    return f"intent://app/{kind}/{urllib.parse.quote(slug)}#Intent;scheme=careeros;package={PACKAGE};S.browser_fallback_url={fallback};end"


def company_of(job: dict) -> dict:
    return job.get("company") or {}


def company_slug(company: dict) -> str:
    return safe_slug(company.get("slug") or slugify(company.get("name") or "company"))


def logo_tile(company: dict, size: str = "md", up: str = "../") -> str:
    """The company's real logo, or a monogram tile coloured from its name."""
    name = company.get("name") or "?"
    logo = company.get("logo_override_url") or company.get("logo_url")
    if logo:
        return f'<span class="logo logo-{size}"><img src="{esc(logo)}" alt="" loading="lazy" decoding="async"></span>'
    hue = MONOGRAM_HUES[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(MONOGRAM_HUES)]
    letters = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", name)[:2]).upper() or "?"
    return f'<span class="logo logo-{size} logo-mono" style="--mono:{hue}" aria-hidden="true">{esc(letters)}</span>'


def job_location(job: dict) -> str:
    place = (job.get("city") or job.get("location") or "").strip()
    country = (job.get("country") or "").strip()
    if not place:
        return country
    if not country or country.lower() in place.lower():
        return place
    return f"{place}, {country}"


def is_early_career(job: dict) -> bool:
    return job.get("employment_type") in ("GRADUATE_PROGRAM", "INTERNSHIP", "APPRENTICESHIP") or job.get("experience_level") in ("ENTRY", "JUNIOR")


# --------------------------------------------------------------------------------------- layout

FIELD_SVG = (
    '<svg class="band-field" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
    '<path d="M62 0 C 52 20, 70 42, 60 64 C 54 80, 60 92, 66 100 L 100 100 L 100 0 Z"/>'
    '<path class="band-field-2" d="M76 0 C 68 22, 84 42, 76 64 C 71 80, 76 92, 82 100 L 100 100 L 100 0 Z"/></svg>'
)


def band(inner: str, *, ticket: bool = False) -> str:
    """Navy page header; `ticket` leaves room for the overlapping application ticket."""
    return f'<section class="band{" band-ticket" if ticket else ""}">{FIELD_SVG}<div class="wrap band-inner">{inner}</div></section>'


def page(*, title: str, description: str, path: str, body: str, ld: list[dict] | None = None, depth: int = 1,
         og_type: str = "website") -> str:
    up = "../" * depth
    ld_html = "".join(
        f'<script type="application/ld+json">{json.dumps(item, ensure_ascii=False)}</script>\n' for item in (ld or [])
    )
    year = datetime.now(timezone.utc).year
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{SITE}/{path}">
<meta name="theme-color" content="#0B1F33">
<link rel="icon" href="{up}assets/careeros-symbol.png">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="CareerOS">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{SITE}/{path}">
<meta property="og:image" content="{SITE}/assets/og-image.png">
<meta name="twitter:card" content="summary_large_image">
{ld_html}<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300..800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{up}pages.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <div class="wrap top-inner">
    <a class="top-brand" href="{up}" aria-label="CareerOS home"><img src="{up}assets/careeros-wordmark.png" alt="CareerOS" width="164" height="28"></a>
    <nav class="top-nav" aria-label="Main">
      <a href="{up}jobs/">Jobs</a>
      <a href="{up}companies/">Companies</a>
      <a href="{up}scholarships/">Scholarships</a>
      <a href="{up}guides/">Guides</a>
    </nav>
    <a class="top-cta" href="{PLAY}">Get the app</a>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="foot">
  <div class="wrap foot-grid">
    <div class="foot-brand">
      <img src="{up}assets/careeros-wordmark-on-dark.png" alt="CareerOS" width="164" height="28" loading="lazy">
      <p>Jobs, scholarships and the preparation to land them. Free on Android.</p>
      <a href="{PLAY}"><img class="foot-badge" src="{GOOGLE_PLAY_BADGE}" alt="Get it on Google Play" width="646" height="250" loading="lazy"></a>
    </div>
    <nav aria-label="Opportunities"><h2>Opportunities</h2>
      <a href="{up}jobs/in-nigeria.html">Jobs in Nigeria</a><a href="{up}jobs/">All jobs by country</a>
      <a href="{up}companies/">Companies hiring</a><a href="{up}scholarships/">Scholarships</a></nav>
    <nav aria-label="Guides"><h2>Guides</h2>
      <a href="{up}guides/how-to-pass-aptitude-tests.html">Aptitude tests</a><a href="{up}guides/graduate-trainee-interview-questions.html">Interview questions</a>
      <a href="{up}guides/ats-friendly-cv.html">ATS-friendly CV</a><a href="{up}guides/scholarship-application-tips.html">Scholarship applications</a></nav>
    <nav aria-label="CareerOS"><h2>CareerOS</h2>
      <a href="{up}">About the app</a><a href="{up}privacy.html">Privacy</a><a href="{up}terms.html">Terms</a><a href="mailto:careeros.info@gmail.com">Contact</a><a href="#" class="consent-link" data-consent-settings hidden>Analytics choices</a></nav>
  </div>
  <div class="wrap foot-base"><span>&copy; {year} CareerOS</span><span>CareerOS is not the employer or scholarship provider for listings shown here.</span></div>
</footer>
<script src="{up}site.js" defer></script>
</body>
</html>
"""


def breadcrumbs(items: list[tuple[str, str]]) -> tuple[str, dict]:
    """[(name, site path)] → visible trail + BreadcrumbList markup."""
    trail = "".join(
        f'<li><a href="{SITE}/{p}">{esc(n)}</a></li>' if i < len(items) - 1 else f'<li aria-current="page">{esc(n)}</li>'
        for i, (n, p) in enumerate(items)
    )
    ld = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": n, "item": f"{SITE}/{p}"} for i, (n, p) in enumerate(items)
        ],
    }
    return f'<nav aria-label="Breadcrumb"><ol class="crumbs">{trail}</ol></nav>', ld


def app_card(kind: str, slug: str, heading: str, text: str, up: str = "../") -> str:
    return f"""<aside class="side-card app-card">
  <img class="app-mark" src="{up}assets/careeros-symbol.png" alt="" width="40" height="40">
  <h2>{esc(heading)}</h2>
  <p>{esc(text)}</p>
  <a class="btn btn-ghost" href="{app_link(kind, slug)}" data-android-only>Open in the app</a>
  <a class="play" href="{PLAY}"><img src="{GOOGLE_PLAY_BADGE}" alt="Get it on Google Play" width="646" height="250" loading="lazy"></a>
</aside>"""


def row(href: str, company: dict, title: str, meta: list[str | None], deadline: str | None, up: str = "../") -> str:
    """One opportunity in a list: logo, title, meta line, and a deadline chip when there is one."""
    label, tone = countdown(deadline)
    chip = f'<span class="chip chip-{tone}">{esc(label)}</span>' if deadline else ""
    return (
        f'<li><a class="row" href="{href}">{logo_tile(company, "sm", up)}'
        f'<span class="row-text"><span class="row-title">{esc(title)}</span>'
        f'<span class="row-meta">{esc(" · ".join(m for m in meta if m))}</span></span>{chip}</a></li>'
    )


def job_row(job: dict, prefix: str = "", up: str = "../") -> str:
    return row(f"{prefix}{safe_slug(job['slug'])}.html", company_of(job), job["title"],
               [company_of(job).get("name"), job_location(job), EMPLOYMENT_LABELS.get(job.get("employment_type"))],
               job.get("application_deadline"), up)


# --------------------------------------------------------------------------------------- jobs

def job_posting_ld(job: dict, url: str) -> dict | None:
    posted = parse_dt(job.get("source_published_at") or job.get("published_at"))
    description = text_to_html(job.get("description")) or list_html(job.get("responsibilities"))
    company = company_of(job)
    if not (posted and description and company.get("name")):
        return None  # Google requires these; never invent them
    ld = {
        "@context": "https://schema.org/", "@type": "JobPosting",
        "title": job["title"], "description": description, "datePosted": posted.date().isoformat(),
        "hiringOrganization": {"@type": "Organization", "name": company["name"]},
        "identifier": {"@type": "PropertyValue", "name": company["name"], "value": job["id"]},
        "url": url, "directApply": False,
    }
    if job.get("employment_type") in EMPLOYMENT_TYPES:
        ld["employmentType"] = EMPLOYMENT_TYPES[job["employment_type"]]
    if company.get("website_url"):
        ld["hiringOrganization"]["sameAs"] = company["website_url"]
    if company.get("logo_url"):
        ld["hiringOrganization"]["logo"] = company["logo_url"]
    deadline = parse_dt(job.get("application_deadline"))
    if deadline:
        ld["validThrough"] = deadline.isoformat()
    if job.get("work_mode") == "REMOTE":
        ld["jobLocationType"] = "TELECOMMUTE"
        ld["applicantLocationRequirements"] = {"@type": "Country", "name": job.get("country") or "Worldwide"}
    if job.get("work_mode") != "REMOTE" or job.get("city"):
        address = {"@type": "PostalAddress"}
        if job.get("city") or job.get("location"):
            address["addressLocality"] = job.get("city") or job.get("location")
        if job.get("state_or_region"):
            address["addressRegion"] = job["state_or_region"]
        if job.get("country"):
            address["addressCountry"] = job["country"]
        if len(address) > 1:
            ld["jobLocation"] = {"@type": "Place", "address": address}
    if job.get("salary_currency") and (job.get("salary_min") or job.get("salary_max")):
        value = {"@type": "QuantitativeValue", "unitText": (job.get("salary_period") or "YEAR").upper()}
        if job.get("salary_min"):
            value["minValue"] = job["salary_min"]
        if job.get("salary_max"):
            value["maxValue"] = job["salary_max"]
        ld["baseSalary"] = {"@type": "MonetaryAmount", "currency": job["salary_currency"], "value": value}
    if "jobLocation" not in ld and "jobLocationType" not in ld:
        return None
    return ld


def salary_text(job: dict) -> str | None:
    cur, lo, hi = job.get("salary_currency"), job.get("salary_min"), job.get("salary_max")
    if not cur or not (lo or hi):
        return None
    period = {"YEAR": "a year", "MONTH": "a month", "HOUR": "an hour", "WEEK": "a week", "DAY": "a day"}.get(
        (job.get("salary_period") or "").upper(), "")
    amount = f"{lo:,} – {hi:,}" if lo and hi and lo != hi else f"{(lo or hi):,}"
    return f"{cur} {amount} {period}".strip()


def prep_card(job: dict) -> str:
    """What to prepare for this kind of role, linking the guides (varies by role type)."""
    if is_early_career(job):
        intro = "Early-career and graduate roles often start with an online aptitude test, then a competency interview."
        links = [("how-to-pass-aptitude-tests", "How to pass aptitude tests"),
                 ("numerical-reasoning-tips", "Numerical reasoning: tips and examples"),
                 ("graduate-trainee-interview-questions", "15 common graduate interview questions")]
    else:
        intro = "Experienced hires are usually screened on the CV first, then interviewed on real examples of your work."
        links = [("ats-friendly-cv", "Tailor your CV to the job description"),
                 ("star-method-interview-answers", "Answer competency questions with STAR"),
                 ("graduate-trainee-interview-questions", "Common interview questions")]
    items = "".join(f'<li><a href="../guides/{s}.html">{esc(t)}</a></li>' for s, t in links)
    return f'<aside class="side-card"><h2>Prepare for this role</h2><p>{esc(intro)}</p><ul class="link-list">{items}</ul></aside>'


def related_jobs(job: dict, by_company: dict[str, list[dict]], by_country: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    company = company_slug(company_of(job))
    same_company = [j for j in by_company.get(company, []) if j["id"] != job["id"]][:4]
    taken = {j["id"] for j in same_company} | {job["id"]}
    pool = [j for j in by_country.get(job.get("country") or "", []) if j["id"] not in taken]
    similar = [j for j in pool if j.get("sector") and j.get("sector") == job.get("sector")][:4]
    if len(similar) < 4:
        similar += [j for j in pool if j not in similar][: 4 - len(similar)]
    return same_company, similar


def job_page(job: dict, by_company: dict[str, list[dict]], by_country: dict[str, list[dict]]) -> str:
    slug = safe_slug(job["slug"])
    path = f"jobs/{slug}.html"
    url = f"{SITE}/{path}"
    company = company_of(job)
    cname = company.get("name") or "Employer"
    cslug = company_slug(company)
    where = job_location(job)
    country = job.get("country")
    crumbs = [("CareerOS", ""), ("Jobs", "jobs/")]
    if country:
        crumbs.append((f"Jobs in {country}", f"jobs/in-{slugify(country)}.html"))
    crumbs.append((job["title"], path))
    crumb_html, crumb_ld = breadcrumbs(crumbs)

    label, tone = countdown(job.get("application_deadline"))
    deadline = nice_date(job.get("application_deadline"))
    posted = nice_date(job.get("source_published_at") or job.get("published_at"), short=True)
    apply_url = job.get("application_url") or job.get("source_url")
    kind = EMPLOYMENT_LABELS.get(job.get("employment_type")) or "Job"
    facts = [
        ("Location", where or None), ("Work mode", WORK_MODE_LABELS.get(job.get("work_mode"))),
        ("Job type", EMPLOYMENT_LABELS.get(job.get("employment_type"))), ("Level", LEVEL_LABELS.get(job.get("experience_level"))),
        ("Field", SECTOR_LABELS.get(job.get("sector"))), ("Qualification", QUALIFICATION_LABELS.get(job.get("minimum_qualification"))),
        ("Salary", salary_text(job)), ("Posted", posted),
    ]
    facts_html = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in facts if v)
    apply_btn = (f'<a class="btn btn-primary" href="{esc(apply_url)}" rel="nofollow noopener" target="_blank">Apply on {esc(cname)}\'s site</a>'
                 if apply_url else "")

    ticket = f"""<div class="ticket">
  <div class="ticket-main">
    <div class="ticket-org">{logo_tile(company, "lg")}<div><a class="ticket-company" href="../companies/{cslug}.html">{esc(cname)}</a>
      {f'<span class="ticket-industry">{esc(company.get("industry"))}</span>' if company.get("industry") else ''}</div></div>
    <dl class="facts">{facts_html}</dl>
  </div>
  <div class="ticket-stub">
    <span class="stub-label">Applications</span>
    <span class="stub-count stub-{tone}">{esc(label)}</span>
    {f'<span class="stub-date">{esc(deadline)}</span>' if deadline else '<span class="stub-date">Apply soon: roles can close early</span>'}
    {apply_btn}
  </div>
</div>"""

    sections = []
    if job.get("short_summary"):
        sections.append(f'<p class="lead">{esc(job["short_summary"])}</p>')
    if job.get("description"):
        sections.append(f"<h2>About the role</h2>\n{text_to_html(job['description'])}")
    for heading, key in (("What you'll do", "responsibilities"), ("What they're looking for", "requirements"),
                         ("Education", "education_requirements"), ("Benefits", "benefits")):
        block = list_html(job.get(key))
        if block:
            sections.append(f"<h2>{heading}</h2>\n{block}")
    skills = [s for s in (job.get("preferred_skills") or []) if s][:12]
    if skills:
        sections.append("<h2>Skills mentioned</h2><ul class=\"tags\">" + "".join(f"<li>{esc(s)}</li>" for s in skills) + "</ul>")
    if company.get("description"):
        sections.append(f'<h2>About {esc(cname)}</h2><p>{esc(company["description"])}</p>')
    source = job.get("source_attribution") or (f"{cname} careers site" if job.get("is_official_source") else None)
    sections.append(
        f'<p class="fine">{"Source: " + esc(source) + ". " if source else ""}CareerOS is not the employer. '
        f"Apply through {esc(cname)}'s own site, and never pay anyone to be considered for a job.</p>"
    )

    same_company, similar = related_jobs(job, by_company, by_country)
    related = ""
    if same_company:
        related += (f'<section class="related"><div class="related-head"><h2>More roles at {esc(cname)}</h2>'
                    f'<a href="../companies/{cslug}.html">All {esc(cname)} jobs</a></div>'
                    f'<ul class="rows">{"".join(job_row(j) for j in same_company)}</ul></section>')
    if similar and country:
        related += (f'<section class="related"><div class="related-head"><h2>Similar roles in {esc(country)}</h2>'
                    f'<a href="in-{slugify(country)}.html">All jobs in {esc(country)}</a></div>'
                    f'<ul class="rows">{"".join(job_row(j) for j in similar)}</ul></section>')

    head = f"""{crumb_html}
<p class="band-eyebrow">{esc(kind)}{(" · " + esc(country)) if country else ""}</p>
<h1>{esc(job['title'])}</h1>
<p class="band-sub">{esc(cname)}{(" · " + esc(where)) if where else ""}</p>"""
    body = f"""{band(head, ticket=True)}
<div class="wrap">
{ticket}
<div class="layout">
  <article class="prose">
{chr(10).join(sections)}
  </article>
  <div class="side">
{prep_card(job)}
{app_card("jobs", job["slug"], "Track this application", "Save the role, get a reminder before it closes, and practise for the interview in CareerOS.")}
  </div>
</div>
{related}
</div>"""
    ld = [crumb_ld]
    posting = job_posting_ld(job, url)
    if posting:
        ld.append(posting)
    title = f"{job['title']} at {cname}" + (f", {where}" if where else "") + " | CareerOS"
    summary = plain(job.get("short_summary") or job.get("description"), 120)
    when = f" {label}." if job.get("application_deadline") else ""
    desc = (f"{kind} at {cname}{(' in ' + where) if where else ''}.{when} {summary}").strip()[:300]
    return page(title=title, description=plain(desc, 158), path=path, body=body, ld=ld)


def stat(value, label: str) -> str:
    return f'<div class="stat"><span class="stat-value">{esc(value)}</span><span class="stat-label">{esc(label)}</span></div>'


def country_page(country: str, jobs: list[dict]) -> str:
    path = f"jobs/in-{slugify(country)}.html"
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Jobs", "jobs/"), (f"Jobs in {country}", path)])
    companies = Counter(company_of(j).get("name") for j in jobs if company_of(j).get("name"))
    closing = sorted((j for j in jobs if (days_left(j.get("application_deadline")) or 99) <= 14 and days_left(j.get("application_deadline")) is not None),
                     key=lambda j: days_left(j.get("application_deadline")))
    early = [j for j in jobs if is_early_career(j)]
    sectors = Counter(SECTOR_LABELS.get(j.get("sector")) for j in jobs if SECTOR_LABELS.get(j.get("sector")))
    head = f"""{crumb_html}
<p class="band-eyebrow">Updated {esc(f"{TODAY.day} {TODAY.strftime('%B %Y')}")}</p>
<h1>Jobs in {esc(country)}</h1>
<p class="band-sub">Open roles from employers' own career sites. Closed roles are removed every day.</p>
<div class="stats">{stat(len(jobs), "open roles")}{stat(len(companies), "companies hiring")}{stat(len(closing), "closing in 14 days")}</div>"""
    blocks = []
    if closing:
        blocks.append(f'<section class="block"><h2>Closing soon</h2><ul class="rows">{"".join(job_row(j, up="../") for j in closing[:8])}</ul></section>')
    if early:
        blocks.append(f'<section class="block"><h2>Graduate programmes, internships and entry-level roles</h2><ul class="rows">{"".join(job_row(j) for j in early[:12])}</ul></section>')
    top_companies = "".join(
        f'<li><a href="../companies/{company_slug(company_of(next(j for j in jobs if company_of(j).get("name") == n)))}.html">{esc(n)} <span>{c}</span></a></li>'
        for n, c in companies.most_common(12)
    )
    side = f'<aside class="side-card"><h2>Top employers in {esc(country)}</h2><ul class="count-list">{top_companies}</ul></aside>'
    if sectors:
        side += ('<aside class="side-card"><h2>By field</h2><ul class="count-list plain">'
                 + "".join(f"<li>{esc(s)} <span>{c}</span></li>" for s, c in sectors.most_common(8)) + "</ul></aside>")
    blocks.append(f'<section class="block"><h2>All open roles</h2><ul class="rows">{"".join(job_row(j) for j in jobs)}</ul></section>')
    body = f"""{band(head)}
<div class="wrap layout layout-hub">
  <div>{"".join(blocks)}</div>
  <div class="side">{side}</div>
</div>"""
    top = ", ".join(n for n, _ in companies.most_common(3))
    return page(
        title=f"Jobs in {country}: {len(jobs)} open roles | CareerOS",
        description=plain(f"{len(jobs)} open jobs in {country} from {len(companies)} employers' career sites, including {top}. "
                          f"Graduate programmes, internships and experienced roles, updated daily."),
        path=path, body=body, ld=[crumb_ld],
    )


def jobs_index(jobs: list[dict], countries: dict[str, list[dict]], companies: dict[str, list[dict]]) -> str:
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Jobs", "jobs/")])
    ordered = sorted(countries.items(), key=lambda kv: (kv[0] != FOCUS_COUNTRY, -len(kv[1]), kv[0]))
    featured = "".join(
        f'<li><a class="tile" href="in-{slugify(c)}.html"><span class="tile-name">{esc(c)}</span>'
        f'<span class="tile-count">{len(rows)} roles</span></a></li>' for c, rows in ordered[:8]
    )
    rest = "".join(f'<li><a href="in-{slugify(c)}.html">{esc(c)} <span>{len(rows)}</span></a></li>' for c, rows in ordered[8:])
    head = f"""{crumb_html}
<p class="band-eyebrow">Updated daily</p>
<h1>Jobs from employers' own career sites</h1>
<p class="band-sub">Graduate programmes, internships and experienced roles. Every listing links to where the employer wants you to apply.</p>
<div class="stats">{stat(len(jobs), "open roles")}{stat(len(companies), "companies")}{stat(len(countries), "countries")}</div>"""
    body = f"""{band(head)}
<div class="wrap">
<section class="block"><h2>Browse by country</h2><ul class="tiles">{featured}</ul>
{f'<ul class="pills">{rest}</ul>' if rest else ''}</section>
<section class="block"><h2>Newest roles</h2><ul class="rows">{"".join(job_row(j) for j in jobs[:40])}</ul></section>
</div>"""
    return page(
        title="Jobs, Graduate Programmes and Internships | CareerOS",
        description=f"{len(jobs)} open jobs from {len(companies)} employers in {len(countries)} countries, including graduate programmes and internships. Updated daily.",
        path="jobs/", body=body, ld=[crumb_ld],
    )


# --------------------------------------------------------------------------------------- companies

def company_page(company: dict, jobs: list[dict]) -> str:
    slug = company_slug(company)
    path = f"companies/{slug}.html"
    name = company.get("name") or "Company"
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Companies", "companies/"), (name, path)])
    countries = Counter(j.get("country") for j in jobs if j.get("country"))
    early = sum(1 for j in jobs if is_early_career(j))
    head = f"""{crumb_html}
<div class="band-org">{logo_tile(company, "xl")}<div>
<p class="band-eyebrow">{esc(company.get("industry") or "Employer")}</p>
<h1>Jobs at {esc(name)}</h1></div></div>
<div class="stats">{stat(len(jobs), "open roles")}{stat(len(countries), "countries")}{stat(early, "early-career roles") if early else ""}</div>"""
    about = []
    if company.get("description"):
        about.append(f"<p>{esc(company['description'])}</p>")
    links = []
    if company.get("website_url"):
        links.append(f'<a href="{esc(company["website_url"])}" rel="nofollow noopener" target="_blank">Website</a>')
    if company.get("career_url"):
        links.append(f'<a href="{esc(company["career_url"])}" rel="nofollow noopener" target="_blank">Careers site</a>')
    if links:
        about.append(f'<p class="link-row">{" ".join(links)}</p>')
    side = f'<aside class="side-card"><h2>About {esc(name)}</h2>{"".join(about) or "<p>Open roles listed from the company&#39;s own careers site.</p>"}</aside>'
    if len(countries) > 1:
        side += ('<aside class="side-card"><h2>Where they\'re hiring</h2><ul class="count-list">'
                 + "".join(f'<li><a href="../jobs/in-{slugify(c)}.html">{esc(c)} <span>{n}</span></a></li>' for c, n in countries.most_common(10))
                 + "</ul></aside>")
    body = f"""{band(head)}
<div class="wrap layout layout-hub">
  <section class="block"><h2>Open roles</h2><ul class="rows">{"".join(job_row(j, "../jobs/") for j in jobs)}</ul></section>
  <div class="side">{side}</div>
</div>"""
    org_ld = {"@context": "https://schema.org", "@type": "Organization", "name": name}
    if company.get("website_url"):
        org_ld["url"] = company["website_url"]
    if company.get("logo_url"):
        org_ld["logo"] = company["logo_url"]
    where = ", ".join(c for c, _ in countries.most_common(3))
    return page(
        title=f"{name} Jobs and Careers: {len(jobs)} open roles | CareerOS",
        description=plain(f"{len(jobs)} open {name} jobs{(' in ' + where) if where else ''}. "
                          f"{company.get('description') or 'Roles listed from the company careers site, updated daily.'}"),
        path=path, body=body, ld=[crumb_ld, org_ld],
    )


def companies_index(companies: dict[str, list[dict]], info: dict[str, dict]) -> str:
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Companies", "companies/")])
    ordered = sorted(companies.items(), key=lambda kv: (-len(kv[1]), (info[kv[0]].get("name") or "").lower()))
    items = "".join(
        f'<li><a class="row" href="{slug}.html">{logo_tile(info[slug], "sm")}<span class="row-text">'
        f'<span class="row-title">{esc(info[slug].get("name"))}</span>'
        f'<span class="row-meta">{esc(info[slug].get("industry") or "")}</span></span>'
        f'<span class="chip chip-later">{len(jobs)} {"role" if len(jobs) == 1 else "roles"}</span></a></li>'
        for slug, jobs in ordered
    )
    head = f"""{crumb_html}
<p class="band-eyebrow">Updated daily</p>
<h1>Companies hiring now</h1>
<p class="band-sub">{len(companies)} employers with open roles, listed from their own careers sites.</p>"""
    body = f'{band(head)}<div class="wrap"><section class="block"><ul class="rows rows-grid">{items}</ul></section></div>'
    return page(
        title=f"Companies Hiring Now: {len(companies)} employers | CareerOS",
        description=f"{len(companies)} companies with open jobs, graduate programmes and internships, listed from their own careers sites and updated daily.",
        path="companies/", body=body, ld=[crumb_ld],
    )


# --------------------------------------------------------------------------------------- scholarships

def scholarship_page(s: dict, others: list[dict]) -> str:
    slug = safe_slug(s["slug"])
    path = f"scholarships/{slug}.html"
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Scholarships", "scholarships/"), (s["name"], path)])
    provider = {"name": s.get("organization") or s["name"], "logo_url": s.get("thumbnail_url")}
    label, tone = countdown(s.get("application_deadline"))
    deadline = nice_date(s.get("application_deadline"))
    facts = [
        ("Study in", s.get("country")), ("Funding", FUNDING_LABELS.get(s.get("funding_type"))),
        ("Levels", ", ".join(DEGREE_LABELS.get(x, x.replace("_", " ").capitalize()) for x in (s.get("degree_levels") or [])) or None),
        ("Fields", ", ".join((s.get("fields_of_study") or [])[:3]) or None),
    ]
    facts_html = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in facts if v)
    apply_url = s.get("official_url") or s.get("source_url")
    apply_btn = (f'<a class="btn btn-primary" href="{esc(apply_url)}" rel="nofollow noopener" target="_blank">Official scholarship page</a>'
                 if apply_url else "")
    ticket = f"""<div class="ticket">
  <div class="ticket-main">
    <div class="ticket-org">{logo_tile(provider, "lg")}<div><span class="ticket-company">{esc(provider['name'])}</span></div></div>
    <dl class="facts">{facts_html}</dl>
  </div>
  <div class="ticket-stub">
    <span class="stub-label">Deadline</span>
    <span class="stub-count stub-{tone}">{esc(label)}</span>
    {f'<span class="stub-date">{esc(deadline)}</span>' if deadline else ''}
    {apply_btn}
  </div>
</div>"""
    sections = []
    if s.get("summary"):
        sections.append(f'<p class="lead">{esc(s["summary"])}</p>')
    if s.get("description"):
        sections.append(f"<h2>About the scholarship</h2>\n{text_to_html(s['description'])}")
    covers = [v for v in (s.get("tuition_coverage"), s.get("monthly_stipend"), s.get("travel_support"),
                          s.get("insurance_support"), s.get("accommodation_support")) if v]
    if covers:
        sections.append(f"<h2>What it covers</h2>\n{list_html(covers)}")
    for heading, key in (("Who can apply", "eligible_nationalities"), ("Academic requirements", "academic_requirements"),
                         ("Language requirements", "language_requirements"), ("Documents you'll need", "required_documents")):
        block = list_html(s.get(key))
        if block:
            sections.append(f"<h2>{heading}</h2>\n{block}")
    sections.append('<p class="fine">Confirm every detail and deadline on the official page. CareerOS is not the scholarship provider, '
                    "and genuine scholarships never ask you to pay to receive an award.</p>")
    prep = ('<aside class="side-card"><h2>Before you apply</h2><p>Strong applications start early and follow the instructions exactly.</p>'
            '<ul class="link-list"><li><a href="../guides/scholarship-application-tips.html">How to apply for scholarships abroad</a></li>'
            '<li><a href="../guides/ats-friendly-cv.html">Write a clear, focused CV</a></li></ul></aside>')
    more = [o for o in others if o["id"] != s["id"]][:4]
    related = ""
    if more:
        related = (f'<section class="related"><div class="related-head"><h2>More scholarships open now</h2><a href="./">All scholarships</a></div>'
                   f'<ul class="rows">{"".join(scholarship_row(o) for o in more)}</ul></section>')
    head = f"""{crumb_html}
<p class="band-eyebrow">Scholarship{(" · " + esc(s["country"])) if s.get("country") else ""}</p>
<h1>{esc(s['name'])}</h1>
<p class="band-sub">{esc(" · ".join(b for b in (s.get("organization"), FUNDING_LABELS.get(s.get("funding_type"))) if b))}</p>"""
    body = f"""{band(head, ticket=True)}
<div class="wrap">
{ticket}
<div class="layout">
  <article class="prose">{chr(10).join(sections)}</article>
  <div class="side">{prep}{app_card("scholarships", s["slug"], "Never miss the deadline", "Save it in CareerOS to get reminders and track your application.")}</div>
</div>
{related}
</div>"""
    title = s["name"] + (f" ({s['country']})" if s.get("country") else "") + " | CareerOS"
    desc = plain(f"{FUNDING_LABELS.get(s.get('funding_type'), 'Scholarship')}{(' to study in ' + s['country']) if s.get('country') else ''}. "
                 f"{label}. {s.get('summary') or plain(s.get('description'), 100)}", 158)
    return page(title=title, description=desc, path=path, body=body, ld=[crumb_ld])


def scholarship_row(s: dict) -> str:
    return row(f"{safe_slug(s['slug'])}.html", {"name": s.get("organization") or s["name"], "logo_url": s.get("thumbnail_url")},
               s["name"], [s.get("organization"), s.get("country"), FUNDING_LABELS.get(s.get("funding_type"))],
               s.get("application_deadline"))


def scholarships_index(items: list[dict]) -> str:
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Scholarships", "scholarships/")])
    ordered = sorted(items, key=lambda s: (days_left(s.get("application_deadline")) is None, days_left(s.get("application_deadline")) or 0))
    full = sum(1 for s in items if s.get("funding_type") == "FULL")
    head = f"""{crumb_html}
<p class="band-eyebrow">Updated daily</p>
<h1>Scholarships open now</h1>
<p class="band-sub">With official application pages, for undergraduate, master's and PhD study. Closed ones are removed.</p>
<div class="stats">{stat(len(items), "open scholarships")}{stat(full, "fully funded") if full else ""}</div>"""
    body = f"""{band(head)}
<div class="wrap layout layout-hub">
  <section class="block"><h2>Closing first</h2><ul class="rows">{"".join(scholarship_row(s) for s in ordered)}</ul></section>
  <div class="side"><aside class="side-card"><h2>Applying this year?</h2><p>Start 9 to 12 months before your course, and check eligibility before you write a word.</p>
  <ul class="link-list"><li><a href="../guides/scholarship-application-tips.html">Step-by-step application guide</a></li></ul></aside></div>
</div>"""
    return page(
        title="Scholarships Open Now: Fully and Partially Funded | CareerOS",
        description=f"{len(items)} open scholarships for undergraduate, master's and PhD study, with eligibility, funding and official application links. Updated daily.",
        path="scholarships/", body=body, ld=[crumb_ld],
    )


# --------------------------------------------------------------------------------------- sitemap & build

def sitemap(out: Path, urls: list[tuple[str, str | None]]) -> None:
    unique = dict(reversed(urls))  # one entry per URL (first one wins); duplicates make Search Console warn
    rows = "".join(f"  <url><loc>{esc(SITE + '/' + u)}</loc>{f'<lastmod>{m}</lastmod>' if m else ''}</url>\n"
                   for u, m in reversed(unique.items()))
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{rows}</urlset>\n',
        encoding="utf-8",
    )


def build(out: Path, jobs: list[dict], scholarships: list[dict]) -> dict:
    today = TODAY.isoformat()
    for folder in ("jobs", "companies", "scholarships"):
        shutil.rmtree(out / folder, ignore_errors=True)
        (out / folder).mkdir(parents=True)

    urls: list[tuple[str, str | None]] = [("", today), ("jobs/", today), ("companies/", today), ("scholarships/", today)]
    for static in ("privacy.html", "terms.html", "refund.html"):
        if (out / static).exists():
            urls.append((static, None))
    guides = out / "guides"
    if guides.exists():
        urls.append(("guides/", None))
        urls.extend((f"guides/{p.name}", None) for p in sorted(guides.glob("*.html")) if p.name != "index.html")

    jobs = [j for j in jobs if not TEST_POSTING.search(j.get("title") or "")]
    country_display: dict[str, str] = {}  # slug -> first spelling seen, so variants share one hub page
    for job in jobs:
        if (job.get("country") or "").strip():
            name = canonical_country(job["country"].strip())
            job["country"] = country_display.setdefault(slugify(name), name)

    by_country: dict[str, list[dict]] = {}
    by_company: dict[str, list[dict]] = {}
    company_info: dict[str, dict] = {}
    for job in jobs:
        if job.get("country"):
            by_country.setdefault(job["country"], []).append(job)
        cslug = company_slug(company_of(job))
        by_company.setdefault(cslug, []).append(job)
        company_info.setdefault(cslug, company_of(job))

    for job in jobs:
        name = f"{safe_slug(job['slug'])}.html"
        (out / "jobs" / name).write_text(job_page(job, by_company, by_country), encoding="utf-8")
        urls.append((f"jobs/{name}", (parse_dt(job.get("published_at")) or datetime.now(timezone.utc)).date().isoformat()))
    for country, rows in by_country.items():
        (out / "jobs" / f"in-{slugify(country)}.html").write_text(country_page(country, rows), encoding="utf-8")
        urls.append((f"jobs/in-{slugify(country)}.html", today))
    (out / "jobs" / "index.html").write_text(jobs_index(jobs, by_country, by_company), encoding="utf-8")

    for cslug, rows in by_company.items():
        (out / "companies" / f"{cslug}.html").write_text(company_page(company_info[cslug], rows), encoding="utf-8")
        urls.append((f"companies/{cslug}.html", today))
    (out / "companies" / "index.html").write_text(companies_index(by_company, company_info), encoding="utf-8")

    for s in scholarships:
        name = f"{safe_slug(s['slug'])}.html"
        (out / "scholarships" / name).write_text(scholarship_page(s, scholarships), encoding="utf-8")
        urls.append((f"scholarships/{name}", (parse_dt(s.get("published_at")) or datetime.now(timezone.utc)).date().isoformat()))
    (out / "scholarships" / "index.html").write_text(scholarships_index(scholarships), encoding="utf-8")

    sitemap(out, urls)
    return {"jobs": len(jobs), "countries": len(by_country), "companies": len(by_company), "scholarships": len(scholarships), "urls": len(urls)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, help="reuse/save fetched data (local design work only)")
    args = parser.parse_args()
    if args.cache and args.cache.exists():
        data = json.loads(args.cache.read_text(encoding="utf-8"))
        jobs, scholarships = data["jobs"], data["scholarships"]
    else:
        jobs, scholarships = fetch()
        if args.cache:
            args.cache.write_text(json.dumps({"jobs": jobs, "scholarships": scholarships}), encoding="utf-8")
    if not jobs and not scholarships:
        sys.exit("The API returned no listings; keeping the existing pages.")
    print(json.dumps(build(args.out, jobs, scholarships)))


if __name__ == "__main__":
    main()
