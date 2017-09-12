from unittest import TestCase

import ddt
from mock import patch
from opaque_keys.edx.keys import CourseKey
from nose.tools import assert_true

from lms.djangoapps.certificates.tasks import generate_certificate
from lms.djangoapps.verify_student.tests.factories import SoftwareSecurePhotoVerificationFactory
from student.tests.factories import UserFactory


@ddt.ddt
class GenerateUserCertificateTest(TestCase):
    @patch('lms.djangoapps.certificates.tasks.generate_user_certificates')
    @patch('lms.djangoapps.certificates.tasks.User.objects.get')
    def test_cert_task(self, user_get_mock, generate_user_certs_mock):
        course_key = 'course-v1:edX+CS101+2017_T2'

        generate_certificate(student='student-id', course_key=course_key, otherarg='c', otherotherarg='d')

        expected_student = user_get_mock.return_value
        generate_user_certs_mock.assert_called_with(
            student=expected_student,
            course_key=CourseKey.from_string(course_key),
            otherarg='c',
            otherotherarg='d'
        )
        user_get_mock.assert_called_once_with(id='student-id')

    @ddt.data('student', 'course_key')
    def test_cert_task_missing_args(self, missing_arg):
        kwargs = {'student': 'a', 'course_key': 'b', 'otherarg': 'c'}
        del kwargs[missing_arg]

        with patch('lms.djangoapps.certificates.tasks.User.objects.get'):
            with self.assertRaisesRegexp(KeyError, missing_arg):
                generate_certificate(**kwargs)

    @patch('lms.djangoapps.certificates.tasks.generate_certificate.retry')
    def test_cert_task_retry(self, mock_retry):
        course_key = 'course-v1:edX+CS101+2017_T2'
        student = UserFactory()

        generate_certificate(student=student.id, course_key=course_key, expected_verification_status='approved')

        SoftwareSecurePhotoVerificationFactory.create(user=student, status='approved')

        self.assertTrue(mock_retry.called)
