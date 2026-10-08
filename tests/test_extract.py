"""Contact and link extraction tests."""

from app.extract import extract_contacts, extract_links, page_looks_like_staff


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
    assert jane.name == "Jane Smith"


def test_contact_below_the_intro_is_still_found():
    preamble = "City news and meetings. " * 200
    html = f"""
    <html><body>
      <p>{preamble}</p>
      <p>Marti Praschan,<br>Chief Financial Officer</p>
      <p>734.794.6500</p>
      <a href="mailto:mpraschan@a2gov.org">mpraschan@a2gov.org</a>
    </body></html>
    """
    assert page_looks_like_staff(
        html,
        "https://www.a2gov.org/finance-and-administrative-services/financial-reporting/",
    )
    contacts = extract_contacts(html)
    marti = next(c for c in contacts if c.email == "mpraschan@a2gov.org")
    assert marti.name == "Marti Praschan"
    assert marti.title and "chief financial" in marti.title.lower()


def test_nav_labels_without_a_way_to_reach_them_are_dropped():
    html = """
    <html><body>
      <a href="/finance">Finance Leadership</a>
      <a href="/cfo">CFO</a>
      <p>Office of the Chief Financial Officer</p>
      <p>Executive Vice President</p>
    </body></html>
    """
    assert extract_contacts(html) == []


def test_escaped_staff_card_keeps_email_and_phone():
    html = (
        "<html><body><script>"
        '{"title":"Dr. Morgan R. Olsen","content":"'
        r"\u003Cp\u003E\u003Ca href=\u0022mailto:Monica.McBee@asu.edu\u0022\u003E"
        r"Monica McBee\u003C/a\u003E\u003Cbr /\u003E\r\n"
        r"Executive Assistant\u003Cbr /\u003E\r\n"
        r"\u003Ca href=\u0022tel:480-727-9921\u0022\u003E480-727-9921\u003C/a\u003E"
        r"\u003C/p\u003E"
        '"}</script>'
        "<nav>Finance Leadership CFO</nav>"
        "</body></html>"
    )
    contacts = extract_contacts(html)
    monica = next(c for c in contacts if c.email == "monica.mcbee@asu.edu")
    assert monica.name == "Monica McBee"
    assert monica.phone and "480" in monica.phone
    assert monica.title and "assistant" in monica.title.lower()
    assert all(c.email or c.phone for c in contacts)
    assert not any(c.name == "Finance Leadership" for c in contacts)


def test_generic_inbox_and_sentence_are_dropped():
    html = """
    <html><body>
      <p>Washtenaw County Treasurer for collection. Any payments made after the due date.</p>
      <a href="mailto:customerservice@a2gov.org">Customer Service</a>
    </body></html>
    """
    assert extract_contacts(html) == []


def test_stacked_name_title_phone_email_block_is_one_contact():
    html = """
    <html><body>
      <p>\u200bTami Cook,</p>
      <p>Accounting Services Manager</p>
      <p>734.794.6500 x45204</p>
      <a href="mailto:tcook@a2gov.org">tcook@a2gov.org</a>
    </body></html>
    """
    contacts = extract_contacts(html)
    tami = next(c for c in contacts if c.email == "tcook@a2gov.org")
    assert tami.name == "Tami Cook"
    assert tami.title == "Accounting Services Manager"
    assert tami.phone == "734.794.6500"


def test_middle_initial_names_are_kept():
    html = """
    <html><body>
      <p>Michael J. Pettigrew,</p><p>City Treasurer</p>
      <p>734.994.2833</p><p>CustomerService@a2gov.org</p>
    </body></html>
    """
    contacts = extract_contacts(html)
    assert any(
        c.name == "Michael J. Pettigrew" and c.title == "City Treasurer"
        for c in contacts
    )


def test_guessed_names_must_fit_the_email():
    html = """
    <html><body>
      <p>Fiscal Year budget feedback: <a href="mailto:budget@city.gov">budget@city.gov</a></p>
    </body></html>
    """
    (contact,) = extract_contacts(html)
    assert contact.email == "budget@city.gov"
    assert contact.name is None


def test_phone_only_lines_without_a_person_are_dropped():
    html = "<html><body><p>County Treasurer's Office Phone Number: 734.222.6600</p></body></html>"
    assert extract_contacts(html) == []


def test_cloudflare_protected_email_is_decoded():
    token = "f3b59a9d929d909a929fa09681859a909680b3928086dd969786"
    html = f'<html><body><a href="/cdn-cgi/l/email-protection#{token}">Email Financial Services</a></body></html>'
    emails = {c.email for c in extract_contacts(html)}
    assert emails == {"financialservices@asu.edu"}
    urls = {link.url for link in extract_links(html, "https://cfo.asu.edu/finance")}
    assert "mailto:FinancialServices@asu.edu" in urls


def test_stacked_name_title_phone_email_block_is_one_contact():
    html = """
    <html><body>
      <p>\u200bTami Cook,</p>
      <p>Accounting Services Manager</p>
      <p>734.794.6500 x45204</p>
      <a href="mailto:tcook@a2gov.org">tcook@a2gov.org</a>
    </body></html>
    """
    contacts = extract_contacts(html)
    tami = next(c for c in contacts if c.email == "tcook@a2gov.org")
    assert tami.name == "Tami Cook"
    assert tami.title == "Accounting Services Manager"
    assert tami.phone == "734.794.6500"


def test_middle_initial_names_are_kept():
    html = """
    <html><body>
      <p>Michael J. Pettigrew,</p><p>City Treasurer</p>
      <p>734.994.2833</p><p>CustomerService@a2gov.org</p>
    </body></html>
    """
    contacts = extract_contacts(html)
    assert any(
        c.name == "Michael J. Pettigrew" and c.title == "City Treasurer"
        for c in contacts
    )


def test_guessed_names_must_fit_the_email():
    html = """
    <html><body>
      <p>Fiscal Year budget feedback: <a href="mailto:budget@city.gov">budget@city.gov</a></p>
    </body></html>
    """
    (contact,) = extract_contacts(html)
    assert contact.email == "budget@city.gov"
    assert contact.name is None


def test_phone_only_lines_without_a_person_are_dropped():
    html = "<html><body><p>County Treasurer's Office Phone Number: 734.222.6600</p></body></html>"
    assert extract_contacts(html) == []


def test_cloudflare_protected_email_is_decoded():
    token = "f3b59a9d929d909a929fa09681859a909680b3928086dd969786"
    html = f'<html><body><a href="/cdn-cgi/l/email-protection#{token}">Email Financial Services</a></body></html>'
    emails = {c.email for c in extract_contacts(html)}
    assert emails == {"financialservices@asu.edu"}
    urls = {link.url for link in extract_links(html, "https://cfo.asu.edu/finance")}
    assert "mailto:FinancialServices@asu.edu" in urls
