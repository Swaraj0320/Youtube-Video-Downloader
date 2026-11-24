#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
for dep in python3 node npm; do
  command -v "$dep" >/dev/null || { echo "Missing $dep. Install Python 3.11+ and Node 22.12+ before running setup."; exit 1; }
done
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
node -e 'const [a,b]=process.versions.node.split(".").map(Number);if(a<22||(a===22&&b<12))throw Error("Node 22.12+ is required")'
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
(cd tools && npm ci)
(cd frontend && npm ci)
mkdir -p tools/bin downloads temp
node <<'JS'
const fs=require('fs'),path=require('path');
for(const [name,source] of [['ffmpeg',require('./tools/node_modules/ffmpeg-static')],['ffprobe',require('./tools/node_modules/ffprobe-static').path]]){
 const dest=path.resolve('tools/bin',name); if(fs.existsSync(dest))fs.unlinkSync(dest);fs.symlinkSync(path.relative(path.dirname(dest),source),dest);
}
JS
./tools/bin/ffmpeg -version | head -n 1
./tools/bin/ffprobe -version | head -n 1
.venv/bin/python -m yt_dlp --version
.venv/bin/python -m pip freeze > backend/requirements.lock.txt
(cd frontend && npm run build)
echo "TubeClip setup complete. Run ./start.sh and open http://127.0.0.1:5173"
