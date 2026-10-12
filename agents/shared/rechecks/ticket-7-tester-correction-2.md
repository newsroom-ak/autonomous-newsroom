Human-authorized final correction. Change ONLY the article-ordering check in
`test_homepage_renders_articles_and_header`; keep every other line and assertion.

Your fixture inserts 12 articles, i = 0..11, all with fetched_at = base_dt (2026-10-11 12:00 UTC):
- even i: published_at = base_dt.replace(hour=10 - i)   (10:00, 08:00, 06:00, 04:00, 02:00, 00:00)
- odd i:  published_at = None

Approved ordering rule (tickets #6/#7): newest first by the parsed publication time; when
published_at is missing or unparseable, use fetched_at instead. The page shows the 10 newest.

Failing assertion: the loop `for i in range(10)` assumes "Title 0".."Title 9" are the 10 shown,
which ignores the fallback rule, so `assert f"Title {i}" in html` fails for i = 8.

Fix: derive the expected set of 10 from the fixture data and the rule above, assert exactly those
10 headlines appear and the remaining 2 do not, and keep `html.count('<div class="article">') == 10`.
Run the per-article checks (summary/label, tag, link) for the expected 10.
Pitfall: "Title 1" is a substring of "Title 10" and "Title 11"; match the full headline element,
e.g. '<div class="title">Title 1</div>', not the bare text.

Do not edit anything else. Make the edit, then reply with the expected set you derived.
