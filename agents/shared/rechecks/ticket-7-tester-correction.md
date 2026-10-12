Human-authorized correction. Change ONLY these two assertions in your existing tests; keep every
other line and assertion exactly as it is.

1. `test_homepage_escapes_html_and_filters_bad_links` asserts
   `"&lt;script&gt;alert('title')&lt;/script&gt;" in html`. Jinja2 autoescape (MarkupSafe) also
   escapes single quotes, so the safe output is `&lt;script&gt;alert(&#39;title&#39;)&lt;/script&gt;`.
   Assert that string instead. Keep `"<script>" not in html`, the javascript: link check and
   the escaped tag check.

2. `test_homepage_renders_articles_and_header` asserts `f"Summary {i}" in html` for every article,
   including fallback ones (`i % 3 == 0`). Approved behaviour: for a fallback article the page shows
   the headline plus the label "summary unavailable – showing headline" and does NOT present the
   stored fallback text as a model summary. For fallback articles, assert the headline and the
   label are present and that `f"Summary {i}"` is not shown; for model articles keep asserting
   the summary text and the "(model: ...)" note.

Do not edit any other file or test. Make the two edits, then reply with what you changed.
