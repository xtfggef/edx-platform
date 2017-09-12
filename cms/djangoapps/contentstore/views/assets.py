import json
import logging
import math
from functools import partial

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest, HttpResponseNotFound
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST
from opaque_keys.edx.keys import AssetKey, CourseKey
from pymongo import ASCENDING, DESCENDING

from contentstore.utils import reverse_course_url
from contentstore.views.exception import AssetNotFoundException
from edxmako.shortcuts import render_to_response
from openedx.core.djangoapps.contentserver.caching import del_cached_content
from student.auth import has_course_author_access
from util.date_utils import get_default_time_display
from util.json_request import JsonResponse
from xmodule.contentstore.content import StaticContent
from xmodule.contentstore.django import contentstore
from xmodule.exceptions import NotFoundError
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError

__all__ = ['assets_handler']

# pylint: disable=unused-argument

request_defaults = {
    'page': 0,
    'page_size': 50,
    'sort': 'date_added',
    'direction': '',
    'asset_type': ''
}

@login_required
@ensure_csrf_cookie
def assets_handler(request, course_key_string=None, asset_key_string=None):
    """
    The restful handler for assets.
    It allows retrieval of all the assets (as an HTML page), as well as uploading new assets,
    deleting assets, and changing the "locked" state of an asset.

    GET
        html: return an html page which will show all course assets. Note that only the asset container
            is returned and that the actual assets are filled in with a client-side request.
        json: returns a page of assets. The following parameters are supported:
            page: the desired page of results (defaults to 0)
            page_size: the number of items per page (defaults to 50)
            sort: the asset field to sort by (defaults to "date_added")
            direction: the sort direction (defaults to "descending")
    POST
        json: create (or update?) an asset. The only updating that can be done is changing the lock state.
    PUT
        json: update the locked state of an asset
    DELETE
        json: delete an asset
    """
    course_key = CourseKey.from_string(course_key_string)
    if not has_course_author_access(request.user, course_key):
        raise PermissionDenied()

    response_format = _get_request_response_format(request)
    if _check_request_response_format_is_json(request, response_format):
        if request.method == 'GET':
            return _assets_json(request, course_key)
        else:
            asset_key = AssetKey.from_string(asset_key_string) if asset_key_string else None
            return _update_asset(request, course_key, asset_key)
    elif request.method == 'GET':  # assume html
        return _asset_index(request, course_key)
    else:
        return HttpResponseNotFound()

def _get_request_response_format(request):

    return request.GET.get('format') or request.POST.get('format') or 'html'

def _check_request_response_format_is_json(request, response_format):

        return response_format == 'json' or 'application/json' in request.META.get('HTTP_ACCEPT', 'application/json')


def _asset_index(request, course_key):
    """
    Display an editable asset library.

    Supports start (0-based index into the list of assets) and max query parameters.
    """
    course_module = modulestore().get_course(course_key)

    return render_to_response('asset_index.html', {
        'context_course': course_module,
        'max_file_size_in_mbs': settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB,
        'chunk_size_in_mbs': settings.UPLOAD_CHUNK_SIZE_IN_MB,
        'max_file_size_redirect_url': settings.MAX_ASSET_UPLOAD_FILE_SIZE_URL,
        'asset_callback_url': reverse_course_url('assets_handler', course_key)
    })


def _assets_json(request, course_key):
    """
    Display an editable asset library.

    Supports start (0-based index into the list of assets) and max query parameters.
    """

    request_options = _parse_request_to_dictionary(request)

    filter_parameters = None

    if (request_options['requested_asset_type']):
        filter_parameters = _get_filter_parameters_for_mongo(request_options['requested_asset_type'])

    sort_type_and_direction = _get_sort_type_and_direction(request_options)

    requested_page_size = request_options['requested_page_size']
    current_page = _get_current_page(request_options['requested_page'])
    first_asset_to_display_index = _get_first_asset_index(current_page, requested_page_size)

    query_options = {
        'current_page': current_page,
        'page_size': requested_page_size,
        'sort': sort_type_and_direction,
        'filter_params': filter_parameters
    }

    assets, total_count = _get_assets_for_page(request, course_key, query_options)

    if requested_page_size > 0 and first_asset_to_display_index >= total_count:
        _update_options_to_requery_final_page(query_options, total_count)
        first_asset_to_display_index = _get_first_asset_index(current_page, requested_page_size)
        assets, total_count = _get_assets_for_page(request, course_key, query_options)


    last_asset_to_display_index = first_asset_to_display_index + len(assets)
    assets_in_json_format = _get_assets_in_json_format(assets, course_key)

    response_payload = {
        'start': first_asset_to_display_index,
        'end': last_asset_to_display_index,
        'page': current_page,
        'pageSize': requested_page_size,
        'totalCount': total_count,
        'assets': assets_in_json_format,
        'sort': request_options['requested_sort'],
    }

    return JsonResponse(response_payload)


