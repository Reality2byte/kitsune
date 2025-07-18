from itertools import chain

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import F, ObjectDoesNotExist, Q
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from kitsune.products.models import Product
from kitsune.sumo import email_utils
from kitsune.wiki.config import (
    HOW_TO_CATEGORY,
    TEMPLATES_CATEGORY,
    TROUBLESHOOTING_CATEGORY,
)
from kitsune.wiki.models import Document, Locale, Revision


class Command(BaseCommand):
    help = 'Sends out the weekly "Ready for review" digest email.'

    def handle(self, **options):
        @email_utils.safe_translation
        def _make_digest_mail(locale, user, context):
            subject = _("[Reviews Pending: %s] SUMO needs your help!") % locale

            return email_utils.make_mail(
                subject=subject,
                text_template="wiki/email/ready_for_review_weekly_digest.ltxt",
                html_template="wiki/email/ready_for_review_weekly_digest.html",
                context_vars=context,
                from_email=settings.TIDINGS_FROM_ADDRESS,
                to_email=user.email,
            )

        # Get the list of revisions ready for review
        categories = (HOW_TO_CATEGORY, TROUBLESHOOTING_CATEGORY, TEMPLATES_CATEGORY)

        revs = Revision.objects.filter(
            reviewed=None, document__is_archived=False, document__category__in=categories
        )

        revs = revs.filter(
            Q(document__current_revision_id__lt=F("id")) | Q(document__current_revision_id=None)
        )

        locales = revs.values_list("document__locale", flat=True).distinct()
        products = Product.active.all()

        messages = []

        for loc in locales:
            doc_ids = (
                revs.filter(document__locale=loc).values_list("document", flat=True).distinct()
            )

            try:
                leaders = Locale.objects.get(locale=loc).leaders.all()
                reviewers = Locale.objects.get(locale=loc).reviewers.all()
                users = {user for user in chain(leaders, reviewers) if user.is_active}
            except ObjectDoesNotExist:
                # Locale does not exist, so skip to the next locale
                continue

            for user in users:
                docs_list = []
                docs = Document.objects.unrestricted(user, id__in=doc_ids)
                for product in products:
                    product_docs = docs.filter(
                        Q(parent=None, products__in=[product]) | Q(parent__products__in=[product])
                    )
                    if product_docs:
                        docs_list.append(
                            {
                                'product': pgettext("DB: products.Product.title", product.title),
                                'docs': product_docs,
                            }
                        )

                product_docs = docs.filter(
                    Q(parent=None, products=None) | Q(parent__products=None)
                )

                if product_docs:
                    docs_list.append({'product': _("Other products"), 'docs': product_docs})

                messages.append(
                    _make_digest_mail(
                        loc,
                        user,
                        {
                            "host": Site.objects.get_current().domain,
                            "locale": loc,
                            "recipient": user,
                            "docs_list": docs_list,
                            "products": products,
                        },
                    )
                )

        email_utils.send_messages(messages)
