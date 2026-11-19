"""Native 1080p export acceptance and representative frame checks."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat
from captions import read_captions
from production_smoke_test import synthetic_voice
import renderer
from fastapi.testclient import TestClient
from gameplay_library import GameplayLibrary
import app


def run(*args):
    return subprocess.check_output([str(arg) for arg in args])


def main():
    with tempfile.TemporaryDirectory(prefix='shadow_conversation_') as folder:
        root = Path(folder)
        gameplay = root/'portrait.mp4'
        run('ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
            'color=c=red:s=360x640:r=30:d=1,drawbox=x=0:y=320:w=360:h=320:color=blue:t=fill',
            '-c:v','libx264','-pix_fmt','yuv420p',gameplay)
        audio_only = root/'audio-as-video.mp4'
        run('ffmpeg','-y','-loglevel','error','-f','lavfi','-i','sine=d=2','-c:a','aac',audio_only)
        upload_root = root/'uploads'
        upload_root.mkdir()
        with patch.multiple(app, UPLOADS=upload_root, GAMEPLAY=GameplayLibrary(root/'library')):
            client = TestClient(app.app)
            for endpoint in ('/api/gameplay','/api/upload-background'):
                response=client.post(endpoint,files={'file':('audio.mp4',audio_only.read_bytes(),'video/mp4')})
                assert response.status_code==400 and 'video track' in response.json()['detail']
            response=client.post('/api/upload-background',files={'file':('gameplay.mp4',gameplay.read_bytes(),'video/mp4')})
            assert response.status_code==200,response.text
            assert (upload_root/response.json()['name']).is_file()
        story = {'title':'The missing guest', 'lines':[
            {'speaker':'Riley','text':'The wedding planner printed one extra place card.'},
            {'speaker':'Avery','text':'I know. It belonged to the person who signed the letter.', 'pause_before':1},
            {'speaker':'Riley','text':("W"*100 + " ") * 2 + "Our next line still fits."},
        ]}
        voice_a='en-US-AriaNeural'
        voice_b='en-US-GuyNeural'
        with patch.object(renderer,'create_voice',side_effect=synthetic_voice) as voice:
            result = renderer.generate(story,root/'export',background=gameplay,resolution='1080p',
                                       voice=voice_a,second_voice=voice_b,primary_speaker='avery',
                                       gameplay_percent=60,gameplay_anchor='bottom')
        assert [call.args[2] for call in voice.call_args_list]==[voice_b,voice_a,voice_b]
        video = Path(result['path'])
        report = json.loads(run('ffprobe','-v','error','-show_streams','-of','json',video))['streams']
        picture,audio = report
        assert (picture['width'],picture['height'],picture['r_frame_rate'])==(1080,1920,'30/1')
        assert abs(float(picture['duration'])-float(audio['duration']))<.11
        cues = read_captions(root/'export'/'captions.srt')
        assert cues[1]['start'] >= cues[0]['end']+1

        def frame(at):
            data=run('ffmpeg','-v','error','-ss',str(at),'-i',video,'-frames:v','1',
                     '-f','rawvideo','-pix_fmt','rgb24','-threads','1','-')
            return Image.frombytes('RGB',(1080,1920),data)

        split,_=renderer.gameplay_dimensions(1920,60)
        preview = Image.open(io.BytesIO(renderer.preview_layout(story,1,'messages',show_gameplay=True,
            background=gameplay,resolution='1080p',gameplay_percent=60,gameplay_anchor='bottom'))).convert('RGB')
        top = Image.open(io.BytesIO(renderer.preview_layout(story,1,'messages',show_gameplay=True,
            background=gameplay,resolution='1080p',gameplay_percent=60,gameplay_anchor='top'))).convert('RGB')
        assert top.getpixel((400,split+400))[0]>180 and preview.getpixel((400,split+400))[2]>180
        first,entrance,settled = [frame(cues[1]['start']+offset) for offset in (-.1,.02,.3)]
        panel=(0,0,1080,split-8)
        def difference(a,b):
            return sum(ImageStat.Stat(ImageChops.difference(a.crop(panel),b.crop(panel))).mean)
        assert difference(settled,preview)<8
        assert difference(entrance,settled)>2
        assert difference(first,settled)>4
        assert settled.getpixel((400,split+400))[2]>170
        assert renderer.conversation_frame(story,2,'messages',size=(1080,1920),gameplay_percent=60)
        sequence = root/'three-colors.mp4'
        run('ffmpeg','-y','-loglevel','error','-f','lavfi','-i','color=red:s=180x320:d=1:r=30',
            '-f','lavfi','-i','color=lime:s=180x320:d=1:r=30','-f','lavfi','-i','color=blue:s=180x320:d=1:r=30',
            '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0','-c:v','libx264',sequence)
        with patch.object(renderer,'create_voice',side_effect=synthetic_voice) as narration:
            try:
                renderer.generate(story,root/'bad-cut',background=sequence,gameplay_trim_start=2.8,gameplay_trim_end=3)
            except ValueError as exc:
                assert '0.5 seconds' in str(exc)
            else:
                raise AssertionError('Invalid trim was accepted')
            assert not narration.called
            trimmed = renderer.generate(story,root/'trimmed',background=sequence,gameplay_trim_start=1.2,gameplay_trim_end=1.9)
        for time in ('0.15','0.80','2.15'):
            rgb=run('ffmpeg','-v','error','-ss',time,'-i',trimmed['path'],'-frames:v','1',
                    '-vf','crop=2:2:400:900','-f','rawvideo','-pix_fmt','rgb24','-threads','1','-')
            assert rgb[1] > 215 and rgb[0] < 35 and rgb[2] < 35, (time,tuple(rgb[:3]))
        print('PASS trimmed gameplay loops only the selected interval; invalid input rejected before narration')
        print(f'PASS native {picture["width"]}×{picture["height"]}, {picture["r_frame_rate"]} FPS, '
              f'video {picture["duration"]} s / audio {audio["duration"]} s, '
              f'preview parity, crop, animated reveal and voice order')
        return first,entrance,settled


if __name__=='__main__':
    main()