def _parse_request_to_dictionary(request):

    return {
        'requested_page': int(_get_requested_attribute(request, 'page')),
        'requested_page_size': int(_get_requested_attribute(request, 'page_size')),
        'requested_sort': _get_requested_attribute(request, 'sort'),
        'requested_sort_direction': _get_requested_attribute(request, 'direction'),
        'requested_asset_type': _get_requested_attribute(request, 'asset_type')
    }


def _get_requested_attribute(request, attribute):

    return request.GET.get(attribute, request_defaults.get(attribute))


def _get_filter_parameters_for_mongo(requested_filter):

    if requested_filter == 'OTHER':
        mongo_where_operator_parameters = _get_mongo_where_operator_parameters_for_other()
    else:
        mongo_where_operator_parameters = _get_mongo_where_operator_parameters_for_filters(requested_filter)
    return mongo_where_operator_parameters


def _get_mongo_where_operator_parameters_for_other():

    requested_file_types = _get_files_and_upload_type_filters().keys()
    file_extensions_for_requested_file_types = _get_extensions_for_file_types(requested_file_types)
    javascript_expression_to_filter_extensions = _get_javascript_expressions_to_filter_extensions_with_operator(file_extensions_for_requested_file_types, "!=")
    javascript_expressions_to_filter_extensions_in_mongo = _get_javascript_expressions_for_mongo_filter_with_separator(javascript_expression_to_filter_extensions, ' && ')

    return javascript_expressions_to_filter_extensions_in_mongo


def _get_mongo_where_operator_parameters_for_filters(requested_filter):

    requested_file_types = _get_requested_file_types_from_request(requested_filter)
    file_extensions_for_request_file_types = _get_extensions_for_file_types(requested_file_types)
    javascript_expressions_to_filter_extensions = _get_javascript_expressions_to_filter_extensions_with_operator(file_extensions_for_requested_file_types, "==")
    javascript_expressions_to_filter_extensions_in_mongo = _get_javascript_expressions_for_mongo_filter_with_separator(javascript_expressions_to_filter_extensions, ' || ')

    return javascript_expressions_to_filter_extensions_in_mongo


def _get_files_and_upload_type_filters():

    return settings.FILES_AND_UPLOAD_TYPE_FILTERS


def _get_requested_file_types_from_request(requested_filter):

    return requested_filter.split(",")


def _get_extensions_for_file_types(requested_file_types):

    file_extensions_for_file_types = []

    for requested_file_type in requested_file_types:
        file_extension_for_file_type = _get_files_and_upload_type_filters().get(requested_file_type)
        file_extensions_for_file_types.extend(file_extension_for_file_type)

    return file_extensions_for_file_types


def _get_javascript_expressions_to_filter_extensions_with_operator(file_extensions, operator):

    return ["JSON.stringify(this.contentType).toUpperCase() " + operator + " JSON.stringify('{}').toUpperCase()".format(
                    file_extension) for file_extension in file_extensions]


def _get_javascript_expressions_for_mongo_filter_with_separator(javascript_expressions_for_mongo_filtering, separator):

    return {
        "$where": separator.join(javascript_expressions_for_mongo_filtering),
    }


def _get_sort_type_and_direction(request_options):

    sort_type = _get_mongo_sort_from_requested_sort(request_options['requested_sort'])
    sort_direction = _get_sort_direction_from_requested_sort(request_options['requested_sort_direction'])
    return [(sort_type, sort_direction)]


def _get_mongo_sort_from_requested_sort(requested_sort):

    if requested_sort == 'date_added':
        sort = 'uploadDate'
    elif requested_sort == 'display_name':
        sort = 'displayname'
    else:
        sort = requested_sort
    return sort


