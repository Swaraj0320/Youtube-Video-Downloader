"""Manual container verification; not a public API endpoint and never downloads media."""
import argparse
import hashlib
import json
import shutil
import subprocess

from .diagnostics import ExtractionError, runtime_details


def self_check():
    import yt_dlp_ejs.yt.solver as solver
    # Use the integrity expectations shipped with our pinned yt-dlp, never bypass them.
    from yt_dlp.extractor.youtube.jsc._builtin import vendor
    for name in ('lib', 'core'):
        code = getattr(solver, name)()
        if hashlib.sha3_512(code.encode()).hexdigest() != vendor.HASHES[f'yt.solver.{name}.min.js']:
            raise RuntimeError('Bundled EJS integrity check failed.')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is missing.')
    # Exercise the same restricted Node stdin mode used by the pinned provider.
    result = subprocess.run([node, '--permission', '-'], input='console.log(6 * 7)',
                            capture_output=True, text=True, timeout=10)
    if result.returncode or result.stdout.strip() != '42':
        raise RuntimeError('Restricted Node execution failed.')
    return {**runtime_details(), 'ejs_integrity': 'ok', 'restricted_node': 'ok'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true', help='Offline package/Node check; no source request.')
    parser.add_argument('url', nargs='?', help='A public, authorized single YouTube video URL.')
    args = parser.parse_args()
    print(json.dumps(self_check()), flush=True)
    if args.self_check:
        return 0
    if not args.url:
        parser.error('Provide a YouTube URL or --self-check.')
    from . import media
    url = media.youtube_url(args.url)
    try:
        info = media.analyze_video(url)
    except ExtractionError as exc:
        print(json.dumps({'status': 'failed', 'reason': exc.reason}))
        return 1
    except ValueError:
        print(json.dumps({'status': 'rejected', 'reason': 'validation_or_demo_limit'}))
        return 2
    print(json.dumps({'status': 'ok', 'qualities': info['qualities'],
                      'audio_available': info['audio_available']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
