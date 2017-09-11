from contextlib import contextmanager
import itertools
from unittest import TestCase

import ddt
import waffle

from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
# this is really lms.djangoapps.certificates, but we can't refer to it like
# that here without raising a RuntimeError about Conflicting models
from certificates.models import CertificateStatuses, GeneratedCertificate
from course_modes.models import CourseMode
from openedx.core.djangoapps.certificates import api
from openedx.core.djangoapps.certificates.config import waffle as certs_waffle
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from student.tests.factories import CourseEnrollmentFactory, UserFactory


@contextmanager
def configure_waffle_namespace(self_paced_enabled, instructor_paced_enabled):
    namespace = certs_waffle.waffle()

    with namespace.override(certs_waffle.SELF_PACED_ONLY, active=self_paced_enabled):
        with namespace.override(certs_waffle.INSTRUCTOR_PACED_ONLY, active=instructor_paced_enabled):
            yield


class CertificatesApiBaseTestCase(TestCase):
    def setUp(self):
        super(CertificatesApiBaseTestCase, self).setUp()
        self.course = CourseOverviewFactory.create()

    def tearDown(self):
        super(CertificatesApiBaseTestCase, self).tearDown()
        self.course.self_paced = False


@ddt.ddt
class FeatureEnabledTestCase(CertificatesApiBaseTestCase):
    @ddt.data(*itertools.product((True, False), (True, False)))
    @ddt.unpack
    def test_auto_certificate_generation_enabled(self, self_paced_enabled, instructor_paced_enabled):
        expected_value = self_paced_enabled or instructor_paced_enabled
        with configure_waffle_namespace(self_paced_enabled, instructor_paced_enabled):
            self.assertEqual(expected_value, api.auto_certificate_generation_enabled())

    @ddt.data(
        (False, False, True, False),  # feature not enabled should return False
        (False, True, True, False),  # self-paced feature enabled and self-paced course should return False
        (True, False, True, True),  # self-paced feature enabled and self-paced course should return True
        (True, False, False, False),  # instructor-paced feature enabled and self-paced course should return False
        (False, True, False, True)  # instructor-paced feature enabled and instructor-paced course should return True
    )
    @ddt.unpack
    def test_auto_certificate_generation_enabled_for_course(
            self, self_paced_enabled, instructor_paced_enabled, is_self_paced, expected_value
    ):
        self.course.self_paced = is_self_paced
        with configure_waffle_namespace(self_paced_enabled, instructor_paced_enabled):
            self.assertEqual(expected_value, api.auto_certificate_generation_enabled_for_course(self.course))


@ddt.ddt
class VisibilityTestCase(CertificatesApiBaseTestCase):
    def setUp(self):
        super(VisibilityTestCase, self).setUp()
        self.user = UserFactory.create()
        self.enrollment = CourseEnrollmentFactory(
            user=self.user,
            course_id=self.course.id,
            is_active=True,
            mode='audit',
        )

    @ddt.data(
        (True, False, True, False),  # feature enabled and self-paced should return False
        (False, True, False, True),  # feature enabled and instructor-paced should return True
        (False, False, True, False),  # feature not enabled and self-paced should return False
        (False, False, False, False),  # feature not enabled and instructor-paced should return False
    )
    @ddt.unpack
    def test_can_show_certificate_available_date_field(
            self, self_paced_enabled, instructor_paced_enabled, is_self_paced, expected_value
    ):
        self.course.self_paced = is_self_paced
        with configure_waffle_namespace(self_paced_enabled, instructor_paced_enabled):
            self.assertEqual(expected_value, api.can_show_certificate_available_date_field(self.course))

    @ddt.data(
        (CourseMode.HONOR, CertificateStatuses.downloadable, True, True),
        (CourseMode.VERIFIED, CertificateStatuses.downloadable, True, True),
        (CourseMode.HONOR, CertificateStatuses.unverified, True, False),
        (CourseMode.VERIFIED, CertificateStatuses.unverified, True, False),
        (CourseMode.AUDIT, CertificateStatuses.auditing, True, False),
        (CourseMode.HONOR, CertificateStatuses.downloadable, False, False),
        (CourseMode.VERIFIED, CertificateStatuses.downloadable, False, False),
        (CourseMode.HONOR, CertificateStatuses.unverified, False, False),
        (CourseMode.VERIFIED, CertificateStatuses.unverified, False, False),
        (CourseMode.AUDIT, CertificateStatuses.auditing, False, False),
    )
    @ddt.unpack
    def test_can_show_view_certificate_button_feature_enabled_toggle_pacing(
            self, enrollment_mode, certificate_status, self_paced, expected_value
    ):
        self.enrollment.mode = enrollment_mode
        self.enrollment.save()

        self.course.self_paced = self_paced
        self.course.save()

        certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            grade='1.0',
            status=certificate_status,
            mode=enrollment_mode,
        )

        with configure_waffle_namespace(True, True):
            self.assertEquals(expected_value, api.can_show_view_certificate_button(self.user, self.course))
