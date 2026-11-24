"""Extract public Instagram photos and videos using yt-dlp's session handling.

Run in a subprocess so metadata requests retain the application's timeout boundary.
"""
import json
import sys
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.extractor.instagram import InstagramIE as BaseInstagramIE


class InstagramIE(BaseInstagramIE):
    def _extract_product_media(self, product_media):
        result = super()._extract_product_media(product_media)
        if result.get('formats'):
            return result
        # yt-dlp normally discards photo posts. Keep their actual source variants.
        images = (product_media.get('image_versions2') or {}).get('candidates') or []
        is_photo = product_media.get('media_type') == 1 or product_media.get('is_video') is False
        if images and is_photo:
            result['_tubeclip_media_type'] = 'image'
            result['_tubeclip_images'] = images
            result['formats'] = [{'url': image['url'], 'width': image.get('width'),
                                  'height': image.get('height'), 'ext': 'jpg'}
                                 for image in images if image.get('url')]
        return result


def extract(url):
    with YoutubeDL({'quiet': True, 'no_warnings': True, 'socket_timeout': 20,
                    'retries': 1, 'skip_download': True, 'noplaylist': True}) as ydl:
        info = InstagramIE(ydl).extract(url)
        entries = list(info.get('entries') or []) if info.get('_type') == 'playlist' else []
        index = int(parse_qs(urlparse(url).query).get('img_index', ['1'])[0])
        if entries:
            if not 1 <= index <= len(entries):
                raise ValueError('Choose an available item in this Instagram post.')
            info = {**entries[index - 1], '_tubeclip_item_count': len(entries), '_tubeclip_item_index': index}
        elif index != 1:
            raise ValueError('This Instagram post has only one item.')
        if info.get('_tubeclip_media_type') != 'image':
            info = ydl.process_ie_result(info, download=False)
        return ydl.sanitize_info(info)


if __name__ == '__main__':
    try:
        print(json.dumps(extract(sys.argv[1])))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
