# Third-party software in Dubify Studio

## FFmpeg
Dubify Studio includes unmodified builds of the FFmpeg command-line programs
`ffmpeg` and `ffprobe` (https://ffmpeg.org), licensed under the GNU Lesser
General Public License, version 2.1 or later. The license text and the exact
version, source checksum and build options are in `licenses/` next to this
file. They are built from the official source release with
`scripts/build-ffmpeg.sh`, without any GPL components; the matching source
archive is published alongside each Dubify Studio release. FFmpeg is a
trademark of Fabrice Bellard.

## OpenCC
Traditional-to-Simplified Chinese conversion uses OpenCC data via
opencc-python-reimplemented, licensed under the Apache License 2.0.

## Other open-source components
The desktop shell is built with Tauri (MIT or Apache-2.0). The bundled backend
runs on Python (PSF License) with FastAPI, Starlette, Pydantic, SQLAlchemy,
Uvicorn, HTTPX and other packages under MIT, BSD and Apache-2.0 licenses.
