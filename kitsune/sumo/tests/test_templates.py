from django.template import engines as template_engines
from django.template.loader import render_to_string
from django.test import override_settings
from django.test.client import RequestFactory
from django.utils import translation
from pyquery import PyQuery as pq

from kitsune.sumo.tests import TestCase
from kitsune.users.tests import UserFactory

# def test_breadcrumb():
#     """Make sure breadcrumb links start with /."""
#     c = Client()
#     response = c.get(reverse('search'))
#
#     doc = pq(response.content)
#     href = doc('.breadcrumbs a')[0]
#     self.assertEqual('/', href.attrib['href'][0])


class MockRequestTests(TestCase):
    """Base class for tests that need a mock request"""

    def setUp(self):
        super().setUp()
        request = RequestFactory()
        request.GET = {}
        request.LANGUAGE_CODE = "en-US"
        request.META = {"csrf_token": "NOTPROVIDED"}
        self.request = request


class BaseTemplateTests(MockRequestTests):
    """Tests for base.html"""

    def setUp(self):
        super().setUp()
        self.template = "base.html"

    def test_dir_ltr(self):
        """Make sure dir attr is set to 'ltr' for LTR language."""
        self.request.user = UserFactory()
        html = render_to_string(self.template, request=self.request)
        self.assertEqual("ltr", pq(html)("html").attr["dir"])

    def test_dir_rtl(self):
        """Make sure dir attr is set to 'rtl' for RTL language."""
        translation.activate("he")
        self.request.LANGUAGE_CODE = "he"
        self.request.user = UserFactory()
        html = render_to_string(self.template, request=self.request)
        self.assertEqual("rtl", pq(html)("html").attr["dir"])
        translation.deactivate()

    def test_multi_feeds(self):
        """Ensure that multiple feeds are put into the page when set."""

        self.request.user = UserFactory()
        feed_urls = (
            ("/feed_one", "First Feed"),
            ("/feed_two", "Second Feed"),
        )

        doc = pq(render_to_string(self.template, {"feeds": feed_urls}, request=self.request))
        feeds = doc('link[type="application/atom+xml"]')
        self.assertEqual(2, len(feeds))
        self.assertEqual("First Feed", feeds[0].attrib["title"])
        self.assertEqual("Second Feed", feeds[1].attrib["title"])

    def test_readonly_attr(self):
        self.request.user = UserFactory()
        html = render_to_string(self.template, request=self.request)
        doc = pq(html)
        self.assertEqual("false", doc("body")[0].attrib["data-readonly"])

    @override_settings(READ_ONLY=True)
    def test_readonly_login_link_disabled(self):
        """Ensure that login/register links are hidden in READ_ONLY."""
        self.request.user = UserFactory()
        html = render_to_string(self.template, request=self.request)
        doc = pq(html)
        self.assertEqual(0, len(doc("a.sign-out, a.sign-in")))

    # TODO: Enable this test after the redesign is complete.
    # @override_settings(READ_ONLY=False)
    # def test_not_readonly_login_link_enabled(self):
    #   """Ensure that login/register links are visible in not READ_ONLY."""
    #    html = render_to_string(self.template, request=self.request)
    #    doc = pq(html)
    #    assert len(doc('a.sign-out, a.register')) > 0


class ErrorListTests(MockRequestTests):
    """Tests for errorlist.html, which renders form validation errors."""

    def test_escaping(self):
        """Make sure we escape HTML entities, lest we court XSS errors."""

        class MockForm:
            errors = True
            auto_id = "id_"

            def visible_fields(self):
                return [{"errors": ['<"evil&ness-field">']}]

            def non_field_errors(self):
                return ['<"evil&ness-non-field">']

        source = (
            """{% from "layout/errorlist.html" import errorlist %}""" """{{ errorlist(form) }}"""
        )
        template = template_engines["jinja2"].from_string(source)
        html = template.render({"form": MockForm()})
        assert '<"evil&ness' not in html
        assert "&lt;&#34;evil&amp;ness-field&#34;&gt;" in html
        assert "&lt;&#34;evil&amp;ness-non-field&#34;&gt;" in html
