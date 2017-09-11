"""
Django admin dashboard configuration for Video XModule.
"""

from config_models.admin import ConfigurationModelAdmin, KeyedConfigurationModelAdmin
from django.contrib import admin

from openedx.core.djangoapps.video_config.forms import (
    CourseHLSPlaybackFlagAdminForm, CourseVideoTranscriptFlagAdminForm
)
from openedx.core.djangoapps.video_config.models import (
    CourseHLSPlaybackEnabledFlag, HLSPlaybackEnabledFlag,
    CourseVideoTranscriptEnabledFlag, VideoTranscriptEnabledFlag
)


class CourseHLSPlaybackEnabledFlagAdmin(KeyedConfigurationModelAdmin):
    """
    Admin of HLS Playback feature on course-by-course basis.
    Allows searching by course id.
    """
    form = CourseHLSPlaybackFlagAdminForm
    search_fields = ['course_id']
    fieldsets = (
        (None, {
            'fields': ('course_id', 'enabled'),
            'description': 'Enter a valid course id. If it is invalid, an error message will be displayed.'
        }),
    )


class CourseVideoTranscriptEnabledFlagAdmin(KeyedConfigurationModelAdmin):
    """
    Admin of Video Transcript feature on course-by-course basis.
    Allows searching by course id.
    """
    form = CourseHLSPlaybackFlagAdminForm
    search_fields = ['course_id']
    fieldsets = (
        (None, {
            'fields': ('course_id', 'enabled'),
            'description': 'Enter a valid course id. If it is invalid, an error message will be displayed.'
        }),
    )

admin.site.register(HLSPlaybackEnabledFlag, ConfigurationModelAdmin)
admin.site.register(CourseHLSPlaybackEnabledFlag, CourseHLSPlaybackEnabledFlagAdmin)
admin.site.register(VideoTranscriptEnabledFlag, ConfigurationModelAdmin)
admin.site.register(CourseVideoTranscriptEnabledFlag, CourseHLSPlaybackEnabledFlagAdmin)
