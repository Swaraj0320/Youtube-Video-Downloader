# TubeClip By Swaraj

A local YouTube video downloader, MP3 extractor, and clip editor for macOS. Built with React, TypeScript, Vite, Tailwind CSS, Lucide, FastAPI, yt-dlp, and FFmpeg. No paid API, account, Docker, or API key is required.

## Start

Requirements: Python 3.11+ and Node.js 22.12+ (Node 24 recommended), npm, an internet connection, and enough free disk space. FFmpeg and FFprobe are installed **inside this project** using npm packages; no Homebrew or system-wide installation is needed. The setup is verified on this Apple Silicon Mac with Python 3.14 and Node 24.

From Terminal:

```bash
cd "$HOME/Desktop/YOUTUBE VIDEO DOWNLOADER"
./setup.sh
./start.sh
```

Setup installs the Python virtual environment, JavaScript packages, and local media binaries, then builds the frontend. You only need setup initially or after changing dependencies. `start.sh` launches the API and frontend, checks both, and prints the address:

**http://127.0.0.1:5173**

Keep that terminal open. Press **Ctrl+C** to stop both servers. If TubeClip is already running, `start.sh` prints its address and reuses it. If another application is using either port, stop that conflicting server before launching TubeClip. Both servers bind to this computer's loopback interface. This is a personal local application, not a publicly hosted service.

## Use

1. Paste a single YouTube video URL and click **Analyze Video**, or press Enter. The Paste button uses the browser clipboard permission; use Command+V if permission is unavailable.
2. Check the title, channel, duration, and thumbnail. Select **Best Available** or a resolution actually returned by yt-dlp. Quality is never upscaled or silently reduced.
3. Choose **Video** or **MP3 Audio**. MP3 uses the best accessible audio and high-quality VBR conversion; conversion cannot improve the source recording.
4. For the full video, leave both timestamp inputs empty. For a clip, enter both as `HH:MM:SS` (for example `00:04:00` to `00:04:50`), or drag the two timeline handles. The handles also support keyboard arrows when focused. The end must be after the start and within the video duration.
5. **Preview Clip** loads the selected interval in the embedded player. YouTube must allow embedding and playback; your browser may require pressing Play. Downloading and timestamp selection are independent of preview availability. **Reset to Full Video** clears both timestamps.
6. Click **Download**. Transfer percentages come from yt-dlp and apply to each source stream; a percentage can reset when the audio stream begins. Processing phases have an indeterminate indicator. Only verified finished output is marked ready.
7. The browser download starts automatically as soon as processing finishes—no second click is needed. If you need another copy or the browser blocks the automatic download, use **Download again** on the same primary button; this reuses the finished file without processing again. Changing the quality, format, or clip starts a new job on the next Download click. The processed original is also retained in the project's `downloads/` folder. Your browser may save an additional copy to its configured Downloads folder. Clips include timestamps in their names; every file has a collision-resistant suffix.
8. Paste another link and analyze it when the current job finishes. Failed/cancelled downloads can be retried. Cancellation terminates the job's subprocess group and removes its working files.

## Appearance

TubeClip By Swaraj opens with a linen background by default. Dark mode keeps a pale-slate page canvas with midnight-violet panels. The original transparent flower logo appears in the header, footer, and browser icon. The sun/moon button in the header switches between dark and linen light themes and remembers your choice in this browser. Manrope and Newsreader fonts are served locally with the app.

## Processing and limits

- Single videos only. Video links containing playlist/radio parameters such as `list=`, `index=`, or `start_radio=1` are accepted; those parameters are stripped and only the selected video is processed. Playlist-only links are rejected. Ongoing live streams are rejected. Completed `/live/` and `/shorts/` links are supported when accessible.
- High-resolution video-only streams are downloaded together with audio and merged. Full videos preserve the video stream without re-encoding; incompatible MP4 audio is converted to AAC. MP4 is preferred; if the source codecs cannot be remuxed to MP4, the output uses MKV and its actual extension is shown.
- Clips use yt-dlp's `--download-sections` and `--force-keyframes-at-cuts`, invoking FFmpeg to re-encode for accurate boundaries. Source/network seek support may affect how much media must be transferred. This takes more CPU than a keyframe-only cut, especially at 4K. MP3 conversion processes only the selected clip, not the entire original.
- Before success, FFprobe checks duration, the requested video height, audio presence, and output type. A duration discrepancy over 1.5 seconds is rejected. Tests use tighter tolerances on generated samples.
- Two concurrent jobs and two metadata analyses are allowed. Individual transfers have a 30-minute timeout and a 20 GB source-file limit; FFmpeg processing also has a 30-minute timeout. At least 512 MB free space is required before starting. Keep substantially more space free for large sources, intermediate streams, and output.
- Analysis expires after one hour. Job status and file links are held in memory; after restarting, existing files remain in `downloads/` and can be opened from Finder. Old job links will no longer work. The last 200 jobs are retained in memory; completed files are never automatically deleted.
- Working folders are isolated under `temp/job-<id>/` and removed on completion, failure, or cancellation. Startup removes abandoned working folders older than 24 hours. After a forced termination, newer abandoned folders can remain until a later startup.
- Browser codec support varies; a downloaded AV1/VP9 MP4 may require a compatible media player. Full video downloads preserve source codecs. Restricted, private, DRM-protected, and inaccessible content is not bypassed. Network and YouTube changes can affect extraction.

## Troubleshooting

**Engine unavailable / missing FFmpeg:** Run `./setup.sh` again, then restart. Check `http://127.0.0.1:8000/api/health` for dependency status.

**YouTube extraction fails:** Check that the video is publicly accessible, try a different single video, and update the extractor and its matching EJS scripts:

```bash
.venv/bin/python -m pip install -U "yt-dlp[default]"
```

Restart TubeClip afterward. Node is explicitly enabled as yt-dlp's JavaScript runtime. EJS scripts are installed via the `default` dependency group as described in the [official yt-dlp EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS). We do not import browser cookies or attempt to bypass restrictions. YouTube may temporarily block access even to public videos.

**Download fails:** Check the backend terminal for technical logs, your network connection, and available disk space. Analyze again if a format or analysis has expired. A failed job never becomes a successful downloadable file.

**Player unavailable:** Some videos disallow embedding or playback in particular browsers. Select the clip using the timeline and time inputs; an embedded player is not required for processing.

**Long clips/4K are slow:** Precise cutting requires encoding. Leave the timestamps empty for a full download without the clip re-encoding cost.

## Verify / develop

```bash
# Run backend logic, API, and real media pipeline tests.
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q

# Type-check and compile the frontend.
cd frontend
npm run build
```

The tests generate an eight-second video and sine-wave audio locally, run real FFmpeg conversions and separate-stream merging, then exercise yt-dlp jobs through a local fixture server for full/clip video and MP3. They also cover exact resolution selection, invalid URLs/timestamps, unavailable dependencies, output filenames/serving, and cancellation. The fixture server is test-only; the public API accepts only canonical YouTube URLs.

`frontend/package-lock.json` and `tools/package-lock.json` record JavaScript dependencies. `backend/requirements.lock.txt` records the verified Python environment; install it with `.venv/bin/python -m pip install -r backend/requirements.lock.txt` when exact reproducibility is needed. Normal setup uses updateable version ranges so yt-dlp can receive extractor fixes.

## Responsible use

Only download content you own or are authorized to save, and follow applicable terms and permissions. No DRM circumvention, authentication bypass, or access-control evasion is implemented.
