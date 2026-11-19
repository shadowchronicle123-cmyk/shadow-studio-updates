"""Selectable narration. Edge voices require internet; Windows speech stays offline."""
import asyncio
import os
import subprocess
from pathlib import Path
import re
import requests

VOICES = [
    {'id':'en-US-AriaNeural','name':'Aria — conversational woman (US)','provider':'online'},
    {'id':'en-US-JennyNeural','name':'Jenny — friendly woman (US)','provider':'online'},
    {'id':'en-US-MichelleNeural','name':'Michelle — warm woman (US)','provider':'online'},
    {'id':'en-US-GuyNeural','name':'Guy — storyteller man (US)','provider':'online'},
    {'id':'en-US-DavisNeural','name':'Davis — deeper man (US)','provider':'online'},
    {'id':'en-US-ChristopherNeural','name':'Christopher — announcer man (US)','provider':'online'},
    {'id':'en-GB-SoniaNeural','name':'Sonia — woman (British)','provider':'online'},
    {'id':'en-GB-RyanNeural','name':'Ryan — man (British)','provider':'online'},
    {'id':'windows-default','name':'Windows built-in voice — offline fallback','provider':'offline'},
]
def configured_elevenlabs_voice():
    """Expose just the configured voice ID, never the secret API key."""
    voice_id = os.getenv('ELEVENLABS_VOICE_ID', '').strip()
    key = os.getenv('ELEVENLABS_API_KEY', '').strip()
    if not key or not re.fullmatch(r'[A-Za-z0-9]{12,64}', voice_id):
        return None
    return {'id': 'elevenlabs:' + voice_id,
            'name': ('Matilda — natural storytelling (ElevenLabs)' if voice_id == 'XrExE9yKIg1WjnnlVkGX' else 'ElevenLabs — my premium voice'), 'provider': 'premium'}

def available_voices():
    extra = configured_elevenlabs_voice()
    return ([extra] if extra else []) + VOICES

def valid_voice(voice):
    return any(v['id'] == voice for v in available_voices())

VOICE_IDS = {v['id'] for v in VOICES}

def create_voice(text: str, wav_path: Path, voice: str, rate: int = 0):
    if not valid_voice(voice):
        raise ValueError('Unsupported voice or missing ElevenLabs configuration')
    if not -30 <= rate <= 30:
        raise ValueError('Voice speed must be between -30 and 30')
    wav_path = Path(wav_path)
    if voice.startswith('elevenlabs:'):
        # The user explicitly opts into paid requests by selecting this provider.
        # Secrets are read server-side from .env and never passed to the browser.
        voice_id = voice.split(':', 1)[1]
        mp3 = wav_path.with_suffix('.mp3')
        try:
            response = requests.post(
                'https://api.elevenlabs.io/v1/text-to-speech/' + voice_id,
                headers={'xi-api-key': os.environ['ELEVENLABS_API_KEY'],
                         'Accept': 'audio/mpeg', 'Content-Type': 'application/json'},
                json={'text': text, 'model_id': 'eleven_multilingual_v2',
                      'voice_settings': {'stability': 0.48, 'similarity_boost': 0.75,
                                         'style': 0.2, 'use_speaker_boost': True}},
                timeout=65,
            )
            if not response.ok:
                raise RuntimeError('Provider returned HTTP ' + str(response.status_code) +
                                   '. Check your API key, voice ID, subscription and quota.')
            if not response.content or len(response.content) > 15_000_000:
                raise RuntimeError('Premium voice returned an empty or oversized response')
            mp3.write_bytes(response.content)
            proc = subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(mp3),
                                   '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav_path)],
                                  capture_output=True, text=True, timeout=60)
            if proc.returncode or not wav_path.is_file():
                raise RuntimeError('Could not decode the provider audio: ' + proc.stderr[-250:])
        except requests.RequestException as exc:
            raise RuntimeError('Could not reach ElevenLabs. Check internet connection.') from exc
        finally:
            mp3.unlink(missing_ok=True)
        return
    if voice == 'windows-default':
        if os.name != 'nt':
            raise RuntimeError('Windows offline voice is only available on Windows')
        # PowerShell passed through standard input, avoiding an additional downloaded script.
        escaped_path=str(wav_path).replace("'", "''")
        escaped_text=text.replace("'", "''")
        script=("Add-Type -AssemblyName System.Speech\n"
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
                f"$s.Rate={max(-10,min(10,round(rate/5)))}\n"
                f"$s.SetOutputToWaveFile('{escaped_path}')\n"
                f"$s.Speak('{escaped_text}')\n$s.Dispose()\n")
        proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command','-'],input=script,text=True,capture_output=True,timeout=90)
        if proc.returncode or not wav_path.is_file():
            raise RuntimeError('Windows voice generation failed: '+proc.stderr[-350:])
        return
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError('Neural voices require edge-tts. Run: .venv\\Scripts\\python.exe -m pip install edge-tts') from exc
    mp3=wav_path.with_suffix('.mp3')
    async def speak():
        c=edge_tts.Communicate(text,voice=voice,rate=f'{rate:+d}%')
        await c.save(str(mp3))
    try:
        asyncio.run(speak())
        proc=subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(mp3),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav_path)],capture_output=True,text=True,timeout=60)
        if proc.returncode or not wav_path.is_file():
            raise RuntimeError(proc.stderr[-400:])
    except Exception as exc:
        raise RuntimeError('Neural voice unavailable. Check internet access or choose Windows offline voice. Details: '+str(exc)[-200:]) from exc
    finally:
        mp3.unlink(missing_ok=True)
