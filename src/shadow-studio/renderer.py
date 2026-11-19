"""Offline story-to-video engine. Requires installed FFmpeg; voice is local OS TTS."""
import os, math, wave, shutil, subprocess, tempfile, textwrap, audioop
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from voice_engine import create_voice

W,H=540,960
FONT_PATHS=['C:/Windows/Fonts/arial.ttf','C:/Windows/Fonts/segoeui.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
FONT_BOLD=['C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
def font(size,bold=False):
    for f in ((FONT_BOLD+FONT_PATHS) if bold else FONT_PATHS):
        if Path(f).exists():return ImageFont.truetype(f,size)
    return ImageFont.load_default()
def run(args):
    p=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-1800:])

def tts(text,path):
    """Generate local speech, not silent pretending to speak."""
    if os.name=='nt':
        ps=path.with_suffix('.ps1')
        safe=text.replace("'","''")
        ps.write_text("Add-Type -AssemblyName System.Speech\n$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n$s.Rate = 2\n$s.SetOutputToWaveFile('"+str(path).replace("'","''")+"')\n$s.Speak('"+safe+"')\n$s.Dispose()\n",encoding='utf-8-sig')
        run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',ps])
    elif shutil.which('espeak'):
        run(['espeak','-s','155','-w',path,text])
    else: raise RuntimeError('No local speech engine. On Windows install Windows speech voices. On Linux install espeak.')

def audio_duration(path):
    p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True,check=True)
    return float(p.stdout.strip())

def validate_narration_audio(wav_path, *, min_seconds=0.25, min_dbfs=-47.0):
    """Fail a render when a scene's generated narration is empty or effectively silent.

    Called on normalized mono 16-bit PCM WAV, so the amplitude check is comparable
    across ElevenLabs, Edge and Windows voices. Does not assert human-like quality.
    """
    with wave.open(str(wav_path), 'rb') as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() <= 0:
            raise RuntimeError('Narration is not a normalized mono 16-bit WAV')
        duration = audio.getnframes() / audio.getframerate()
        if duration < min_seconds:
            raise RuntimeError('Narration audio is too short to use')
        samples = audio.readframes(audio.getnframes())
    from array import array
    values = array("h")
    values.frombytes(samples)
    if not values:
        raise RuntimeError("Narration contains no audio samples")
    peak = max(abs(x) for x in values)
    rms = math.sqrt(sum(x*x for x in values) / len(values))
    dbfs = 20 * math.log10(max(rms, 1) / 32768)
    if peak == 0 or dbfs < min_dbfs:
        raise RuntimeError(f'Narration is silent or too quiet ({dbfs:.1f} dBFS). Preview another voice before rendering.')
    return {'duration_seconds':round(duration,3), 'rms_dbfs':round(dbfs,1), 'peak':peak}


def text_wrap(draw,text,ft,max_width):
    """Pixel-fit text, including unusually long unbroken words and links."""
    words = str(text).split()
    out=[]; current=''
    for word in words:
        # Break a single token when it cannot fit on a line by itself.
        chunks=[]; rest=word
        while rest:
            n=len(rest)
            while n>1 and draw.textbbox((0,0),rest[:n],font=ft)[2] > max_width:
                n-=1
            chunks.append(rest[:n]);rest=rest[n:]
        for chunk in chunks:
            combined=(current+' '+chunk).strip()
            if current and draw.textbbox((0,0),combined,font=ft)[2]>max_width:
                out.append(current);current=chunk
            else:
                current=combined
    if current:out.append(current)
    return out


