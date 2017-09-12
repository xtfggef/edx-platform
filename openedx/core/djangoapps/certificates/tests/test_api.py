from contextlib import contextmanager
from datetime import datetime, timedelta
import itertools
from unittest import TestCase

import ddt
from freezegun import freeze_time
import pytz
import waffle

from lms.djangoapps.certificates.tests.factories import GeneratedCertificateFactory
# this is really lms.djangoapps.certificates, but we can't refer to it like
# that here without raising a RuntimeError about Conflicting models
from certificates.models import CertificateStatuses
from course_modes.models import CourseMode
from openedx.core.djangoapps.certificates import api
from openedx.core.djangoapps.certificates.config import waffle as certs_waffle
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from student.tests.factories import CourseEnrollmentFactory, UserFactory


def days(n):
    return timedelta(days=n)


@contextmanager
def configure_waffle_namespace(self_paced_enabled, instructor_paced_enabled):
    namespace = certs_waffle.waffle()

    with namespace.override(certs_waffle.SELF_PACED_ONLY, active=self_paced_enabled):
        with namespace.override(certs_waffle.INSTRUCTOR_PACED_ONLY, active=instructor_paced_enabled):
            yield


class CertificatesApiBaseTestCase(TestCase):
    def setUp(self):
        super(CertificatesApiBaseTestCase, self).setUp()
        self.course = CourseOverviewFactory.create(
            start = datetime(2017, 1, 1, tzinfo=pytz.UTC),
            end = datetime(2017, 1, 31, tzinfo=pytz.UTC),
            certificate_available_date=None
        )

    def tearDown(self):
        super(CertificatesApiBaseTestCase, self).tearDown()
        self.course.self_paced = False
        self.course.certificate_available_date = None


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
        self.certificate = GeneratedCertificateFactory.create(
            user=self.user,
            course_id=self.course.id,
            grade='1.0',
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
        (CourseMode.CREDIT_MODE, True, True),
        (CourseMode.VERIFIED, True, True),
        (CourseMode.AUDIT, True, False),
        (CourseMode.CREDIT_MODE, False, True),
        (CourseMode.VERIFIED, False, True),
        (CourseMode.AUDIT, False, False),
    )
    @ddt.unpack
    def test_can_show_view_certificate_button_self_paced(
            self, enrollment_mode, feature_enabled, expected_value_if_downloadable
    ):
        self.enrollment.mode = enrollment_mode
        self.enrollment.save()

        self.course.self_paced = True
        self.course.save()

        for certificate_status in CertificateStatuses.ALL_STATUSES:
            self.certificate.mode = enrollment_mode
            self.certificate.status = certificate_status

            expected_to_view = (
                expected_value_if_downloadable and
                (certificate_status == CertificateStatuses.downloadable)
            )
            with configure_waffle_namespace(feature_enabled, feature_enabled):
                self.assertEquals(expected_to_view, api.can_show_view_certificate_button(self.course, self.certificate))

    @ddt.data(
        # null certificate_available_date, depend only on cert being valid
        (CourseMode.CREDIT_MODE, True, None, days(0), True),
        (CourseMode.VERIFIED, True, None, days(0), True),
        (CourseMode.AUDIT, True, None, days(0), False),
        # feature not enabled, so only depend on course having ended
        (CourseMode.CREDIT_MODE, False, None, days(1), True),
        (CourseMode.VERIFIED, False, None, days(1), True),
        (CourseMode.AUDIT, False, None, days(1), False),
    )
    @ddt.unpack
    def test_can_show_view_certificate_button_instructor_paced_course_ended(
            self, enrollment_mode, feature_enabled, cert_avail_delta, current_time_delta, expected_value_if_downloadable
    ):
        self.enrollment.mode = enrollment_mode
        self.enrollment.save()

        self.course.self_paced = False
        if cert_avail_delta:
            self.course.certificate_available_date = self.course.end + cert_avail_delta
        self.course.save()

        for certificate_status in CertificateStatuses.ALL_STATUSES:
            self.certificate.mode = enrollment_mode
            self.certificate.status = certificate_status

            expected_to_view = (
                expected_value_if_downloadable and
                (certificate_status == CertificateStatuses.downloadable)
            )
            with configure_waffle_namespace(feature_enabled, feature_enabled):
                with freeze_time(self.course.end + current_time_delta):
                    self.assertEquals(
                        expected_to_view,
                        api.can_show_view_certificate_button(self.course, self.certificate)
                    )
