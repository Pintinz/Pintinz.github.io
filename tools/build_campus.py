"""Builds the campus pages: campus/ambassadors.html (apply to be a campus ambassador) and
campus/sessions.html (career centres, NYSC CDS groups and student associations request a free
session). The forms post to the CareerOS API (/api/v1/campus/...), handled by campus.js.

    python tools/build_campus.py
"""

from __future__ import annotations

from pathlib import Path

from build_listings import ROOT, band, breadcrumbs, esc, page

PRIVACY = '<a href="../privacy.html">privacy policy</a>'


def field(name: str, label: str, *, kind: str = "text", required: bool = False, hint: str = "", options: list[tuple[str, str]] | None = None,
          autocomplete: str = "", maxlength: int = 160, rows: int = 0) -> str:
    req = " required" if required else ""
    star = "" if required else ' <span class="field-optional">(optional)</span>'
    auto = f' autocomplete="{autocomplete}"' if autocomplete else ""
    hint_html = f'<span class="field-hint" id="{name}-hint">{esc(hint)}</span>' if hint else ""
    described = f' aria-describedby="{name}-hint"' if hint else ""
    if options:
        opts = '<option value="">Choose one</option>' + "".join(f'<option value="{esc(v)}">{esc(t)}</option>' for v, t in options)
        control = f'<select id="{name}" name="{name}"{req}{described}>{opts}</select>'
    elif rows:
        control = f'<textarea id="{name}" name="{name}" rows="{rows}" maxlength="{maxlength}"{req}{described}></textarea>'
    else:
        control = f'<input id="{name}" name="{name}" type="{kind}" maxlength="{maxlength}"{auto}{req}{described}>'
    return f'<div class="field"><label for="{name}">{esc(label)}{star}</label>{hint_html}{control}</div>'


def form(endpoint: str, fields: str, submit: str, success: str) -> str:
    return f"""<form class="campus-form" data-campus-form="{endpoint}" novalidate>
  {fields}
  <div class="hp" aria-hidden="true"><label for="website">Leave this empty</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
  <p class="form-status" role="status" aria-live="polite" data-form-status></p>
  <button class="btn btn-primary" type="submit">{esc(submit)}</button>
  <p class="form-privacy">We use these details only to reply to you about this request. See our {PRIVACY}.</p>
</form>
<div class="form-done" data-form-done hidden tabindex="-1"><h2>Thank you</h2><p>{esc(success)}</p></div>
<script src="../campus.js" defer></script>"""


def ambassadors() -> str:
    crumbs, ld = breadcrumbs([("CareerOS", ""), ("Campus ambassadors", "campus/ambassadors.html")])
    head = band(crumbs + """
<p class="band-eyebrow">Campus ambassadors</p>
<h1>Become a CareerOS campus ambassador</h1>
<p class="band-sub">Help students on your campus find real jobs and scholarships and prepare for aptitude tests and interviews, and earn CareerOS credits for every friend who joins.</p>""")
    fields = "".join([
        field("full_name", "Full name", required=True, autocomplete="name", maxlength=120),
        field("email", "Email", kind="email", required=True, autocomplete="email", maxlength=254,
              hint="Use the email of your CareerOS account. No account yet? Sign up in the app first."),
        field("phone", "WhatsApp number", kind="tel", autocomplete="tel", maxlength=32),
        field("campus", "University or polytechnic", required=True, hint="For example: University of Lagos"),
        field("course", "Course"),
        field("year_of_study", "Level or year", maxlength=40, hint="For example: 300 level"),
        field("reach", "How would you tell students about CareerOS?", rows=4, maxlength=600,
              hint="Department groups, associations, events, social media: whatever you already do."),
    ])
    body = head + f"""
<div class="wrap layout layout-hub">
  <article class="prose prose-guide campus">
<h2>What ambassadors do</h2>
<ul>
<li>Share CareerOS with classmates, departments and student associations on your campus.</li>
<li>Pass on scholarship deadlines and graduate trainee openings from the app to your groups.</li>
<li>Show friends how to practise aptitude tests and check their CV before they apply.</li>
</ul>
<h2>What you get</h2>
<ul>
<li><strong>More credits per invite.</strong> Ambassadors earn a higher reward than regular members for every friend who signs up with their invite code and verifies their account. The current amount shows in the app under Invite &amp; Earn.</li>
<li>Your friends get a bonus credit too when they join with your code.</li>
<li>Credits unlock premium tools such as AI interview practice and optimized CV downloads. They can't be exchanged for cash.</li>
</ul>
<h2>Who can apply</h2>
<p>Students at a university, polytechnic or college of education, with a CareerOS account. We review every application and reply by email.</p>
<h2 id="apply">Apply</h2>
{form("ambassador-applications", fields, "Send application", "We've received your application and will reply by email. Meanwhile, your invite code is ready in the app under Profile, then Invite & Earn.")}
  </article>
</div>"""
    return page(title="Become a CareerOS Campus Ambassador | CareerOS",
                description="Share CareerOS on your campus and earn credits for every friend who joins. Apply to be a CareerOS campus ambassador.",
                path="campus/ambassadors.html", body=body, ld=[ld], depth=1)


