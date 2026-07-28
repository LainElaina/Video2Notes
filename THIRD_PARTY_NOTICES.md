# Third-party notices

Video2Notes is distributed under the repository MIT license.  The project
contains and/or depends on the following third-party software.  This file is
an attribution and operational notice, not a replacement for the license text
bundled with each dependency or for a platform's terms of use.

## BiliNote study submodule

`reference/BiliNote` is an independent, shallow, read-only Git submodule used
for product and code-study reference.

- Project: [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote)
- Pinned snapshot: `6d67e5a76a2c8da1dd73067943d39021ed137c26` (BiliNote v2.4.4)
- Copyright: Jeffery Huang, 2024
- License: MIT; its complete license is retained in `reference/BiliNote/LICENSE`.

Video2Notes is a new implementation.  It does not copy BiliNote source files
into `src/` or the desktop package.  Any future selective migration of upstream
source must retain the associated copyright and license notice.

## Runtime dependencies and optional engines

The Python package declares its direct runtime and optional dependencies in
`pyproject.toml`; exact resolved versions are recorded by the user's Python
environment.  Notable components include:

| Component | Role in Video2Notes | Distribution / license reference |
| --- | --- | --- |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) (`2026.7.4`) | Local Bilibili/YouTube/X acquisition adapter | Upstream project and its license |
| [PyAV](https://github.com/PyAV-Org/PyAV) | Container PTS/time-base decode | Upstream project and its license |
| [FFmpeg](https://ffmpeg.org/) | Media probe/extraction/processing; sourced from `PATH` in development and copied into Windows release builds | The selected binary distribution determines its license; release builds include `FFMPEG_LICENSE.txt` and exact `FFMPEG_BUILD_INFO.txt` beside the bundled tools |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (optional) | Local ASR | MIT (see upstream) |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (optional) | Local OCR | Apache License 2.0 (see upstream) |
| [PaddlePaddle](https://github.com/PaddlePaddle/Paddle) (optional) | PaddleOCR inference engine | Apache License 2.0 (see upstream) |
| [FastAPI](https://github.com/fastapi/fastapi), Uvicorn (optional) | Local loopback API | Upstream projects and their licenses |
| [React](https://react.dev/), [Tauri](https://tauri.app/) | Desktop UI and Windows shell | Upstream projects and their licenses |
| [PyInstaller](https://pyinstaller.org/) | Builds the Windows local sidecar | GPL 2.0 with the upstream exception for distributing bundled applications |
| [Playwright](https://playwright.dev/) (development only) | Browser workflow verification | Apache License 2.0 (see upstream) |

The `yt-dlp` adapter is for content the local user is permitted to access and
download.  Video2Notes does not provide DRM circumvention, cookie hosting, or
service-side account collection.  Users are responsible for respecting the
platform terms, applicable law and content rights for each source.

The Windows release build copies `ffmpeg.exe` and `ffprobe.exe` from the
explicit `-FfmpegDirectory` or from the build host's `PATH`.  The build refuses
to package them unless it can also copy that distribution's license (or the
caller supplies `-FfmpegLicensePath`).  It writes the complete `ffmpeg
-version` output and an upstream source reference into
`backend/tools/FFMPEG_BUILD_INFO.txt`.  The 2026-07-28 verification package used
Gyan's `2026-02-09-git-9bfa1635ae` static essentials build, whose build page
identifies those binaries as GPLv3.  See
[Gyan FFmpeg builds](https://www.gyan.dev/ffmpeg/builds/) and the
[FFmpeg source repository](https://github.com/FFmpeg/FFmpeg).

## Models and model providers

Model weights, local OCR/ASR engines, Ollama models and OpenAI-compatible
providers are selected by the user and are not redistributed by this repository.
They may carry separate licenses, acceptable-use terms, privacy obligations and
hardware requirements.  Video2Notes stores provider secret values in the local
credential manager when configured through its API; it does not include those
secrets in repository files or task manifests.

## Self-authored demo media

`samples/evidence-demo.mp4` is generated entirely by
`scripts/create_demo_media.ps1` from FFmpeg color, text and sine sources. It
contains no third-party footage, music, account data or model output and is
distributed under the repository MIT license.
