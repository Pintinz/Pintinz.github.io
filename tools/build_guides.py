"""Builds guides/*.html from tools/guides/*.txt (original CareerOS articles).

Each .txt starts with `key: value` lines (title, description, eyebrow, updated), a blank line,
then a small Markdown subset: `## heading`, paragraphs, `- ` and `1. ` lists, **bold**, *italic*,
[links](url) and `> ` callouts.

    python tools/build_guides.py [--out DIR]
"""

from __future__ import annotations

import argparse
import html
import math
import re
from datetime import date
from pathlib import Path

from build_listings import ROOT, SITE, band, breadcrumbs, esc, page, slugify

SOURCE = Path(__file__).resolve().parent / "guides"
ORG = {"@type": "Organization", "name": "CareerOS", "url": f"{SITE}/", "logo": f"{SITE}/assets/careeros-symbol.png"}


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", text)
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(html.unescape(m.group(2)))}">{m.group(1)}</a>', text)


def render(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Article HTML plus the (anchor, heading) list for the table of contents."""
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("### "):
            out.append(f'<h3 id="{slugify(line[4:])}">{inline(line[4:])}</h3>')
            i += 1
            continue
        if line.startswith("## "):
            anchor = slugify(line[3:])
            toc.append((anchor, line[3:]))
            out.append(f'<h2 id="{anchor}">{inline(line[3:])}</h2>')
            i += 1
            continue
        if line.startswith("> "):
            out.append(f'<p class="note">{inline(line[2:])}</p>')
            i += 1
            continue
        for pattern, tag in ((r"^- (.*)", "ul"), (r"^\d+\. (.*)", "ol")):
            if re.match(pattern, line):
                items = []
                while i < len(lines) and re.match(pattern, lines[i].rstrip()):
                    items.append(f"<li>{inline(re.match(pattern, lines[i].rstrip()).group(1))}</li>")
                    i += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{2,3} |> |- |\d+\. )", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append("<p>" + "<br>".join(inline(p) for p in para) + "</p>")
    return "\n".join(out), toc


def plain(text: str) -> str:
    """Markdown subset → plain text (for structured data)."""
    text = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", text)
    return re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text).strip()


def faq_items(body: str) -> list[tuple[str, str]]:
    """(question, answer) pairs from the "## Frequently asked questions" section, if any."""
    match = re.search(r"^## Frequently asked questions\s*$(.*?)(?=^## |\Z)", body, flags=re.M | re.S)
    if not match:
        return []
    items = []
    for block in re.split(r"^### ", match.group(1), flags=re.M)[1:]:
        question, _, answer = block.partition("\n")
        answer = " ".join(line.strip() for line in answer.strip().splitlines() if line.strip())
        if question.strip() and answer:
            items.append((plain(question), plain(answer)))
    return items


def parse(path: Path) -> dict:
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    meta = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    meta["slug"] = path.stem
    meta["body"] = body
    meta["minutes"] = max(1, math.ceil(len(re.findall(r"\w+", body)) / 220))
    return meta


def nice(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def guide_card(g: dict) -> str:
    return (f'<li><a href="{g["slug"]}.html"><span class="tag">{esc(g.get("eyebrow", "Guide"))}</span>'
            f'<h3>{esc(g["title"])}</h3><p>{esc(g["description"])}</p><span class="mins">{g["minutes"]} min read</span></a></li>')


def build(out: Path) -> int:
    guides = sorted((parse(p) for p in SOURCE.glob("*.txt")), key=lambda g: g["title"])
    folder = out / "guides"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.html"):
        old.unlink()
    for g in guides:
        path = f"guides/{g['slug']}.html"
        crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Guides", "guides/"), (g["title"], path)])
        article_html, toc = render(g["body"])
        article = {
            "@context": "https://schema.org", "@type": "Article", "headline": g["title"], "description": g["description"],
            "datePublished": g["updated"], "dateModified": g["updated"], "author": ORG, "publisher": ORG,
            "mainEntityOfPage": f"{SITE}/{path}", "image": f"{SITE}/assets/og-image.png",
        }
        ld = [crumb_ld, article]
        faqs = faq_items(g["body"])
        if faqs:
            ld.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs]})
        # Same-topic guides first, so each article's "read next" differs.
        others = sorted((o for o in guides if o["slug"] != g["slug"]),
                        key=lambda o: (o.get("eyebrow") != g.get("eyebrow"), o["title"]))[:3]
        toc_html = ""
        if len(toc) >= 3:
            toc_html = ('<nav class="side-card toc" aria-label="In this guide"><h2>In this guide</h2><ol>'
                        + "".join(f'<li><a href="#{a}">{esc(t)}</a></li>' for a, t in toc) + "</ol></nav>")
        head = f"""{crumb_html}
<p class="band-eyebrow">{esc(g.get('eyebrow', 'Guide'))}</p>
<h1>{esc(g['title'])}</h1>
<p class="band-sub">{esc(g['description'])}</p>
<p class="guide-meta"><span>By the CareerOS Team</span><span>Updated {esc(nice(g['updated']))}</span><span>{g['minutes']} min read</span></p>"""
        body = f"""{band(head)}
<div class="wrap layout layout-hub">
  <article class="prose prose-guide">
{article_html}
  </article>
  <div class="side">
{toc_html}
{app_card_plain(g['slug'])}
  </div>
</div>
<div class="wrap"><section class="related"><div class="related-head"><h2>Read next</h2><a href="./">All guides</a></div>
<ul class="guide-cards">{"".join(guide_card(o) for o in others)}</ul></section></div>"""
        (folder / f"{g['slug']}.html").write_text(
            page(title=f"{g['title']} | CareerOS", description=g["description"], path=path, body=body,
                 ld=ld, og_type="article"),
            encoding="utf-8",
        )
    crumb_html, crumb_ld = breadcrumbs([("CareerOS", ""), ("Guides", "guides/")])
    head = f"""{crumb_html}
<p class="band-eyebrow">{len(guides)} guides · free to read</p>
<h1>Career guides</h1>
<p class="band-sub">Practical, original advice on aptitude tests, interviews, CVs and scholarships, written by the CareerOS Team.</p>"""
    index = f"""{band(head)}
<div class="wrap layout-hub"><ul class="guide-cards">{"".join(guide_card(g) for g in guides)}</ul></div>"""
    (folder / "index.html").write_text(
        page(title="Career Guides: Aptitude Tests, Interviews, CVs and Scholarships | CareerOS",
             description="Free, practical guides on passing aptitude tests, answering interview questions, writing an ATS-friendly CV and applying for scholarships.",
             path="guides/", body=index, ld=[crumb_ld]),
        encoding="utf-8",
    )
    return len(guides)


def app_card_plain(slug: str) -> str:
    """App promo without a per-listing deep link (guides aren't listings)."""
    from build_listings import GOOGLE_PLAY_BADGE, campaign_for, play
    return f"""<aside class="side-card app-card">
  <img class="app-mark" src="../assets/careeros-symbol.png" alt="" width="40" height="40">
  <h2>Practise it in CareerOS</h2>
  <p>Timed aptitude tests, AI interview practice, CV checks and live jobs and scholarships. Free on Android.</p>
  <a class="play" href="{play(campaign_for(f'guides/{slug}.html'), 'card')}"><img src="{GOOGLE_PLAY_BADGE}" alt="Get it on Google Play" width="646" height="250" loading="lazy"></a>
</aside>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT)
    print(f"guides: {build(parser.parse_args().out)}")