def _visible_messages(draw, lines, ft, width, available_height, *, row_height, header_height, gap, max_lines):
    """Keep the newest message visible; hide earlier bubbles before overflow."""
    visible=[];total=0
    for item in reversed(lines):
        rows=text_wrap(draw, item['text'], ft, width)
        if len(rows)>max_lines:
            rows=rows[:max_lines]
            rows[-1]=rows[-1].rstrip(' .') + '…'
        height=header_height + row_height * len(rows)
        cost=height + (gap if visible else 0)
        if total+cost>available_height:
            if visible:break
            # Latest message is mandatory. Trim its visible rows if necessary.
            rows=rows[:max(1,int((available_height-header_height)//row_height))]
            rows[-1]=rows[-1].rstrip(' .')+'…'
            height=header_height+row_height*len(rows)
            cost=height
        visible.append((item,rows,height))
        total+=cost
    return list(reversed(visible)),total

def png_frame(path,story,index,style):
    im=Image.new('RGB',(W,H),(12,17,31));d=ImageDraw.Draw(im)
    # Original gradient background, no copyrighted gameplay required.
    for y in range(H):
        c=(int(13+22*y/H),int(9+8*y/H),int(16+19*y/H))
        d.line((0,y,W,y),fill=c)
    title='SHADOWCHRONICLES'; d.text((26,38),title,font=font(26,True),fill='#fff0f1')
    d.rounded_rectangle((20,96,W-20,H-125),radius=25,fill='#190d15',outline='#623246',width=2)
    d.text((40,126),'FICTIONAL STORY  •  PART 1',font=font(16,True),fill='#d98ca0')
    displayed=story['lines'][:index+1]
    if style=='reddit':
        current=displayed[-1]
        d.text((44,210),'r/ShadowChronicles',font=font(24,True),fill='#ffd8de')
        block=text_wrap(d,current['text'],font(35,True),440)
        for j,line in enumerate(block[:11]):d.text((46,290+49*j),line,font=font(35,True),fill='white')
    else:
        bubbles,total=_visible_messages(d,displayed[-5:],font(24),376,598,
                                        row_height=34,header_height=50,gap=18,max_lines=10)
        y=max(202,800-total)
        if y > 485:
            # When only a few messages are on screen, use the open area for a
            # cinematic story heading rather than leaving a large blank panel.
            headline = text_wrap(d, str(story.get('title','STORY TIME')).replace('#Shorts','').strip(), font(32, True), 445)[:3]
            for j, text_line in enumerate(headline):
                d.text((43, 240 + j * 44), text_line, font=font(32,True), fill='#fbe5e9')
            d.rounded_rectangle((43, 400, 142, 405), radius=2, fill='#ba3157')
        for item,lines,height in bubbles:
            me=item['speaker'].lower() in ('me','narrator')
            x=96 if me else 36; right=502 if me else 442
            d.rounded_rectangle((x,y,right,y+height),radius=20,fill=('#9d2646' if me else '#33212d'))
            d.text((x+16,y+9),item['speaker'],font=font(17,True),fill='#ffe2e9')
            for j,line in enumerate(lines):d.text((x+16,y+35+34*j),line,font=font(24),fill='white')
            y+=height+18
    d.rounded_rectangle((28,H-98,W-28,H-35),radius=18,fill='#711b39')
    d.text((49,H-81),'COMMENT WHAT YOU WOULD DO  ↓',font=font(20,True),fill='#ffffff')
    im.save(path)

def gameplay_frame(path,story,index,style):
    """Text panel positioned below unobscured top-of-screen gameplay."""
    im=Image.new('RGB',(W,H),'#110b11');d=ImageDraw.Draw(im)
    d.rectangle((0,385,W,H),fill='#160d16')
    d.text((22,415),'SHADOWCHRONICLES  •  STORY',font=font(19,True),fill='#f5b6c4')
    d.rounded_rectangle((18,466,W-18,H-112),radius=18,fill='#26121e',outline='#6a304b',width=2)
    # Only show the most recent messages to keep dialogue in the lower half.
    recent=story['lines'][max(0,index-2):index+1]
    blocks,total=_visible_messages(d,recent,font(23),365,335,
                                   row_height=32,header_height=44,gap=12,max_lines=6)
    y=max(500,835-total)
    for item,rows,height in blocks:
        me=item['speaker'].lower() in ('me','narrator')
        x=92 if me else 32; right=502 if me else 448
        d.rounded_rectangle((x,y,right,y+height),radius=15,fill='#97223f' if me else '#34455e')
        d.text((x+12,y+7),item['speaker'],font=font(16,True),fill='#ffe6ec')
        for j,line in enumerate(rows):d.text((x+12,y+30+32*j),line,font=font(23),fill='white')
        y+=height+12
    d.rounded_rectangle((22,H-94,W-22,H-25),radius=12,fill='#711b39')
    d.text((38,H-72),'WHAT WOULD YOU DO?  ↓',font=font(22,True),fill='white')
    im.save(path)

def _srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{millis:03}'


def save_scene_subtitles(story, durations, destination):
    """One timestamped cue per narrated scene, aligned to the generated audio timeline.

    This SRT is editable in conventional video editors; it isn't word-aligned.
    """
    position = 0.0
    blocks = []
    for i, (line, duration) in enumerate(zip(story['lines'], durations), 1):
        text = ' '.join(str(line['text']).split())
        if not text:
            position += duration
            continue
        end = position + duration
        blocks.append(f'{i}\n{_srt_time(position)} --> {_srt_time(end)}\n{text}\n')
        position = end
    Path(destination).write_text('\n'.join(blocks), encoding='utf-8')


def generate(story,output_dir,style='messages',background=None,progress=None,voice='en-US-AriaNeural',second_voice=None,voice_rate=0,resolution='720p',gameplay_start=0):
    if not isinstance(story, dict) or not isinstance(story.get('lines'), list) or not 1 <= len(story['lines']) <= 24:
        raise ValueError('Story requires 1 to 24 narration scenes')
    for line in story['lines']:
        if not isinstance(line, dict) or not str(line.get('text','')).strip():
            raise ValueError('Each narration scene needs non-empty text')
    if resolution not in ('720p','1080p'):raise ValueError('Choose 720p or 1080p')
    out_width,out_height=(720,1280) if resolution=='720p' else (1080,1920)
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('FFmpeg and ffprobe are required on PATH')
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    first_speaker=str(story['lines'][0].get('speaker','Narrator')).strip().casefold()
    if second_voice is not None:
        from voice_engine import valid_voice
        if not valid_voice(second_voice): raise ValueError('Second character voice is unavailable')
    with tempfile.TemporaryDirectory(prefix='shadow_') as temp:
        tmp=Path(temp);concat=[]; wavs=[]; durations=[]
        for i,line in enumerate(story['lines']):
            if progress:progress(10+int(i/max(len(story['lines']),1)*45),f'Creating scene {i+1}/{len(story["lines"])}')
            wav=tmp/f'voice_{i:03}.wav'
            who=str(line.get('speaker','Narrator')).strip().casefold()
            selected=voice if who==first_speaker else (second_voice or voice)
            create_voice(line['text'],wav,selected,voice_rate)
            # Normalize all voice clips into PCM 16k for concatenation.
            normalized=tmp/f'a_{i:03}.wav'
            run(['ffmpeg','-y','-loglevel','error','-i',wav,'-ar','16000','-ac','1','-c:a','pcm_s16le',normalized])
            validate_narration_audio(normalized)
            duration=max(audio_duration(normalized)+0.5,2.25);durations.append(duration);wavs.append(normalized)
            img=tmp/f'scene_{i:03}.png'
            if background:gameplay_frame(img,story,i,style)
            else:png_frame(img,story,i,style)
            concat.append((img,duration))
        save_scene_subtitles(story, durations, output_dir/'captions.srt')
        listfile=tmp/'scenes.txt'
        def escaped(p):return str(p).replace("'", "'\\''")
        lines=[]
        for img,dur in concat:lines.extend(["file '"+escaped(img)+"'",f'duration {dur:.3f}'])
        lines.append("file '"+escaped(concat[-1][0])+"'")
        listfile.write_text('\n'.join(lines),encoding='utf-8')
        if progress:progress(65,'Rendering vertical video')
        visual=tmp/'visual.mp4'
        run(['ffmpeg','-y','-loglevel','error','-safe','0','-f','concat','-i',listfile,'-vf','fps=30,format=yuv420p','-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p',visual])
        # Audio each speech followed by exactly enough silence for that scene.
        audio_list=tmp/'audio.txt'; paths=[]
        for i,(wav,dur) in enumerate(zip(wavs,durations)):
            segment=tmp/f'segment_{i:03}.wav'
            run(['ffmpeg','-y','-loglevel','error','-i',wav,'-af',f'apad=whole_dur={dur:.3f}','-t',f'{dur:.3f}','-ar','16000','-ac','1',segment]); paths.append(segment)
        audio_list.write_text('\n'.join("file '"+escaped(x)+"'" for x in paths),encoding='utf-8')
        audio=tmp/'full.wav';run(['ffmpeg','-y','-loglevel','error','-safe','0','-f','concat','-i',audio_list,'-c','copy',audio])
        output=output_dir/'short.mp4'
        args=['ffmpeg','-y','-loglevel','error','-i',visual,'-i',audio]
        if background and Path(background).is_file():
            # Background is owned/licensed by the user. Compose an inset gameplay strip
            # while preserving text readability; scale inside filter_complex.
            # Gameplay occupies the TOP 40%, story panel the BOTTOM 60%.
            # The previous renderer put an opaque story layer over the gameplay.
            top=int(out_height*0.4)
            lower=out_height-top
            filter_graph=(f'[2:v]scale={out_width}:{out_height}:force_original_aspect_ratio=increase,'
                          f'crop={out_width}:{out_height},setsar=1[bg];'
                          f'[0:v]scale={out_width}:{out_height}:flags=lanczos,'
                          f'crop={out_width}:{lower}:0:{top},setsar=1[panel];'
                          f'[bg][panel]overlay=0:{top}:shortest=1,format=yuv420p[v]')
            args.extend(['-stream_loop','-1','-ss',str(max(0,gameplay_start)),'-i',str(background),'-filter_complex',filter_graph,'-map','[v]','-map','1:a'])
        else:
            args.extend(['-map','0:v','-map','1:a','-vf',f'scale={out_width}:{out_height}:flags=lanczos,setsar=1'])
        args+=['-r','30','-c:v','libx264','-preset','veryfast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-t',f'{sum(durations):.3f}','-movflags','+faststart',output]
        if progress:progress(85,'Finalizing voiceover and MP4')
        run(args)
        if progress:progress(100,'Video ready for review')
        return {'path':str(output),'duration':round(sum(durations),1),'resolution':resolution,'fps':30,'captions_path':str(output_dir/'captions.srt')}