def sessions() -> str:
    crumbs, ld = breadcrumbs([("CareerOS", ""), ("Free sessions", "campus/sessions.html")])
    head = band(crumbs + """
<p class="band-eyebrow">For career centres and NYSC CDS groups</p>
<h1>Free career sessions for your students or corps members</h1>
<p class="band-sub">A free CareerOS session on winning scholarships, passing aptitude tests, or getting a CV and interview ready, for university career centres, NYSC CDS groups and student associations.</p>""")
    fields = "".join([
        field("org_type", "Type of group", required=True, options=[
            ("CAREER_CENTRE", "University or polytechnic career centre"), ("NYSC_CDS", "NYSC CDS group"),
            ("STUDENT_GROUP", "Student association or department"), ("OTHER", "Other")]),
        field("org_name", "Name of your group or institution", required=True, maxlength=200),
        field("contact_name", "Your name", required=True, autocomplete="name", maxlength=120),
        field("email", "Email", kind="email", required=True, autocomplete="email", maxlength=254),
        field("phone", "Phone or WhatsApp", kind="tel", autocomplete="tel", maxlength=32),
        field("location", "City and state", required=True, hint="For example: Ikeja, Lagos"),
        field("audience", "How many people would attend?", maxlength=80),
        field("preferred_dates", "Preferred dates or days", maxlength=200),
        '<fieldset class="field"><legend>Topics <span class="field-optional">(choose any)</span></legend>'
        + "".join(f'<label class="check"><input type="checkbox" name="topics" value="{v}"> {esc(t)}</label>' for v, t in [
            ("SCHOLARSHIPS", "Finding and winning scholarships"), ("APTITUDE_TESTS", "Passing aptitude tests"),
            ("CV_AND_INTERVIEWS", "CV and interviews"), ("JOB_SEARCH", "Finding graduate jobs and trainee programmes")])
        + "</fieldset>",
        field("notes", "Anything else we should know?", rows=4, maxlength=1000),
    ])
    body = head + f"""
<div class="wrap layout layout-hub">
  <article class="prose prose-guide campus">
<h2>What the session covers</h2>
<ul>
<li><strong>Scholarships:</strong> where to find real, open scholarships and how to write an application that stands out.</li>
<li><strong>Aptitude tests:</strong> what graduate employers test and how to practise under time pressure.</li>
<li><strong>CV and interviews:</strong> making a CV that applicant tracking systems can read, and answering competency questions.</li>
<li><strong>Job search:</strong> finding graduate trainee programmes and applying on employers' official pages.</li>
</ul>
<h2>How it works</h2>
<ul>
<li>Free of charge, about an hour, online or in person where we can reach you.</li>
<li>Attendees get a sign-up code for your group, so they can keep practising in the CareerOS app afterwards.</li>
<li>Your group gets a printable flyer with a QR code to share before the session.</li>
</ul>
<h2 id="request">Request a session</h2>
{form("partner-requests", fields, "Request a session", "We've received your request and will reply by email to agree a date.")}
  </article>
</div>"""
    return page(title="Free Career Sessions for Career Centres and NYSC CDS Groups | CareerOS",
                description="Request a free CareerOS session on scholarships, aptitude tests, CVs and interviews for your students or corps members.",
                path="campus/sessions.html", body=body, ld=[ld], depth=1)


def build(out: Path = ROOT) -> list[str]:
    folder = out / "campus"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ambassadors.html").write_text(ambassadors(), encoding="utf-8")
    (folder / "sessions.html").write_text(sessions(), encoding="utf-8")
    return ["campus/ambassadors.html", "campus/sessions.html"]


if __name__ == "__main__":
    print(build())
