"""Contact and link extraction tests."""

from app.extract import extract_contacts, extract_links


HTML = """
<html><body>
  <h2>Finance Department</h2>
  <ul>
    <li>Jane Smith, Finance Director —
      <a href="mailto:jsmith@city.gov">jsmith@city.gov</a>
      Phone: (555) 123-4567
    </li>
    <li>Budget Office contact: bob [at] city.gov</li>
  </ul>
  <a href="/finance/fy2024-budget.pdf">Adopted Budget FY2024</a>
  <a href="#top">Top</a>
</body></html>
"""


def test_extract_links_resolves_and_skips_fragments():
    links = extract_links(HTML, "https://example.gov/dept/")
    urls = {link.url for link in links}
    assert "https://example.gov/finance/fy2024-budget.pdf" in urls
    assert "mailto:jsmith@city.gov" in urls
    assert not any(u.endswith("#top") for u in urls)


def test_extract_contacts_including_obfuscated_email():
    contacts = extract_contacts(HTML)
    emails = {c.email for c in contacts if c.email}
    assert "jsmith@city.gov" in emails
    assert "bob@city.gov" in emails
    jane = next(c for c in contacts if c.email == "jsmith@city.gov")
    assert jane.title and "finance director" in jane.title.lower()
    assert jane.phone and "555" in jane.phone
