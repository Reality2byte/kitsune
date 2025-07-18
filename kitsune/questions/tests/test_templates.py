import json
import random
from datetime import datetime, timedelta
from string import ascii_letters
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from pyquery import PyQuery as pq

from kitsune.products.tests import ProductFactory, TopicFactory
from kitsune.questions.events import QuestionReplyEvent, QuestionSolvedEvent
from kitsune.questions.models import Answer, Question, QuestionLocale, VoteMetadata
from kitsune.questions.tests import AAQConfigFactory, AnswerFactory, QuestionFactory, tags_eq
from kitsune.questions.views import NO_TAG, UNAPPROVED_TAG
from kitsune.sumo.templatetags.jinja_helpers import urlparams
from kitsune.sumo.tests import TestCase, attrs_eq, emailmessage_raise_smtp, get, post
from kitsune.sumo.urlresolvers import reverse
from kitsune.tags.models import SumoTag
from kitsune.tags.tests import TagFactory
from kitsune.tidings.models import Watch
from kitsune.upload.models import ImageAttachment
from kitsune.users.tests import UserFactory, add_permission


class AnswersTemplateTestCase(TestCase):
    """Test the Answers template."""

    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.client.login(username=self.user.username, password="testpass")
        self.question = AnswerFactory().question
        self.answer = self.question.answers.all()[0]

    def test_answer(self):
        """Posting a valid answer inserts it."""
        num_answers = self.question.answers.count()
        content = "lorem ipsum dolor sit amet"
        response = post(
            self.client,
            "questions.reply",
            {"content": content},
            args=[self.question.id],
        )

        self.assertEqual(1, len(response.redirect_chain))
        self.assertEqual(num_answers + 1, self.question.answers.count())

        new_answer = self.question.answers.order_by("-id")[0]
        self.assertEqual(content, new_answer.content)
        # Check canonical url
        doc = pq(response.content)
        self.assertEqual(
            "{}/en-US/questions/{}".format(settings.CANONICAL_URL, self.question.id),
            doc('link[rel="canonical"]')[0].attrib["href"],
        )

    def test_answer_upload(self):
        """Posting answer attaches an existing uploaded image to the answer."""
        f = open("kitsune/upload/tests/media/test.jpg", "rb")
        post(
            self.client,
            "upload.up_image_async",
            {"image": f},
            args=["auth.User", self.user.pk],
        )
        f.close()

        content = "lorem ipsum dolor sit amet"
        response = post(
            self.client,
            "questions.reply",
            {"content": content},
            args=[self.question.id],
        )
        self.assertEqual(200, response.status_code)

        new_answer = self.question.answers.order_by("-id")[0]
        self.assertEqual(1, new_answer.images.count())
        image = new_answer.images.all()[0]
        name = "098f6b.png"
        message = 'File name "{}" does not contain "{}"'.format(image.file.name, name)
        assert name in image.file.name, message
        self.assertEqual(self.user.username, image.creator.username)

        # Clean up
        ImageAttachment.objects.all().delete()

    def test_solve_unsolve(self):
        """Test accepting a solution and undoing."""
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(0, len(doc("div.solution")))

        ans = self.question.answers.all()[0]
        # Sign in as asker, solve and verify
        self.client.login(username=self.question.creator.username, password="testpass")
        response = post(self.client, "questions.solve", args=[self.question.id, ans.id])
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(1, len(doc("div.solution")))
        div = doc("h3.is-solution")[0].getparent().getparent()
        self.assertEqual("answer-{}".format(ans.id), div.attrib["id"])
        q = Question.objects.get(pk=self.question.id)
        self.assertEqual(q.solution, ans)
        self.assertEqual(q.solver, self.question.creator)

        # Try to solve again with different answer. It shouldn't blow up or
        # change the solution.
        AnswerFactory(question=q)
        response = post(self.client, "questions.solve", args=[self.question.id, ans.id])
        self.assertEqual(200, response.status_code)
        q = Question.objects.get(pk=self.question.id)
        self.assertEqual(q.solution, ans)

        # Unsolve and verify
        response = post(self.client, "questions.unsolve", args=[self.question.id, ans.id])
        q = Question.objects.get(pk=self.question.id)
        self.assertEqual(q.solution, None)
        self.assertEqual(q.solver, None)

    def test_only_owner_or_admin_can_solve_unsolve(self):
        """Make sure non-owner/non-admin can't solve/unsolve."""
        # Try as asker
        self.client.login(username=self.question.creator.username, password="testpass")
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc('input[name="solution"]')))
        self.client.logout()

        # Try as a nobody
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(0, len(doc('input[name="solution"]')))

        ans = self.question.answers.all()[0]
        # Try to solve
        response = post(self.client, "questions.solve", args=[self.question.id, ans.id])
        self.assertEqual(403, response.status_code)
        # Try to unsolve
        response = post(self.client, "questions.unsolve", args=[self.question.id, ans.id])
        self.assertEqual(403, response.status_code)

    def test_solve_unsolve_with_perm(self):
        """Test marking solve/unsolve with 'change_solution' permission."""
        u = UserFactory()
        add_permission(u, Question, "change_solution")
        self.client.login(username=u.username, password="testpass")
        ans = self.question.answers.all()[0]
        # Solve and verify
        post(self.client, "questions.solve", args=[self.question.id, ans.id])
        q = Question.objects.get(pk=self.question.id)
        self.assertEqual(q.solution, ans)
        self.assertEqual(q.solver, u)
        # Unsolve and verify
        post(self.client, "questions.unsolve", args=[self.question.id, ans.id])
        q = Question.objects.get(pk=self.question.id)
        self.assertEqual(q.solution, None)
        self.assertEqual(q.solver, None)

    def test_needs_info_checkbox(self):
        """Test that needs info checkbox is correctly shown"""
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc('input[name="needsinfo"]')))

        self.question.set_needs_info()

        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc('input[name="clear_needsinfo"]')))

    def test_question_vote_GET(self):
        """Attempting to vote with HTTP GET returns a 405."""
        response = get(self.client, "questions.vote", args=[self.question.id])
        self.assertEqual(405, response.status_code)

    def common_vote(self, me_too_count=1):
        """Helper method for question vote tests."""
        # Check that there are no votes and vote form renders
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        assert "0" in doc(".have-problem")[0].text
        self.assertEqual(me_too_count, len(doc("div.me-too form")))

        # Vote
        ua = "Mozilla/5.0 (DjangoTestClient)"
        self.client.post(
            reverse("questions.vote", args=[self.question.id]), {}, HTTP_USER_AGENT=ua
        )

        # Check that there is 1 vote and vote form doesn't render
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        assert "1" in doc(".have-problem")[0].text
        self.assertEqual(0, len(doc("div.me-too form")))
        # Verify user agent
        vote_meta = VoteMetadata.objects.all()[0]
        self.assertEqual("ua", vote_meta.key)
        self.assertEqual(ua, vote_meta.value)

        # Voting again (same user) should not increment vote count
        post(self.client, "questions.vote", args=[self.question.id])
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        assert "1" in doc(".have-problem")[0].text

    def test_question_authenticated_vote(self):
        """Authenticated user vote."""
        # Common vote test
        self.common_vote()

    def test_question_anonymous_vote(self):
        """Anonymous user vote."""
        # Log out
        self.client.logout()

        # Common vote test
        self.common_vote(2)

    def common_answer_vote(self):
        """Helper method for answer vote tests."""
        # Check that there are no votes and vote form renders
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc('form.helpful button[name="helpful"]')))

        # Vote
        ua = "Mozilla/5.0 (DjangoTestClient)"
        self.client.post(
            reverse("questions.answer_vote", args=[self.question.id, self.answer.id]),
            {"helpful": "y"},
            HTTP_USER_AGENT=ua,
        )

        # Check that there is 1 vote and vote form doesn't render
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)

        self.assertEqual(1, len(doc("#answer-{} span.is-helpful".format(self.answer.id))))
        self.assertEqual(0, len(doc('form.helpful input[name="helpful"]')))
        # Verify user agent
        vote_meta = VoteMetadata.objects.all()[0]
        self.assertEqual("ua", vote_meta.key)
        self.assertEqual(ua, vote_meta.value)

    def test_answer_authenticated_vote(self):
        """Authenticated user answer vote."""
        # log in as new user (didn't ask or answer question)
        self.client.logout()
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")

        # Common vote test
        self.common_answer_vote()

    def test_answer_anonymous_vote(self):
        """Anonymous user answer vote."""
        # Log out
        self.client.logout()

        # Common vote test
        self.common_answer_vote()

    def test_can_vote_on_asker_reply(self):
        """An answer posted by the asker can be voted on."""
        self.client.logout()
        # Post a new answer by the asker => two votable answers
        q = self.question
        Answer.objects.create(question=q, creator=q.creator, content="test")
        response = get(self.client, "questions.details", args=[q.id])
        doc = pq(response.content)
        self.assertEqual(2, len(doc('form.helpful button[name="helpful"]')))

    def test_asker_can_vote(self):
        """The asker can vote Not/Helpful."""
        self.client.login(username=self.question.creator.username, password="testpass")
        self.common_answer_vote()

    def test_can_solve_with_answer_by_asker(self):
        """An answer posted by the asker can be the solution."""
        self.client.login(username=self.question.creator.username, password="testpass")
        # Post a new answer by the asker => two solvable answers
        q = self.question
        Answer.objects.create(question=q, creator=q.creator, content="test")
        response = get(self.client, "questions.details", args=[q.id])
        doc = pq(response.content)
        self.assertEqual(2, len(doc('form.solution input[name="solution"]')))

    def test_delete_question_without_permissions(self):
        """Deleting a question without permissions is a 403."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.delete", args=[self.question.id])
        self.assertEqual(403, response.status_code)
        response = post(self.client, "questions.delete", args=[self.question.id])
        self.assertEqual(403, response.status_code)

    def test_delete_question_logged_out(self):
        """Deleting a question while logged out redirects to login."""
        self.client.logout()
        response = get(self.client, "questions.delete", args=[self.question.id])
        redirect = response.redirect_chain[0]
        self.assertEqual(302, redirect[1])
        self.assertEqual(
            "/{}{}?next=/en-US/questions/{}/delete".format(settings.LANGUAGE_CODE, settings.LOGIN_URL, self.question.id),
            redirect[0],
        )

        response = post(self.client, "questions.delete", args=[self.question.id])
        redirect = response.redirect_chain[0]
        self.assertEqual(302, redirect[1])
        self.assertEqual(
            "/{}{}?next=/en-US/questions/{}/delete".format(settings.LANGUAGE_CODE, settings.LOGIN_URL, self.question.id),
            redirect[0],
        )

    def test_delete_question_with_permissions(self):
        """Deleting a question with permissions."""
        u = UserFactory()
        add_permission(u, Question, "delete_question")
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.delete", args=[self.question.id])
        self.assertEqual(200, response.status_code)

        response = post(self.client, "questions.delete", args=[self.question.id])
        self.assertEqual(0, Question.objects.filter(pk=self.question.id).count())

    def test_delete_answer_without_permissions(self):
        """Deleting an answer without permissions sends 403."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        ans = self.question.last_answer
        response = get(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        self.assertEqual(403, response.status_code)

        response = post(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        self.assertEqual(403, response.status_code)

    def test_delete_answer_logged_out(self):
        """Deleting an answer while logged out redirects to login."""
        self.client.logout()
        q = self.question
        ans = q.last_answer
        response = get(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        redirect = response.redirect_chain[0]
        self.assertEqual(302, redirect[1])
        self.assertEqual(
            "/{}{}?next=/en-US/questions/{}/delete/{}".format(settings.LANGUAGE_CODE, settings.LOGIN_URL, q.id, ans.id),
            redirect[0],
        )

        response = post(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        redirect = response.redirect_chain[0]
        self.assertEqual(302, redirect[1])
        self.assertEqual(
            "/{}{}?next=/en-US/questions/{}/delete/{}".format(settings.LANGUAGE_CODE, settings.LOGIN_URL, q.id, ans.id),
            redirect[0],
        )

    def test_delete_answer_with_permissions(self):
        """Deleting an answer with permissions."""
        ans = self.question.last_answer
        u = UserFactory()
        add_permission(u, Answer, "delete_answer")
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        self.assertEqual(200, response.status_code)

        response = post(self.client, "questions.delete_answer", args=[self.question.id, ans.id])
        self.assertEqual(0, Answer.objects.filter(pk=self.question.id).count())

    def test_edit_answer_without_permission(self):
        """Editing an answer without permissions returns a 403.

        The edit link shouldn't show up on the Answers page."""
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(0, len(doc("ol.answers li.edit")))

        answer = self.question.last_answer
        response = get(self.client, "questions.edit_answer", args=[self.question.id, answer.id])
        self.assertEqual(403, response.status_code)

        content = "New content for answer"
        response = post(
            self.client,
            "questions.edit_answer",
            {"content": content},
            args=[self.question.id, answer.id],
        )
        self.assertEqual(403, response.status_code)

    def test_edit_answer_with_permissions(self):
        """Editing an answer with permissions.

        The edit link should show up on the Answers page."""
        u = UserFactory()
        add_permission(u, Answer, "change_answer")
        self.client.login(username=u.username, password="testpass")

        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc("li.edit")))

        answer = self.question.last_answer
        response = get(self.client, "questions.edit_answer", args=[self.question.id, answer.id])
        self.assertEqual(200, response.status_code)

        content = "New content for answer"
        response = post(
            self.client,
            "questions.edit_answer",
            {"content": content},
            args=[self.question.id, answer.id],
        )
        self.assertEqual(content, Answer.objects.get(pk=answer.id).content)

    def test_answer_creator_can_edit(self):
        """The creator of an answer can edit his/her answer."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")

        # Initially there should be no edit links
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(0, len(doc("ul.mzp-c-menu-list-list li.edit")))

        # Add an answer and verify the edit link shows up
        content = "lorem ipsum dolor sit amet"
        response = post(
            self.client,
            "questions.reply",
            {"content": content},
            args=[self.question.id],
        )
        doc = pq(response.content)
        self.assertEqual(1, len(doc("li.edit")))
        new_answer = self.question.answers.order_by("-id")[0]
        self.assertEqual(1, len(doc("#answer-{} li.edit".format(new_answer.id))))

        # Make sure it can be edited
        content = "New content for answer"
        response = post(
            self.client,
            "questions.edit_answer",
            {"content": content},
            args=[self.question.id, new_answer.id],
        )
        self.assertEqual(200, response.status_code)

        # Now lock it and make sure it can't be edited
        self.question.is_locked = True
        self.question.save()
        response = post(
            self.client,
            "questions.edit_answer",
            {"content": content},
            args=[self.question.id, new_answer.id],
        )
        self.assertEqual(403, response.status_code)

    def test_lock_question_without_permissions(self):
        """Trying to lock a question without permission is a 403."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        q = self.question
        response = post(self.client, "questions.lock", args=[q.id])
        self.assertEqual(403, response.status_code)

    def test_lock_question_logged_out(self):
        """Trying to lock a question while logged out redirects to login."""
        self.client.logout()
        q = self.question
        response = post(self.client, "questions.lock", args=[q.id])
        redirect = response.redirect_chain[0]
        self.assertEqual(302, redirect[1])
        self.assertEqual(
            "/{}{}?next=/en-US/questions/{}/lock".format(settings.LANGUAGE_CODE, settings.LOGIN_URL, q.id),
            redirect[0],
        )

    def test_lock_question_with_permissions_GET(self):
        """Trying to lock a question via HTTP GET."""
        u = UserFactory()
        add_permission(u, Question, "lock_question")
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.lock", args=[self.question.id])
        self.assertEqual(405, response.status_code)

    def test_lock_question_with_permissions_POST(self):
        """Locking questions with permissions via HTTP POST."""
        u = UserFactory()
        add_permission(u, Question, "lock_question")
        self.client.login(username=u.username, password="testpass")
        q = self.question
        response = post(self.client, "questions.lock", args=[q.id])
        self.assertEqual(200, response.status_code)
        self.assertEqual(True, Question.objects.get(pk=q.pk).is_locked)
        assert b"This thread was closed." in response.content

        # now unlock it
        response = post(self.client, "questions.lock", args=[q.id])
        self.assertEqual(200, response.status_code)
        self.assertEqual(False, Question.objects.get(pk=q.pk).is_locked)

    def test_reply_to_locked_question(self):
        """Locked questions can't be answered."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")

        # Without add_answer permission, we should 403.
        q = self.question
        q.is_locked = True
        q.save()
        response = post(self.client, "questions.reply", {"content": "just testing"}, args=[q.id])
        self.assertEqual(403, response.status_code)

        # With add_answer permission, it should work.
        add_permission(u, Answer, "add_answer")
        response = post(self.client, "questions.reply", {"content": "just testing"}, args=[q.id])
        self.assertEqual(200, response.status_code)

    def test_edit_answer_locked_question(self):
        """Verify edit answer of a locked question only with permissions."""
        self.question.is_locked = True
        self.question.save()

        # The answer creator can't edit if question is locked
        u = self.question.last_answer.creator
        self.client.login(username=u.username, password="testpass")

        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(0, len(doc("li.edit")))

        answer = self.question.last_answer
        response = get(self.client, "questions.edit_answer", args=[self.question.id, answer.id])
        self.assertEqual(403, response.status_code)

        # A user with edit_answer permission can edit.
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        add_permission(u, Answer, "change_answer")

        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual(1, len(doc("li.edit")))

        answer = self.question.last_answer
        response = get(self.client, "questions.edit_answer", args=[self.question.id, answer.id])
        self.assertEqual(200, response.status_code)

        content = "New content for answer"
        response = post(
            self.client,
            "questions.edit_answer",
            {"content": content},
            args=[self.question.id, answer.id],
        )
        self.assertEqual(content, Answer.objects.get(pk=answer.id).content)

    def test_vote_locked_question_403(self):
        """Locked questions can't be voted on."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")

        q = self.question
        q.is_locked = True
        q.save()
        response = post(self.client, "questions.vote", args=[q.id])
        self.assertEqual(403, response.status_code)

    def test_vote_answer_to_locked_question_403(self):
        """Answers to locked questions can't be voted on."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")

        q = self.question
        q.is_locked = True
        q.save()
        response = post(
            self.client,
            "questions.answer_vote",
            {"helpful": "y"},
            args=[q.id, self.answer.id],
        )
        self.assertEqual(403, response.status_code)

    def test_watch_GET_405(self):
        """Watch replies with HTTP GET results in 405."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.watch", args=[self.question.id])
        self.assertEqual(405, response.status_code)

    def test_unwatch_GET_405(self):
        """Unwatch replies with HTTP GET results in 405."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        response = get(self.client, "questions.unwatch", args=[self.question.id])
        self.assertEqual(405, response.status_code)

    def test_watch_replies(self):
        """Watch a question for replies."""
        self.client.logout()

        # Delete existing watches
        Watch.objects.all().delete()

        post(
            self.client,
            "questions.watch",
            {"email": "some@bo.dy", "event_type": "reply"},
            args=[self.question.id],
        )
        assert QuestionReplyEvent.is_notifying(
            "some@bo.dy", self.question
        ), "Watch was not created"

        attrs_eq(
            mail.outbox[0],
            to=["some@bo.dy"],
            subject="Please confirm your email address",
        )
        assert "questions/confirm/" in mail.outbox[0].body
        assert "New answers" in mail.outbox[0].body

        # Now activate the watch.
        w = Watch.objects.get()
        get(self.client, "questions.activate_watch", args=[w.id, w.secret])
        assert Watch.objects.get(id=w.id).is_active

    @mock.patch.object(mail.EmailMessage, "send")
    def test_watch_replies_smtp_error(self, emailmessage_send):
        """Watch a question for replies and fail to send email."""
        emailmessage_send.side_effect = emailmessage_raise_smtp
        self.client.logout()

        r = post(
            self.client,
            "questions.watch",
            {"email": "some@bo.dy", "event_type": "reply"},
            args=[self.question.id],
        )
        assert not QuestionReplyEvent.is_notifying(
            "some@bo.dy", self.question
        ), "Watch was created"
        self.assertContains(r, "Could not send a message to that email")

    def test_watch_replies_wrong_secret(self):
        """Watch a question for replies."""
        # This also covers test_watch_solution_wrong_secret.
        self.client.logout()

        # Delete existing watches
        Watch.objects.all().delete()

        post(
            self.client,
            "questions.watch",
            {"email": "some@bo.dy", "event_type": "reply"},
            args=[self.question.id],
        )

        # Now activate the watch.
        w = Watch.objects.get()
        r = get(self.client, "questions.activate_watch", args=[w.id, "fail"])
        self.assertEqual(200, r.status_code)
        assert not Watch.objects.get(id=w.id).is_active

    def test_watch_replies_logged_in(self):
        """Watch a question for replies (logged in)."""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        u = User.objects.get(username=u.username)
        post(
            self.client,
            "questions.watch",
            {"event_type": "reply"},
            args=[self.question.id],
        )
        assert QuestionReplyEvent.is_notifying(u, self.question), "Watch was not created"
        return u

    def test_watch_solution(self):
        """Watch a question for solution."""
        self.client.logout()

        # Delete existing watches
        Watch.objects.all().delete()

        post(
            self.client,
            "questions.watch",
            {"email": "some@bo.dy", "event_type": "solution"},
            args=[self.question.id],
        )
        assert QuestionSolvedEvent.is_notifying(
            "some@bo.dy", self.question
        ), "Watch was not created"

        attrs_eq(
            mail.outbox[0],
            to=["some@bo.dy"],
            subject="Please confirm your email address",
        )
        assert "questions/confirm/" in mail.outbox[0].body
        assert "Solution found" in mail.outbox[0].body

        # Now activate the watch.
        w = Watch.objects.get()
        get(self.client, "questions.activate_watch", args=[w.id, w.secret])
        assert Watch.objects.get().is_active

    def test_unwatch(self):
        """Unwatch a question."""
        # First watch question.
        u = self.test_watch_replies_logged_in()
        # Then unwatch it.
        self.client.login(username=u.username, password="testpass")
        post(self.client, "questions.unwatch", args=[self.question.id])
        assert not QuestionReplyEvent.is_notifying(u, self.question), "Watch was not destroyed"

    def test_watch_solution_and_replies(self):
        """User subscribes to solution and replies: page doesn't break"""
        u = UserFactory()
        self.client.login(username=u.username, password="testpass")
        QuestionReplyEvent.notify(u, self.question)
        QuestionSolvedEvent.notify(u, self.question)
        response = get(self.client, "questions.details", args=[self.question.id])
        self.assertEqual(200, response.status_code)

    def test_preview_answer(self):
        """Preview an answer."""
        num_answers = self.question.answers.count()
        content = "Awesome answer."
        response = post(
            self.client,
            "questions.reply",
            {"content": content, "preview": "any string"},
            args=[self.question.id],
        )
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(content, doc("#answer-preview div.content").text())
        self.assertEqual(num_answers, self.question.answers.count())

    def test_preview_answer_as_admin(self):
        """Preview an answer as admin and verify response is 200."""
        u = UserFactory(is_staff=True, is_superuser=True)
        self.client.login(username=u.username, password="testpass")
        content = "Awesome answer."
        response = post(
            self.client,
            "questions.reply",
            {"content": content, "preview": "any string"},
            args=[self.question.id],
        )
        self.assertEqual(200, response.status_code)

    def test_links_nofollow(self):
        """Links posted in questions and answers should have rel=nofollow."""
        q = self.question
        q.content = "lorem http://ipsum.com"
        q.save()
        a = self.answer
        a.content = "testing http://example.com"
        a.save()
        response = get(self.client, "questions.details", args=[self.question.id])
        doc = pq(response.content)
        self.assertEqual("nofollow", doc(".question .main-content a")[0].attrib["rel"])
        self.assertEqual("nofollow", doc(".answer .main-content a")[0].attrib["rel"])

    def test_robots_noindex_unsolved(self):
        """Verify noindex on unsolved questions."""
        q = QuestionFactory()

        # A brand new questions should be noindexed...
        response = get(self.client, "questions.details", args=[q.id])
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(1, len(doc("meta[name=robots]")))

        # If it has one answer, it should still be noindexed...
        a = AnswerFactory(question=q)
        response = get(self.client, "questions.details", args=[q.id])
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(1, len(doc("meta[name=robots]")))

        # If the answer is the solution, then it shouldn't be noindexed
        # anymore.
        q.solution = a
        q.save()
        response = get(self.client, "questions.details", args=[q.id])
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(0, len(doc("meta[name=robots]")))


class TaggingViewTestsAsTagger(TestCase):
    """Tests for views that add and remove tags, logged in as someone who can
    add and remove but not create tags

    Also hits the tag-related parts of the answer template.

    """

    def setUp(self):
        super().setUp()

        u = UserFactory()
        add_permission(u, Question, "tag_question")
        self.client.login(username=u.username, password="testpass")

        self.question = QuestionFactory()

    # add_tag view:

    def test_add_tag_get_method(self):
        """Assert GETting the add_tag view redirects to the answers page."""
        response = self.client.get(_add_tag_url(self.question.id))
        url = reverse(
            "questions.details",
            kwargs={"question_id": self.question.id},
        )
        self.assertRedirects(response, url)

    def test_add_nonexistent_tag(self):
        """Assert adding a nonexistent tag sychronously shows an error."""
        response = self.client.post(
            _add_tag_url(self.question.id), data={"tag-name": "nonexistent tag"}
        )
        self.assertContains(response, UNAPPROVED_TAG)

    def test_add_existent_tag(self):
        """Test adding a tag, case insensitivity, and space stripping."""
        TagFactory(name="PURplepurplepurple", slug="purplepurplepurple")
        response = self.client.post(
            _add_tag_url(self.question.id),
            data={"tag-name": " PURplepurplepurple "},
            follow=True,
        )
        self.assertContains(response, "purplepurplepurple")

    def test_add_no_tag(self):
        """Make sure adding a blank tag shows an error message."""
        response = self.client.post(_add_tag_url(self.question.id), data={"tag-name": ""})
        self.assertContains(response, NO_TAG)

    # add_tag_async view:

    def test_add_async_nonexistent_tag(self):
        """Assert adding an nonexistent tag yields an AJAX error."""
        response = self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": "nonexistent tag"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertContains(response, UNAPPROVED_TAG, status_code=400)

    def test_add_async_existent_tag(self):
        """Assert adding an unapplied tag."""
        TagFactory(name="purplepurplepurple", slug="purplepurplepurple")
        response = self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": " PURplepurplepurple "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertContains(response, "canonicalName")
        tags = Question.objects.get(id=self.question.id).tags.all()

        # Test the backend since we don't have a newly rendered page to
        # rely on.
        self.assertEqual([t.name for t in tags], ["purplepurplepurple"])

    def test_add_async_no_tag(self):
        """Assert adding an empty tag asynchronously yields an AJAX error."""
        response = self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertContains(response, NO_TAG, status_code=400)

    # remove_tag view:

    def test_remove_applied_tag(self):
        """Assert removing an applied tag succeeds."""
        self.question.tags.add("green")
        self.question.tags.add("colorless")
        response = self.client.post(
            _remove_tag_url(self.question.id), data={"remove-tag-colorless": "dummy"}
        )
        self._assert_redirects_to_question(response, self.question.id)
        tags = Question.objects.get(pk=self.question.id).tags.all()
        self.assertEqual([t.name for t in tags], ["green"])

    def test_remove_unapplied_tag(self):
        """Test removing an unapplied tag fails silently."""
        response = self.client.post(
            _remove_tag_url(self.question.id), data={"remove-tag-lemon": "dummy"}
        )
        self._assert_redirects_to_question(response, self.question.id)

    def test_remove_no_tag(self):
        """Make sure removing with no params provided redirects harmlessly."""
        response = self.client.post(_remove_tag_url(self.question.id), data={})
        self._assert_redirects_to_question(response, self.question.id)

    def _assert_redirects_to_question(self, response, question_id):
        url = reverse("questions.details", kwargs={"question_id": question_id})
        self.assertRedirects(response, url)

    # remove_tag_async view:

    def test_remove_async_applied_tag(self):
        """Assert taking a tag off a question works."""
        self.question.tags.add("green")
        self.question.tags.add("colorless")
        response = self.client.post(
            _remove_async_tag_url(self.question.id),
            data={"name": "colorless"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        tags = Question.objects.get(pk=self.question.id).tags.all()
        self.assertEqual([t.name for t in tags], ["green"])

    def test_remove_async_unapplied_tag(self):
        """Assert trying to remove a tag that isn't there succeeds."""
        response = self.client.post(
            _remove_async_tag_url(self.question.id),
            data={"name": "lemon"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)

    def test_remove_async_no_tag(self):
        """Assert calling the remove handler with no param fails."""
        response = self.client.post(
            _remove_async_tag_url(self.question.id),
            data={},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertContains(response, NO_TAG, status_code=400)


class TaggingViewTestsAsAdmin(TestCase):
    """Tests for views that create new tags, logged in as someone who can"""

    def setUp(self):
        super().setUp()

        u = UserFactory()
        add_permission(u, Question, "tag_question")
        add_permission(u, SumoTag, "add_tag")
        self.client.login(username=u.username, password="testpass")

        self.question = QuestionFactory()
        TagFactory(name="red", slug="red")

    def test_add_async_new_tag_permission_error(self):
        """Assert adding an nonexistent tag returns a permission error."""
        response = self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": "nonexistent tag"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 400)

    def test_add_new_case_insensitive(self):
        """Adding a tag differing only in case from existing ones shouldn't
        create a new tag."""
        self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": "RED"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        tags_eq(Question.objects.get(id=self.question.id), ["red"])

    def test_add_new_canonicalizes(self):
        """Adding a new tag as an admin should still canonicalize case."""
        response = self.client.post(
            _add_async_tag_url(self.question.id),
            data={"tag-name": "RED"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(json.loads(response.content)["canonicalName"], "red")


def _add_tag_url(question_id):
    """Return the URL to add_tag for question 1, an untagged question."""
    return reverse("questions.add_tag", kwargs={"question_id": question_id})


def _add_async_tag_url(question_id):
    """Return the URL to add_tag_async for question 1, an untagged question."""
    return reverse("questions.add_tag_async", kwargs={"question_id": question_id})


def _remove_tag_url(question_id):
    """Return  URL to remove_tag for question 2, tagged {colorless, green}."""
    return reverse("questions.remove_tag", kwargs={"question_id": question_id})


def _remove_async_tag_url(question_id):
    """Return URL to remove_tag_async on q. 2, tagged {colorless, green}."""
    return reverse("questions.remove_tag_async", kwargs={"question_id": question_id})


class QuestionsTemplateTestCase(TestCase):
    def test_tagged(self):
        u = UserFactory()
        add_permission(u, Question, "tag_question")
        tagname = "mobile"
        TagFactory(name=tagname, slug=tagname)
        self.client.login(username=u.username, password="testpass")
        tagged = urlparams(reverse("questions.list", args=["all"]), tagged=tagname, show="all")

        # First there should be no questions tagged 'mobile'
        response = self.client.get(tagged)
        doc = pq(response.content)
        self.assertEqual(0, len(doc(".forum--question-item")))

        # Tag a question 'mobile'
        q = QuestionFactory()
        response = post(self.client, "questions.add_tag", {"tag-name": tagname}, args=[q.id])
        self.assertEqual(200, response.status_code)

        # Add an answer
        AnswerFactory(question=q)

        # Now there should be 1 question tagged 'mobile'
        response = self.client.get(tagged)
        doc = pq(response.content)
        self.assertEqual(1, len(doc(".forum--question-item")))
        self.assertEqual(
            "{}/en-US/questions/all?tagged=mobile&show=all".format(settings.CANONICAL_URL),
            doc('link[rel="canonical"]')[0].attrib["href"],
        )

        # Test a tag that doesn't exist. It shouldnt blow up.
        url = urlparams(
            reverse("questions.list", args=["all"]), tagged="garbage-plate", show="all"
        )
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)

    def test_owner_tab_selected_in_list(self):
        # Test one tab is selected for no show arg specified
        questions_list = urlparams(reverse("questions.list", args=["all"]))
        response = self.client.get(questions_list)
        doc = pq(response.content)
        self.assertEqual(1, len(doc("#owner-tabs .selected")))

        # Test one tab is selected for all show args
        show_args = ["needs-attention", "responded", "done", "all"]
        for show_arg in show_args:
            questions_list = urlparams(reverse("questions.list", args=["all"]), show=show_arg)
            response = self.client.get(questions_list)
            doc = pq(response.content)
            self.assertEqual(1, len(doc("#owner-tabs .selected")))

    def test_product_filter(self):
        p1 = ProductFactory()
        p2 = ProductFactory()
        p3 = ProductFactory()

        q1 = QuestionFactory()
        q2 = QuestionFactory(product=p1)
        q2.save()
        q3 = QuestionFactory(product=p2)
        q3.save()

        def check(product, expected):
            url = reverse("questions.list", args=[product])
            response = self.client.get(url)
            doc = pq(response.content)
            # Make sure all questions are there.

            # This won't work, because the test case base adds more tests than
            # we expect in it's setUp(). TODO: Fix that.
            self.assertEqual(len(expected), len(doc(".forum--question-item")))

            for q in expected:
                self.assertEqual(1, len(doc(".forum--question-item[id=question-{}]".format(q.id))))

        # No filtering -> All questions.
        check("all", [q1, q2, q3])
        # Filter on p1 -> only q2
        check(p1.slug, [q2])
        # Filter on p2 -> only q3
        check(p2.slug, [q3])
        # Filter on p3 -> No results
        check(p3.slug, [])
        # Filter on p1,p2
        check("{},{}".format(p1.slug, p2.slug), [q2, q3])
        # Filter on p1,p3
        check("{},{}".format(p1.slug, p3.slug), [q2])
        # Filter on p2,p3
        check("{},{}".format(p2.slug, p3.slug), [q3])

    def test_topic_filter(self):
        p = ProductFactory()
        t1 = TopicFactory()
        t2 = TopicFactory()
        t3 = TopicFactory()
        t1.products.add(p)
        t2.products.add(p)
        t3.products.add(p)

        q1 = QuestionFactory(product=p)
        q2 = QuestionFactory(topic=t1, product=p)
        q3 = QuestionFactory(topic=t2, product=p)

        url = reverse("questions.list", args=["all"])

        def check(filter, expected):
            response = self.client.get(urlparams(url, **filter))
            doc = pq(response.content)
            # Make sure all questions are there.

            # This won't work, because the test case base adds more tests than
            # we expect in it's setUp(). TODO: Fix that.
            # self.assertEqual(len(expected), len(doc('.forum--question-item')))

            for q in expected:
                self.assertEqual(1, len(doc(".forum--question-item[id=question-{}]".format(q.id))))

        # No filtering -> All questions.
        check({}, [q1, q2, q3])
        # Filter on p1 -> only q2
        check({"topic": t1.slug}, [q2])
        # Filter on p2 -> only q3
        check({"topic": t2.slug}, [q3])
        # Filter on p3 -> No results
        check({"topic": t3.slug}, [])

    def test_robots_noindex(self):
        """Verify the page is set for noindex by robots."""
        response = self.client.get(reverse("questions.list", args=["all"]))
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(1, len(doc("meta[name=robots]")))

    def test_select_in_question(self):
        """Verify we properly escape <select/>."""
        QuestionFactory(
            title="test question lorem ipsum <select></select>",
            content="test question content lorem ipsum <select></select>",
        )
        response = self.client.get(reverse("questions.list", args=["all"]))
        assert b"test question lorem ipsum" in response.content
        assert b"test question content lorem ipsum" in response.content
        doc = pq(response.content)
        self.assertEqual(0, len(doc("article.questions select")))

    def test_truncated_text_is_stripped(self):
        """Verify we strip html from truncated text."""
        long_str = "".join(random.choice(ascii_letters) for x in range(170))
        QuestionFactory(content="<p>{}</p>".format(long_str))
        response = self.client.get(reverse("questions.list", args=["all"]))

        # Verify that the <p> was stripped
        assert b'<p class="short-text"><p>' not in response.content
        assert b'<p class="short-text">%s' % long_str[:5].encode() in response.content

    def test_views(self):
        """Verify the view count is displayed correctly."""
        q = QuestionFactory()
        q.questionvisits_set.create(visits=1007)
        response = self.client.get(reverse("questions.list", args=["all"]))
        doc = pq(response.content)
        self.assertEqual("1007", doc(".views-val").text())

    def test_no_unarchive_on_old_questions(self):
        ques = QuestionFactory(created=(datetime.now() - timedelta(days=200)), is_archived=True)
        response = get(self.client, "questions.details", args=[ques.id])
        assert b"Archive this post" not in response.content

    def test_show_is_empty_string_doesnt_500(self):
        QuestionFactory()
        response = self.client.get(urlparams(reverse("questions.list", args=["all"]), show=""))
        self.assertEqual(200, response.status_code)


class QuestionsTemplateTestCaseNoFixtures(TestCase):
    def test_locked_questions_dont_appear(self):
        """Locked questions are not listed on the no-replies list."""
        p = ProductFactory()
        QuestionFactory(product=p)
        QuestionFactory(product=p)
        QuestionFactory(product=p, is_locked=True)

        url = reverse("questions.list", args=["all"])
        url = urlparams(url, filter="no-replies")
        response = self.client.get(url)
        doc = pq(response.content)
        self.assertEqual(2, len(doc(".forum--question-item")))


class QuestionEditingTests(TestCase):
    """Tests for the question-editing view and templates"""

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        add_permission(self.user, Question, "change_question")
        self.client.login(username=self.user.username, password="testpass")

    def test_extra_fields(self):
        """The edit-question form should show appropriate metadata fields."""
        product = ProductFactory()
        question_id = QuestionFactory(product=product).id
        AAQConfigFactory(product=product)
        response = get(self.client, "questions.edit_question", kwargs={"question_id": question_id})
        self.assertEqual(response.status_code, 200)

        # Make sure each extra metadata field is in the form:
        doc = pq(response.content)
        q = Question.objects.get(pk=question_id)
        extra_fields = q.product_config.extra_fields
        for field in extra_fields:
            assert doc("input[name={}]".format(field)) or doc("textarea[name={}]".format(field)), (
                "The {} field is missing from the edit page.".format(field)
            )

    def test_no_extra_fields(self):
        """The edit-question form shouldn't show inappropriate metadata."""
        question_id = QuestionFactory().id
        response = get(self.client, "questions.edit_question", kwargs={"question_id": question_id})
        self.assertEqual(response.status_code, 200)

        # Take the "os" field as representative. Make sure it doesn't show up:
        doc = pq(response.content)
        assert not doc("input[name=os]")

    def test_post(self):
        """Posting a valid edit form should save the question."""
        p = ProductFactory(slug="firefox")
        q = QuestionFactory(product=p)
        AAQConfigFactory(product=p)
        response = post(
            self.client,
            "questions.edit_question",
            {
                "title": "New title",
                "content": "New content",
                "ff_version": "New version",
            },
            kwargs={"question_id": q.id},
        )

        # Make sure the form redirects and thus appears to succeed:
        url = reverse("questions.details", kwargs={"question_id": q.id})
        self.assertRedirects(response, url)

        # Make sure the static fields, the metadata, and the updated_by field
        # changed:
        q = Question.objects.get(pk=q.id)
        self.assertEqual(q.title, "New title")
        self.assertEqual(q.content, "New content")
        self.assertEqual(q.updated_by, self.user)


class AAQTemplateTestCase(TestCase):
    """Test the AAQ template."""

    data = {
        "title": "A test question",
        "content": "I have this question that I hope...",
        "category": "troubleshooting",
        "ff_version": "3.6.6",
        "os": "Intel Mac OS X 10.6",
        "plugins": "* Shockwave Flash 10.1 r53",
        "useragent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.8.3) "
        "Gecko/20120221 Firefox/18.0",
        "troubleshooting": """{
                "accessibility": {
                    "isActive": true
                },
                "application": {
                    "name": "Firefox",
                    "supportURL": "Some random url.",
                    "userAgent": "A user agent.",
                    "version": "18.0.2"
                },
                "extensions": [],
                "graphics": {},
                "javaScript": {},
                "modifiedPreferences": {
                    "print.macosx.pagesetup": "QWERTY",
                    "print.macosx.pagesetup-2": "POIUYT"
                },
                "userJS": {
                    "exists": false
                }
            }""",
    }

    def setUp(self):
        super().setUp()

        self.user = UserFactory()
        self.product = ProductFactory(title="Firefox", slug="firefox")
        self.aaq_config = AAQConfigFactory(product=self.product, is_active=True)
        self.client.login(username=self.user.username, password="testpass")

    def _post_new_question(self, locale=None):
        """Post a new question and return the response."""
        for loc_code in (settings.LANGUAGE_CODE, "pt-BR"):
            loc, _ = QuestionLocale.objects.get_or_create(locale=loc_code)
            self.aaq_config.enabled_locales.add(loc)
        topic = TopicFactory(
            title="Troubleshooting", slug="troubleshooting", products=[self.product], in_aaq=True
        )
        self.data["category"] = topic.id
        extra = {}
        if locale is not None:
            q_loc, _ = QuestionLocale.objects.get_or_create(locale=locale)
            self.aaq_config.enabled_locales.add(q_loc)
            extra["locale"] = locale
        url = urlparams(reverse("questions.aaq_step3", args=["firefox"], **extra))

        # Set 'in-aaq' for the session. It isn't already set because this
        # test doesn't do a GET of the form first.
        self.client.session["in-aaq"] = True
        self.client.session.save()

        return self.client.post(url, self.data, follow=True)

    def test_full_workflow(self):
        self.aaq_config.extra_fields = ["troubleshooting"]
        self.aaq_config.save()
        response = self._post_new_question()
        self.assertEqual(200, response.status_code)
        assert "Done!" in pq(response.content)("ul.user-messages li").text()

        # Verify question is in db now
        question = Question.objects.filter(title="A test question")[0]

        # Make sure question is in questions list
        response = self.client.get(reverse("questions.list", args=["all"]))
        doc = pq(response.content)
        self.assertEqual(1, len(doc("#question-{}".format(question.id))))
        # And no email was sent
        self.assertEqual(0, len(mail.outbox))

        # Verify product and topic assigned to question.
        self.assertEqual("troubleshooting", question.topic.slug)
        self.assertEqual("firefox", question.product.slug)

        # Verify troubleshooting information
        troubleshooting = question.metadata["troubleshooting"]
        assert "modifiedPreferences" in troubleshooting
        assert "print.macosx" not in troubleshooting

        # Verify firefox version
        version = question.metadata["ff_version"]
        self.assertEqual("18.0.2", version)

    def test_full_workflow_inactive(self):
        """
        Test that an inactive user cannot create a new question
        """
        u = self.user
        u.is_active = False
        u.save()
        self._post_new_question()
        self.assertEqual(0, Question.objects.count())

    def test_localized_creation(self):
        response = self._post_new_question(locale="pt-BR")
        self.assertEqual(200, response.status_code)
        assert "Pronto!" in pq(response.content)("ul.user-messages li").text()

        # Verify question is in db now
        question = Question.objects.filter(title="A test question")[0]
        self.assertEqual(question.locale, "pt-BR")

    def test_invalid_product_404(self):
        url = reverse("questions.aaq_step2", args=["lipsum"])
        response = self.client.get(url)
        self.assertEqual(404, response.status_code)


class ProductForumTemplateTestCase(TestCase):
    def test_product_forum_listing(self):
        firefox = ProductFactory(title="Firefox", slug="firefox")
        android = ProductFactory(title="Firefox for Android", slug="mobile")
        mza = ProductFactory(title="Mozilla Account", slug="mozilla-account")

        lcl, _ = QuestionLocale.objects.get_or_create(locale=settings.LANGUAGE_CODE)
        AAQConfigFactory(product=firefox, is_active=True, enabled_locales=[lcl])
        AAQConfigFactory(product=android, is_active=True, enabled_locales=[lcl])

        response = self.client.get(reverse("questions.home"))
        self.assertEqual(200, response.status_code)
        doc = pq(response.content)
        self.assertEqual(2, len(doc(".product-list .product")))
        product_list_html = doc(".product-list").html()
        assert firefox.title in product_list_html
        assert android.title in product_list_html
        assert mza.title not in product_list_html
