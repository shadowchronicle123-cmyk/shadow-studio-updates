"""Run the real local web app with temporary data and synthetic voices for browser tests."""
import os
from pathlib import Path
import sys

# Disable external provider paths before app startup, including in a developer install.
for key in ('ELEVENLABS_API_KEY','SHADOW_S3_BUCKET','SHADOW_S3_ACCESS_KEY','SHADOW_S3_SECRET_KEY',
            'SHADOW_S3_ENDPOINT','SHADOW_OLLAMA_MODEL','SHADOW_HANDOFF_AUTO_UPLOAD'):
    os.environ[key]=''

import uvicorn
import app
import cloud_handoff
import renderer
import studio_tools
from gameplay_library import GameplayLibrary
from production_smoke_test import synthetic_voice


def offline_voice(text, path, voice, rate):
    assert not voice.startswith('elevenlabs:')
    synthetic_voice(text,path,voice,rate)


if __name__=='__main__':
    root=Path(sys.argv[1])
    app.OUT=root/'outputs';app.OUT.mkdir()
    app.UPLOADS=root/'uploads';app.UPLOADS.mkdir()
    studio_tools.PROJECTS=root/'projects';studio_tools.PROJECTS.mkdir()
    cloud_handoff.OUT=app.OUT
    app.GAMEPLAY=GameplayLibrary(root/'gameplay')
    renderer.create_voice=offline_voice
    app.create_voice=offline_voice
    uvicorn.run(app.app, host='127.0.0.1', port=8878, log_level='warning')
