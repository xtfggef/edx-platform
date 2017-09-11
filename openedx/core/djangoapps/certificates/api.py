"""
The public API for certificates.
"""
from datetime import datetime

from pytz import UTC

# this is really lms.djangoapps.certificates, but we can't refer to it like
# that here without raising a RuntimeError about Conflicting models
from certificates.models import GeneratedCertificate
from openedx.core.djangoapps.certificates.config import waffle
from student.models import CourseEnrollment


SWITCHES = waffle.waffle()


def auto_certificate_generation_enabled():
    return (
        SWITCHES.is_enabled(waffle.SELF_PACED_ONLY) or
        SWITCHES.is_enabled(waffle.INSTRUCTOR_PACED_ONLY)
    )


def auto_certificate_generation_enabled_for_course(course):
    if not auto_certificate_generation_enabled():
        return False

    if course.self_paced:
        if not SWITCHES.is_enabled(waffle.SELF_PACED_ONLY):
            return False
    else:
        if not SWITCHES.is_enabled(waffle.INSTRUCTOR_PACED_ONLY):
            return False

    return True


def _enabled_and_self_paced(course):
    if auto_certificate_generation_enabled_for_course(course):
        return not course.self_paced
    return False


def enrollment_is_verified(student, course):
    """
    Returns True if the student has a verified certificate enrollment in the course, False otherwise.
    """
    enrollment_mode, __ = CourseEnrollment.enrollment_mode_for_user(student, course.id)
    return enrollment_mode in GeneratedCertificate.VERIFIED_CERTS_MODES


def has_valid_certificate(student, course):
    """
    Returns True if the student has a valid, verified certificate for this course, False otherwise.
    """
    verified_enrollment = enrollment_is_verified(student, course)
    certificate = GeneratedCertificate.certificate_for_student(student, course.id)
    return certificate.is_valid()


def certificates_viewable_for_course(course):
    """
    Returns True if certificates are viewable for any student enrolled in the course, False otherwise.
    """
    if course.self_paced:
        return True
    if (
        course.certificates_display_behavior in ('early_with_info', 'early_no_info')
        or course.certificates_show_before_end
    ):
        return True
    if (
        course.certificate_available_date
        and course.certificate_available_date <= datetime.now(UTC)
    ):
        return True
    if (
        course.certificate_available_date is None
        and course.has_ended()
    ):
        return True
    return False


def can_show_view_certificate_button(student, course):
    """
    Returns True if the student can see the "View Certificate" button on their course progress page, False otherwise.
    """
    certificate_is_valid = has_valid_certificate(student, course)
    print 'certificate_is_valid:', certificate_is_valid
    if auto_certificate_generation_enabled():
        print 'switch enabled'
        print 'certificates_viewable_for_course:', certificates_viewable_for_course(course)
        return certificates_viewable_for_course(course) and certificate_is_valid
    return certificate_is_valid


def can_show_certificate_available_date_field(course):
    return _enabled_and_self_paced(course)
