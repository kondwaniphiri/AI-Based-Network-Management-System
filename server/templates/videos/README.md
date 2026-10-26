# Local video workload assets

Video files are not included in this repository. Place media you are authorized
to use in this directory and update STREAM_CONFIG in server/server.py to match
its filenames. Keep media local; .gitignore excludes video assets.

The server can generate synthetic traffic when a configured file is absent.
Synthetic bytes are useful as transfer load but are not a playable substitute
for an actual MP4. Use a valid local video when testing browser playback.
