import factory
from django.conf import settings
from django.core.files import File

from kitsune.questions.tests import QuestionFactory
from kitsune.sumo.tests import TestCase
from kitsune.upload.models import ImageAttachment
from kitsune.upload.storage import RenameFileStorage
from kitsune.upload.utils import FileTooLargeError, check_file_size, create_imageattachment
from kitsune.users.tests import UserFactory


class ImageAttachmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ImageAttachment

    creator = factory.SubFactory(UserFactory)
    content_object = factory.SubFactory(QuestionFactory)
    file = factory.django.FileField()


def check_file_info(file_info, name, width, height, delete_url, url, thumbnail_url):
    tc = TestCase()
    tc.assertEqual(name, file_info["name"])
    tc.assertEqual(width, file_info["width"])
    tc.assertEqual(height, file_info["height"])
    tc.assertEqual(delete_url, file_info["delete_url"])
    tc.assertEqual(url, file_info["url"])
    tc.assertEqual(thumbnail_url, file_info["thumbnail_url"])


def get_file_name(name):
    storage = RenameFileStorage()
    return storage.get_available_name(name)


class CheckFileSizeTestCase(TestCase):
    """Tests for check_file_size"""

    def test_check_file_size_under(self):
        """No exception should be raised"""
        with open("kitsune/upload/tests/media/test.jpg", "rb") as f:
            up_file = File(f)
            check_file_size(up_file, settings.IMAGE_MAX_FILESIZE)

    def test_check_file_size_over(self):
        """FileTooLargeError should be raised"""
        with self.assertRaises(FileTooLargeError):
            with open("kitsune/upload/tests/media/test.jpg", "rb") as f:
                up_file = File(f)
                # This should raise
                check_file_size(up_file, 0)


class CreateImageAttachmentTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = UserFactory()
        self.obj = QuestionFactory()

    def tearDown(self):
        ImageAttachment.objects.all().delete()
        super().tearDown()

    def test_create_imageattachment(self):
        """
        An image attachment is created from an uploaded file.

        Verifies all appropriate fields are correctly set.
        """
        with open("kitsune/upload/tests/media/test.jpg", "rb") as f:
            up_file = File(f)
            file_info = create_imageattachment({"image": up_file}, self.user, self.obj)

        image = ImageAttachment.objects.all()[0]
        check_file_info(
            file_info,
            name="test.png",
            width=90,
            height=120,
            delete_url=image.get_delete_url(),
            url=image.get_absolute_url(),
            thumbnail_url=image.thumbnail.url,
        )

    def test_create_imageattachment_when_animated(self):
        """
        An image attachment is created from an uploaded animated GIF file.

        Verifies all appropriate fields are correctly set.
        """
        filepath = "kitsune/upload/tests/media/animated.gif"
        with open(filepath, "rb") as f:
            up_file = File(f)
            file_info = create_imageattachment({"image": up_file}, self.user, self.obj)

        image = ImageAttachment.objects.all()[0]
        check_file_info(
            file_info,
            name=filepath,
            width=120,
            height=120,
            delete_url=image.get_delete_url(),
            url=image.get_absolute_url(),
            thumbnail_url=image.thumbnail.url,
        )


class FileNameTestCase(TestCase):
    def _match_file_name(self, name, name_end):
        assert name.endswith(name_end), '"{}" does not end with "{}"'.format(name, name_end)

    def test_empty_file_name(self):
        self._match_file_name("", "")

    def test_empty_file_name_with_extension(self):
        self._match_file_name(get_file_name(".wtf"), "3f8242")

    def test_ascii(self):
        self._match_file_name(get_file_name("some ascii.jpg"), "5959e0.jpg")

    def test_ascii_dir(self):
        self._match_file_name(get_file_name("dir1/dir2/some ascii.jpg"), "5959e0.jpg")

    def test_low_unicode(self):
        self._match_file_name(get_file_name("157d9383e6aeba7180378fd8c1d46f80.gif"), "bdaf1a.gif")

    def test_high_unicode(self):
        self._match_file_name(get_file_name("\u6709\u52b9.jpeg"), "ce1518.jpeg")

    def test_full_mixed(self):
        self._match_file_name(
            get_file_name("123\xe5\xe5\xee\xe9\xf8\xe7\u6709\u52b9.png"), "686c11.png"
        )
