"""Profile pictures: validated, cropped square, shrunk and re-encoded (which also strips camera metadata)."""
import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 5 * 1024 * 1024        # what we accept
SIZE = 320                          # what we keep: a 320 px square is crisp at every size we show
ALLOWED = {'JPEG', 'PNG', 'WEBP', 'GIF'}


class AvatarError(ValueError):
    """The upload can't be used; the message is safe to show to the person."""


def process(upload):
    """Returns a ContentFile (square JPEG) made from an uploaded image, or raises AvatarError."""
    if upload.size > MAX_UPLOAD:
        raise AvatarError('Please choose a picture under 5 MB.')
    try:
        image = Image.open(upload)
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise AvatarError('That file isn’t a picture we can read. Use a JPG, PNG or WebP image.') from None
    if image.format not in ALLOWED:
        raise AvatarError('Use a JPG, PNG or WebP image.')
    image = ImageOps.exif_transpose(image)               # respect the phone's rotation
    image = ImageOps.fit(image.convert('RGBA'), (SIZE, SIZE), Image.Resampling.LANCZOS)  # centre-crop to a square
    flat = Image.new('RGB', image.size, (255, 255, 255))  # transparent areas become white
    flat.paste(image, mask=image.getchannel('A'))
    out = BytesIO()
    flat.save(out, 'JPEG', quality=88, optimize=True)
    return ContentFile(out.getvalue())


def save_avatar(profile, upload):
    """Replace the profile's picture. The old file is removed, and the new name is unique so browsers never show a stale one."""
    content = process(upload)
    old = profile.avatar.name if profile.avatar else ''
    profile.avatar.save(f'u{profile.user_id}-{uuid.uuid4().hex[:8]}.jpg', content, save=False)
    profile.save(update_fields=['avatar', 'updated_at'])
    if old:
        profile.avatar.storage.delete(old)


def remove_avatar(profile):
    if profile.avatar:
        name = profile.avatar.name
        profile.avatar = ''
        profile.save(update_fields=['avatar', 'updated_at'])
        profile.avatar.storage.delete(name)
