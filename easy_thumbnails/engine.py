import os

from io import BytesIO

from PIL import Image

from easy_thumbnails import utils
from easy_thumbnails.conf import settings
from easy_thumbnails.options import ThumbnailOptions
from easy_thumbnails.utils import get_color_profile_data, prepare_image_color_mode


class NoSourceGenerator(Exception):
    """
    Exception that is raised if no source generator can process the source
    file.
    """

    def __str__(self):
        return "Tried {0} source generators with no success".format(
            len(self.args))


def process_image(source, processor_options, processors=None):
    """
    Process a source PIL image through a series of image processors, returning
    the (potentially) altered image.
    """
    processor_options = ThumbnailOptions(processor_options)
    if processors is None:
        processors = [
            utils.dynamic_import(name)
            for name in settings.THUMBNAIL_PROCESSORS]
    image = source
    for processor in processors:
        image = processor(image, **processor_options)
    return image


def save_image(image, destination=None, filename=None, **options):
    """
    Save a PIL image with proper ICC profile handling and EXIF metadata preservation.

    Parameters:
        image (PIL.Image.Image): The PIL image to save
        destination (str or file-like object, optional): The destination to save the image
        filename (str, optional): The filename (used to determine format)
        **options: Additional keyword arguments for saving the image

    Returns:
        destination: The saved image file-like object or file path
    """
    if destination is None:
        destination = BytesIO()

    # Determine format
    filename = filename or ''
    # Ensure plugins are fully loaded so that Image.EXTENSION is populated.
    Image.init()
    ext = os.path.splitext(filename)[1].lower()
    format = Image.EXTENSION.get(ext, 'JPEG')
    image.format = format

    # Initialize save options
    save_options = options.copy()
    default_quality = 85

    # Set format-specific options
    if format in ('JPEG', 'WEBP', 'TIFF'):
        save_options['optimize'] = True
        save_options.setdefault('quality', default_quality)
    elif format == 'PNG':
        save_options.pop('quality', None)
        save_options['compress_level'] = 3

    # Handle progressive JPEGs
    if format == 'JPEG' and settings.THUMBNAIL_PROGRESSIVE and (max(image.size) >= settings.THUMBNAIL_PROGRESSIVE):
        save_options['progressive'] = True

    # Get color profile settings
    srgb_profile_path = getattr(settings, 'THUMBNAIL_SRGB_ICC_PROFILE_PATH', None)
    color_options = get_color_profile_data(image, srgb_profile_path)
    save_options.update(color_options)

    # Prepare image color mode
    image = prepare_image_color_mode(image, format)

    # Save the image
    try:
        image.save(destination, format=format, **save_options)
    except IOError as e:
        if 'optimize' in save_options:
            save_options.pop('optimize')
            image.save(destination, format=format, **save_options)
        else:
            raise e

    # Reset the destination's file pointer if it's file-like
    if hasattr(destination, 'seek'):
        destination.seek(0)
    return destination


def generate_source_image(source_file, processor_options, generators=None,
                          fail_silently=True):
    """
    Processes a source ``File`` through a series of source generators, stopping
    once a generator returns an image.

    The return value is this image instance or ``None`` if no generators
    return an image.

    If the source file cannot be opened, it will be set to ``None`` and still
    passed to the generators.
    """
    processor_options = ThumbnailOptions(processor_options)
    # Keep record of whether the source file was originally closed. Not all
    # file-like objects provide this attribute, so just fall back to False.
    was_closed = getattr(source_file, 'closed', False)
    if generators is None:
        generators = [
            utils.dynamic_import(name)
            for name in settings.THUMBNAIL_SOURCE_GENERATORS]
    exceptions = []
    try:
        for generator in generators:
            source = source_file
            # First try to open the file.
            try:
                source.open()
            except Exception:
                # If that failed, maybe the file-like object doesn't support
                # reopening so just try seeking back to the start of the file.
                try:
                    source.seek(0)
                except Exception:
                    source = None
            try:
                image = generator(source, **processor_options)
            except Exception as e:
                if not fail_silently:
                    if len(generators) == 1:
                        raise
                    exceptions.append(e)
                image = None
            if image:
                return image
    finally:
        # Attempt to close the file if it was closed originally (but fail
        # silently).
        if was_closed:
            try:
                source_file.close()
            except Exception:
                pass
    if exceptions and not fail_silently:
        raise NoSourceGenerator(*exceptions)
