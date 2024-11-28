import hashlib
import inspect
import logging
import math

from django.utils.functional import LazyObject
from django.utils import timezone
from PIL import Image
from easy_thumbnails.conf import settings

logger = logging.getLogger('easy_thumbnails.utils')


def image_entropy(im):
    """
    Calculate the entropy of an image. Used for "smart cropping".
    """
    if not isinstance(im, Image.Image):
        # Can only deal with PIL images. Fall back to a constant entropy.
        return 0
    hist = im.histogram()
    hist_size = float(sum(hist))
    hist = [h / hist_size for h in hist]
    return -sum([p * math.log(p, 2) for p in hist if p != 0])


def dynamic_import(import_string):
    """
    Dynamically import a module or object.
    """
    # Use rfind rather than rsplit for Python 2.3 compatibility.
    lastdot = import_string.rfind('.')
    if lastdot == -1:
        return __import__(import_string, {}, {}, [])
    module_name, attr = import_string[:lastdot], import_string[lastdot + 1:]
    parent_module = __import__(module_name, {}, {}, [attr])
    return getattr(parent_module, attr)


def valid_processor_options(processors=None):
    """
    Return a list of unique valid options for a list of image processors
    (and/or source generators)
    """
    if processors is None:
        processors = [
            dynamic_import(p) for p in
            tuple(settings.THUMBNAIL_PROCESSORS) +
            tuple(settings.THUMBNAIL_SOURCE_GENERATORS)]
    valid_options = set(['size', 'quality', 'subsampling'])
    for processor in processors:
        args = inspect.getfullargspec(processor)[0]
        # Add all arguments apart from the first (the source image).
        valid_options.update(args[1:])
    return list(valid_options)


def is_storage_local(storage):
    """
    Check to see if a file storage is local.
    """
    try:
        storage.path('test')
    except NotImplementedError:
        return False
    return True


def get_storage_hash(storage):
    """
    Return a hex string hash for a storage object (or string containing
    'full.path.ClassName' referring to a storage object).
    """
    # If storage is wrapped in a lazy object we need to get the real thing.
    if isinstance(storage, LazyObject):
        if storage._wrapped is None:
            storage._setup()
        storage = storage._wrapped
    if not isinstance(storage, str):
        storage_cls = storage.__class__
        storage = '%s.%s' % (storage_cls.__module__, storage_cls.__name__)
    return hashlib.md5(storage.encode('utf8')).hexdigest()


def is_transparent(image):
    """
    Check to see if an image is transparent.
    """
    if not isinstance(image, Image.Image):
        # Can only deal with PIL images, fall back to the assumption that that
        # it's not transparent.
        return False
    return (image.mode in ('RGBA', 'LA') or
            (image.mode == 'P' and 'transparency' in image.info))


def is_progressive(image):
    """
    Check to see if an image is progressive.
    """
    if not isinstance(image, Image.Image):
        # Can only check PIL images for progressive encoding.
        return False
    return ('progressive' in image.info) or ('progression' in image.info)


def exif_orientation(im):
    """
    Rotate and/or flip an image to respect the image's EXIF orientation data.
    """
    try:
        exif = im._getexif()
    except Exception:
        # There are many ways that _getexif fails, we're just going to blanket
        # cover them all.
        exif = None
    if exif:
        orientation = exif.get(0x0112)
        if orientation == 2:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            im = im.transpose(Image.ROTATE_180)
        elif orientation == 4:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            im = im.transpose(Image.ROTATE_270).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 6:
            im = im.transpose(Image.ROTATE_270)
        elif orientation == 7:
            im = im.transpose(Image.ROTATE_90).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 8:
            im = im.transpose(Image.ROTATE_90)
    return im


def get_modified_time(storage, name):
    """
    Get modified time from storage, ensuring the result is a timezone-aware
    datetime.
    """
    try:
        modified_time = storage.get_modified_time(name)
    except OSError:
        return 0
    except NotImplementedError:
        return None
    if modified_time and timezone.is_naive(modified_time):
        if getattr(settings, 'USE_TZ', False):
            default_timezone = timezone.get_default_timezone()
            return timezone.make_aware(modified_time, default_timezone)
    return modified_time


def get_srgb_profile(srgb_profile_path):
    """
    Load sRGB ICC profile from the specified path.

    Parameters:
        srgb_profile_path (str): Path to the sRGB ICC profile file

    Returns:
        bytes or None: The ICC profile data if successful, None otherwise
    """
    if not srgb_profile_path:
        return None

    try:
        with open(srgb_profile_path, 'rb') as icc_file:
            return icc_file.read()
    except Exception:
        return None


def get_color_profile_data(image, srgb_profile_path=None):
    """
    Extract and process color profile data from an image.

    Parameters:
        image (PIL.Image.Image): The PIL image to process
        srgb_profile_path (str, optional): Path to the sRGB ICC profile file

    Returns:
        dict: Color profile options to be used when saving the image
    """
    save_options = {}

    # Get existing ICC profile
    icc_profile = image.info.get('icc_profile', None)

    logger.debug(f"ICC profile: {icc_profile}")

    # Extract EXIF data
    exif = image.getexif()
    exif_bytes = image.info.get('exif', b'')

    # Check color space from EXIF
    color_space_tag = 40961  # 0xA001
    color_space = exif.get(color_space_tag, 0)  # 1 = sRGB, 65535 = Uncalibrated

    # Handle ICC profile
    if not icc_profile and color_space == 1 and srgb_profile_path:
        # Image references sRGB but has no embedded profile
        srgb_icc_profile = get_srgb_profile(srgb_profile_path)
        if srgb_icc_profile:
            logger.debug(f"Using sRGB ICC profile")
            save_options['icc_profile'] = srgb_icc_profile
    elif icc_profile:
        # Preserve existing ICC profile
        save_options['icc_profile'] = icc_profile

    # Include EXIF data if present
    if exif_bytes:
        logger.debug(f"Using EXIF data")
        save_options['exif'] = exif_bytes

    return save_options


def prepare_image_color_mode(image, format):
    """
    Prepare image color mode for the target format.

    Parameters:
        image (PIL.Image.Image): The PIL image to process
        format (str): The target format (e.g., 'JPEG', 'PNG')

    Returns:
        PIL.Image.Image: The processed image
    """
    if format == 'JPEG':
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and 'transparency' in image.info):
            return image.convert("RGB")
    return image