def _get_sort_direction_from_requested_sort(requested_sort_direction):

    if requested_sort_direction.lower() == 'asc':
        return ASCENDING
    else:
        return DESCENDING


def _get_current_page(requested_page):

    return max(requested_page, 0)


def _get_first_asset_index(current_page, page_size):

    return current_page * page_size


def _get_assets_for_page(request, course_key, options):

    current_page = options['current_page']
    page_size = options['page_size']
    sort = options['sort']
    filter_params = options['filter_params'] if options['filter_params'] else None
    start = current_page * page_size

    return contentstore().get_all_content_for_course(
        course_key, start=start, maxresults=page_size, sort=sort, filter_params=filter_params
    )


def _update_options_to_requery_final_page(query_options, total_asset_count):

    query_options['current_page'] = int(math.floor((total_asset_count -1) / query_options['page_size']))


def _get_assets_in_json_format(assets, course_key):

    assets_in_json_format = []
    for asset in assets:
        thumbnail_asset_key = _get_thumbnail_asset_key(asset, course_key)
        asset_is_locked = asset.get('locked', False)

        asset_in_json = _get_asset_json(
            asset['displayname'],
            asset['contentType'],
            asset['uploadDate'],
            asset['asset_key'],
            thumbnail_asset_key,
            asset_is_locked
        )

        assets_in_json_format.append(asset_in_json)

    return assets_in_json_format


@require_POST
@ensure_csrf_cookie
@login_required
def _upload_asset(request, course_key):
    '''
    This method allows for POST uploading of files into the course asset
    library, which will be supported by GridFS in MongoDB.
    '''

    _check_course_exists(course_key)

    file_metadata = _get_file_metadata_as_dictionary(request)

    # note that since the front-end may batch large file uploads in smaller chunks,
    # we validate the file-size on the front-end in addition to
    # validating on the backend (see cms/static/js/views/assets.js)
    _check_upload_file_size(file_metadata)

    content, temporary_file_path = _get_file_content_and_path(file_metadata, course_key)

    (thumbnail_content, thumbnail_location) = contentstore().generate_thumbnail(content, tempfile_path=temporary_file_path)

    # delete cached thumbnail even if one couldn't be created this time (else
    # the old thumbnail will continue to show)
    del_cached_content(thumbnail_location)

    if _check_thumbnail_uploaded(thumbnail_content):
        content.thumbnail_location = thumbnail_location

    contentstore().save(content)
    del_cached_content(content.location)

    # readback the saved content - we need the database timestamp
    readback = contentstore().find(content.location)
    locked = getattr(content, 'locked', False)
    response_payload = {
        'asset': _get_asset_json(
            content.name,
            content.content_type,
            readback.last_modified_at,
            content.location,
            content.thumbnail_location,
            locked
        ),
        'msg': _('Upload completed')
    }

    return JsonResponse(response_payload)


def _check_course_exists(course_key):

    try:
        modulestore().get_course(course_key)
    except ItemNotFoundError:
        logging.error("Could not find course: %s", course_key)
        return HttpResponseBadRequest()


def _get_file_metadata_as_dictionary(request):

    upload_file = request.FILES['file']

    # compute a 'filename' which is similar to the location formatting; we're
    # using the 'filename' nomenclature since we're using a FileSystem paradigm
    # here; we're just imposing the Location string formatting expectations to
    # keep things a bit more consistent
    return {
        'upload_file': upload_file,
        'filename': upload_file.name,
        'mime_type': upload_file.content_type,
        'upload_file_size': get_file_size(upload_file)
    }


def get_file_size(upload_file):

    # can be used for mocking test file sizes.
    return upload_file.size


def _check_upload_file_size(file_metadata):

    filename = file_metadata['filename']
    upload_file_size = file_metadata['upload_file_size']
    maximum_file_size_in_bytes = settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB * 1000 ** 2

    if upload_file_size > maximum_file_size_in_bytes:
        error_message = _get_file_too_large_error_message(filename)
        return JsonResponse({'error' : error_message}, status = 413)


def _get_file_too_large_error_message(filename):

    return
    _(
        'File {filename} exceeds maximum size of '
        '{size_mb} MB. Please follow the instructions here '
        'to upload a file elsewhere and link to it instead: '
        '{faq_url}'
    ).format(
        filename=filename,
        size_mb=settings.MAX_ASSET_UPLOAD_FILE_SIZE_IN_MB,
        faq_url=settings.MAX_ASSET_UPLOAD_FILE_SIZE_URL,
    )


