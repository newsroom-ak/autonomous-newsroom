Scoped repair. Edit ONLY `repo/app/newsroom/templates/index.html`. Do not change any other file.

Defect: the whole template is wrapped in `{% raw %}` (first line) ... `{% endraw %}` (last line),
so Jinja outputs the template source instead of rendering it. The page shows literal
`{% for article in articles %}` and no article data.

Failing committed tests (do not edit tests):
- tests/acceptance/test_ticket_7.py::test_homepage_renders_articles_and_header
  -> "Updated 2 hours ago" is not in the response
- tests/acceptance/test_ticket_7.py::test_homepage_escapes_html_and_filters_bad_links
  -> the escaped headline `&lt;script&gt;...` is not in the response

Fix: delete the `{% raw %}` line and the `{% endraw %}` line. Keep everything else exactly as it
is (headline, summary, tag badge, source link, fallback label, "Updated ..." header, responsive
inline CSS, empty state). The inline CSS uses single braces only, which Jinja does not
interpret, so no other escaping is needed.

Read the file once, apply one patch removing those two lines, then reply. Nothing else.