def _get_file_content_and_path(file_metadata, course_key):

    content_location = StaticContent.compute_location(course_key, file_metadata['filename'])
    upload_file = file_metadata['upload_file']

    file_can_be_chunked = upload_file.multiple_chunks()

    static_content_partial = partial(StaticContent, content_location, file_metadata['filename'], file_metadata['mime_type'])

    if file_can_be_chunked:
        content = static_content_partial(upload_file.chunks())
        temporary_file_path = upload_file.temporary_file_path()
    else:
        content = static_content_partial(upload_file.read())
        temporary_file_path = None
    return content, temporary_file_path


def _check_thumbnail_uploaded(thumbnail_content):

    return thumbnail_content is not None


def _get_thumbnail_asset_key(asset, course_key):

    # note, due to the schema change we may not have a 'thumbnail_location' in the result set
    thumbnail_location = asset.get('thumbnail_location', None)
    thumbnail_asset_key = None

    if thumbnail_location:
        thumbnail_path = thumbnail_location[4]
        thumbnail_asset_key = course_key.make_asset_key('thumbnail', thumbnail_path)
    return thumbnail_asset_key


@require_http_methods(("DELETE", "POST", "PUT"))
@login_required
@ensure_csrf_cookie
def _update_asset(request, course_key, asset_key):
    """
    restful CRUD operations for a course asset.
    Currently only DELETE, POST, and PUT methods are implemented.

    asset_path_encoding: the odd /c4x/org/course/category/name repr of the asset (used by Backbone as the id)
    """
    if request.method == 'DELETE':
        try:
            delete_asset(course_key, asset_key)
            return JsonResponse()
        except AssetNotFoundException:
            return JsonResponse(status=404)

    elif request.method in ('PUT', 'POST'):
        if 'file' in request.FILES:
            return _upload_asset(request, course_key)
        else:
            # update existing asset
            try:
                modified_asset = json.loads(request.body)
            except ValueError:
                return HttpResponseBadRequest()
            contentstore().set_attr(asset_key, 'locked', modified_asset['locked'])
            # delete the asset from the cache so we check the lock status the next time it is requested.
            del_cached_content(asset_key)
            return JsonResponse(modified_asset, status=201)


def _save_content_to_trash(content):

    contentstore('trashcan').save(content)


def delete_asset(course_key, asset_key):

    content = _check_existence_and_get_asset_content(asset_key)

    _save_content_to_trash(content)

    _delete_thumbnail(content.thumbnail_location, course_key, asset_key)
    contentstore().delete(content.get_id())
    del_cached_content(content.location)

def _check_existence_and_get_asset_content(asset_key):

    try:
        content = contentstore().find(asset_key)
        return content
    except NotFoundError:
        raise AssetNotFoundException


def _delete_thumbnail(thumbnail_location, course_key, asset_key):

    if thumbnail_location is not None:

        # We are ignoring the value of the thumbnail_location-- we only care whether
        # or not a thumbnail has been stored, and we can now easily create the correct path.
        thumbnail_location = course_key.make_asset_key('thumbnail', asset_key.name)

        try:
            thumbnail_content = contentstore().find(thumbnail_location)
            _save_content_to_trash(thumbnail_content)
            contentstore().delete(thumbnail_content.get_id())
            del_cached_content(thumbnail_location)
        except Exception:  # pylint: disable=broad-except
            logging.warning('Could not delete thumbnail: %s', thumbnail_location)


def _get_asset_json(display_name, content_type, date, location, thumbnail_location, locked):
    """
    Helper method for formatting the asset information to send to client.
    """
    asset_url = StaticContent.serialize_asset_key_with_slash(location)
    external_url = settings.LMS_BASE + asset_url
    return {
        'display_name': display_name,
        'content_type': content_type,
        'date_added': get_default_time_display(date),
        'url': asset_url,
        'external_url': external_url,
        'portable_url': StaticContent.get_static_path_from_location(location),
        'thumbnail': StaticContent.serialize_asset_key_with_slash(thumbnail_location) if thumbnail_location else None,
        'locked': locked,
        # needed for Backbone delete/update.
        'id': unicode(location)
    }