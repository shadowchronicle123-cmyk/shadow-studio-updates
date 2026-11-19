"""Offline story-to-video engine. Requires installed FFmpeg; voice is local OS TTS."""
import io, os, math, wave, shutil, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from voice_engine import create_voice
from audio_mix import line_sound, mix_audio
from captions import save_scene_subtitles

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


def conversation_frame(story, index, style, *, size=(W,H), gameplay_percent=0, entrance=1):
    """Compose the same fully settled scene used by the storyboard and MP4."""
    width, height = size
    scale = width / W
    unit = lambda value: round(value * scale)
    panel = gameplay_dimensions(height, gameplay_percent)[0] if gameplay_percent else height
    image = Image.new('RGB', size, '#101422')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, unit(65)), fill='#151a29')
    draw.ellipse((unit(21), unit(17), unit(54), unit(50)), fill='#bdffdf')
    draw.text((unit(29), unit(19)), 'S', font=font(unit(22),True), fill='#172539')
    header = 'ShadowChronicles' if style == 'messages' else 'ShadowChronicles / Stories'
    draw.text((unit(66), unit(23)), header, font=font(unit(23),True), fill='#f4fff9')
    draw.line((0, unit(64), width, unit(64)), fill='#3a465c', width=max(1,unit(1)))
    if gameplay_percent:
        draw.rectangle((0, panel-unit(4), width, panel), fill='#89e9be')
    if index < 0:
        return image

    rows = story['lines'][:index+1]
    if style == 'reddit':
        rows = rows[-1:]
    keys = list(dict.fromkeys(str(line.get('speaker','Narrator')).strip().casefold() for line in story['lines']))
    colors = ('#244b40', '#342c57', '#234461', '#594128', '#62394f', '#375164')
    labels = ('#b0f6d6', '#e2d5ff', '#c9ebff', '#ffe0ae', '#ffd7ed', '#d8f2ff')
    max_width = unit(422 if style == 'messages' else 456)
    pad = unit(15)
    floor = panel-unit(18)
    top = unit(79)
    bubbles = []
    total = 0
    for line in reversed(rows[-5:]):
        name = str(line.get('speaker','Narrator')).strip() or 'Narrator'
        ordinal = keys.index(str(line.get('speaker','Narrator')).strip().casefold())
        for size_px in range(26, 13, -1):
            body = font(unit(size_px))
            wrapped = text_wrap(draw, line['text'], body, max_width-2*pad)
            row_height = unit(size_px+6)
            bubble_height = unit(43)+len(wrapped)*row_height
            if bubble_height <= floor-top and (not bubbles or size_px >= 24):
                break
        if total+bubble_height > floor-top:
            break
        actual_width = max(unit(135), min(max_width,
            max([draw.textlength(row,font=body) for row in wrapped] +
                [draw.textlength(name,font=font(unit(16),True))])+2*pad))
        bubbles.append((name,ordinal,wrapped,body,row_height,bubble_height,round(actual_width)))
        total += bubble_height+unit(10)
    if not bubbles:
        raise ValueError('A dialogue line could not fit. Shorten the line or use a smaller gameplay panel.')

    if not gameplay_percent:
        title = str(story.get('title','')).replace('#Shorts','').strip()
        heading = text_wrap(draw,title,font(unit(31),True),unit(464))[:3]
        if floor-total > unit(260):
            for row, line in enumerate(heading):
                draw.text((unit(30),unit(108+39*row)),line,font=font(unit(31),True),fill='#ecfff8')

    y = floor-total+unit(10)
    for position, (name, ordinal, wrapped, body, row_height, bubble_height, bubble_width) in enumerate(reversed(bubbles)):
        last = position==len(bubbles)-1
        right = ordinal%2==0 and style=='messages'
        x = width-unit(23)-bubble_width if right else unit(23)
        slide = unit(14)*(1-entrance)*(-1 if not right else 1) if last else 0
        x += round(slide)
        layer = Image.new('RGBA',size) if last and entrance<1 else None
        # Use a temporary layer for the new bubble only so earlier dialogue stays legible.
        pen = ImageDraw.Draw(layer) if layer is not None else draw
        fill = colors[ordinal%len(colors)]
        label = labels[ordinal%len(labels)]
        pen.rounded_rectangle((x,y,x+bubble_width,y+bubble_height),radius=unit(20),fill=fill,outline=label,width=max(1,unit(1)))
        pen.text((x+pad,y+unit(8)),name,font=font(unit(16),True),fill=label)
        for row, line in enumerate(wrapped):
            pen.text((x+pad,y+unit(30)+row_height*row),line,font=body,fill='#f9fffb')
        if layer is not None:
            alpha = layer.getchannel('A').point(lambda value: round(value*entrance))
            image.paste(layer, (0,0), alpha)
        y += bubble_height+unit(10)
    return image


def line_pause(line):
    """Seconds of silence immediately before this line appears and is spoken."""
    try:
        pause = float(line.get('pause_before', 0))
    except (TypeError, ValueError) as exc:
        raise ValueError('Line pauses must be between 0 and 10 seconds') from exc
    if not 0 <= pause <= 10:
        raise ValueError('Line pauses must be between 0 and 10 seconds')
    return pause


def gameplay_dimensions(height, gameplay_percent=58):
    panel = round(height * (100-gameplay_percent) / 200) * 2
    return panel, height - panel


def gameplay_crop(width, height, anchor='center'):
    y = {'top':'0', 'bottom':'ih-oh', 'center':'(ih-oh)/2'}[anchor]
    return (f'scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,'
            f'crop={width}:{height}:(iw-ow)/2:{y},setsar=1')


def preview_layout(story, index, style, *, show_gameplay=False, background=None, start=0,
                   gameplay_percent=58, gameplay_anchor='center', resolution='720p'):
    """Render the exact export layout without synthesizing narration."""
    size = (1080,1920) if resolution=='1080p' else (720,1280)
    width,height = size
    panel_height,gameplay_height = gameplay_dimensions(height,gameplay_percent)
    frame = conversation_frame(story,index,style,size=size,gameplay_percent=gameplay_percent if show_gameplay else 0)
    if show_gameplay and background:
        with tempfile.TemporaryDirectory(prefix='shadow_layout_') as folder:
            sample = Path(folder)/'gameplay.png'
            for seek in (start,0):
                args = ['ffmpeg','-y','-loglevel','error','-threads','1','-ss',str(seek),'-i',str(background),
                        '-map','0:v:0','-frames:v','1','-filter_threads','1','-vf',
                        gameplay_crop(width,gameplay_height,gameplay_anchor),'-threads','1',str(sample)]
                result = subprocess.run(args,capture_output=True,text=True,timeout=15)
                if result.returncode==0 and sample.is_file():
                    break
            else:
                raise RuntimeError('Could not preview the selected gameplay clip')
            with Image.open(sample) as gameplay:
                frame.paste(gameplay,(0,panel_height))
    elif show_gameplay:
        draw = ImageDraw.Draw(frame)
        draw.rectangle((0,panel_height,width,height),fill='#263549')
        draw.text((28,panel_height+32),'Gameplay placement preview',font=font(26,True),fill='white')
    output = io.BytesIO()
    frame.save(output,format='PNG')
    return output.getvalue()


SCENE_PACING = {"snappy": (0.2, 1.2), "normal": (0.5, 2.25), "deliberate": (0.85, 2.6)}


def dialogue_holds(narration, gap, minimum_hold, target_seconds=None):
    """Return scene holds in frames, adjusting only silence when fitting a target."""
    base = [math.ceil(max(spoken + gap, minimum_hold) * 30) for _, spoken in narration]
    if target_seconds is None:
        return base
    pause_frames = sum(round(pause * 30) for pause, _ in narration)
    delta = round(target_seconds * 30) - pause_frames - sum(base)
    if not delta:
        return base
    if delta > 0:
        capacity = [33] * len(base)
    else:
        capacity = [max(0, hold - math.ceil((spoken + 0.1) * 30))
                    for hold, (_, spoken) in zip(base, narration)]
    available = sum(capacity)
    if not available:
        return base
    change = min(abs(delta), available)
    sign = 1 if delta > 0 else -1
    previous, cumulative = 0, 0
    for index, share in enumerate(capacity):
        cumulative += share
        allocated = round(change * cumulative / available)
        base[index] += sign * (allocated - previous)
        previous = allocated
    return base



def generate(story,output_dir,style='messages',background=None,progress=None,voice='en-US-AriaNeural',second_voice=None,voice_rate=0,resolution='720p',gameplay_start=0,*,pacing='normal',captions=True,
             music=None,voice_volume=100,music_volume=22,target_seconds=None,gameplay_percent=58,gameplay_anchor="center"):
    gap, minimum_hold = SCENE_PACING[pacing]
    if not isinstance(story, dict) or not isinstance(story.get('lines'), list) or not 1 <= len(story['lines']) <= 24:
        raise ValueError('Story requires 1 to 24 narration scenes')
    for line in story['lines']:
        if not isinstance(line, dict) or not str(line.get('text','')).strip():
            raise ValueError('Each narration scene needs non-empty text')
        line_pause(line)
        line_sound(line)
    if resolution not in ('720p','1080p'):raise ValueError('Choose 720p or 1080p')
    out_width,out_height=(720,1280) if resolution=='720p' else (1080,1920)
    has_gameplay=bool(background)
    layout={'size':(out_width,out_height),'gameplay_percent':gameplay_percent if has_gameplay else 0}
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise RuntimeError('FFmpeg and ffprobe are required on PATH')
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    first_speaker=str(story['lines'][0].get('speaker','Narrator')).strip().casefold()
    if second_voice is not None:
        from voice_engine import valid_voice
        if not valid_voice(second_voice): raise ValueError('Second character voice is unavailable')
    with tempfile.TemporaryDirectory(prefix='shadow_') as temp:
        tmp=Path(temp);concat=[]; wavs=[]; durations=[]; narration=[]; images=[]
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
            with wave.open(str(normalized), 'rb') as pcm:
                spoken = pcm.getnframes() / pcm.getframerate()
            offset = round(line_pause(line) * 30) / 30
            narration.append((offset, spoken))
            wavs.append(normalized)
            img=tmp/f'scene_{i:03}.png'
            conversation_frame(story,i,style,**layout).save(img)
            images.append(img)
        holds = dialogue_holds(narration, gap, minimum_hold, target_seconds)
        for i, (img, (offset, _), frames) in enumerate(zip(images, narration, holds)):
            hold = frames / 30
            durations.append(offset + hold)
            if offset:
                previous = tmp / f'scene_{i-1:03}.png'
                if i == 0:
                    previous = tmp / 'opening.png'
                    conversation_frame(story,-1,style,**layout).save(previous)
                concat.append((previous, offset))
            # Six short animation frames start at the voice/caption cue.
            count = min(6, frames-1)
            for frame in range(count):
                animated = tmp/f'entrance_{i:03}_{frame}.png'
                phase = 1-(1-(frame+1)/(count+1))**2
                conversation_frame(story,i,style,entrance=phase,**layout).save(animated)
                concat.append((animated,1/30))
            concat.append((img,(frames-count)/30))
        captions_path = output_dir / 'captions.srt'
        if captions:
            save_scene_subtitles(story, durations, captions_path, narration)
        else:
            captions_path.unlink(missing_ok=True)
        listfile=tmp/'scenes.txt'
        def escaped(p):return p.as_posix().replace("'", "'\\''")
        lines=[]
        for img,dur in concat:lines.extend(["file '"+escaped(img)+"'","option framerate 30",f'duration {dur:.6f}'])
        lines.extend(["file '"+escaped(concat[-1][0])+"'", "option framerate 30"])
        listfile.write_text('\n'.join(lines),encoding='utf-8')
        if progress:progress(65,'Rendering vertical video')
        visual=tmp/'visual.mp4'
        run(['ffmpeg','-y','-loglevel','error','-safe','0','-f','concat','-i',listfile,'-vf','fps=30,format=yuv420p','-c:v','libx264','-preset','ultrafast','-crf','24','-pix_fmt','yuv420p',visual])
        audio = tmp / 'full.wav'
        with wave.open(str(audio), 'wb') as track:
            track.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            elapsed = 0.0
            cues = []
            samples_written = 0
            for line, wav, duration, (offset, _) in zip(story['lines'], wavs, durations, narration):
                effect, adjustment = line_sound(line)
                if effect != 'none':
                    cues.append((effect, elapsed + offset + adjustment))
                start = round((elapsed + offset) * 16000)
                track.writeframes(b'\0\0' * (start - samples_written))
                with wave.open(str(wav), 'rb') as speech:
                    track.writeframes(speech.readframes(speech.getnframes()))
                    samples_written = start + speech.getnframes()
                elapsed += duration
                end = round(elapsed * 16000)
                track.writeframes(b'\0\0' * (end - samples_written))
                samples_written = end
        audio = mix_audio(audio, music, cues, sum(durations), voice_volume, music_volume)
        output=output_dir/'short.mp4'
        args=['ffmpeg','-y','-loglevel','error','-i',visual,'-i',audio]
        if background and Path(background).is_file():
            panel_height, gameplay_height = gameplay_dimensions(out_height,gameplay_percent)
            crop = gameplay_crop(out_width,gameplay_height,gameplay_anchor)
            filters = []
            if gameplay_start > 0:
                # Seek only the first play. Subsequent plays start at frame zero.
                args.extend(['-ss', str(gameplay_start), '-i', str(background)])
                args.extend(['-stream_loop', '-1', '-i', str(background)])
                filters.extend([
                    f'[2:v]{crop},fps=30,setpts=PTS-STARTPTS[remaining]',
                    f'[3:v]{crop},fps=30,setpts=PTS-STARTPTS[full]',
                    '[remaining][full]concat=n=2:v=1:a=0[gameplay]',
                ])
            else:
                args.extend(['-stream_loop', '-1', '-i', str(background)])
                filters.append(f'[2:v]{crop},fps=30[gameplay]')
            filters.extend([
                f'[0:v]scale={out_width}:{out_height}:flags=lanczos,'
                f'crop={out_width}:{panel_height}:0:0,setsar=1[panel]',
                '[panel][gameplay]vstack=shortest=1,format=yuv420p[v]',
            ])
            args.extend(['-filter_complex_threads','1','-filter_complex',';'.join(filters),
                         '-map','[v]','-map','1:a'])
        else:
            args.extend(['-map','0:v','-map','1:a','-vf',f'scale={out_width}:{out_height}:flags=lanczos,setsar=1'])
        args+=['-r','30','-c:v','libx264','-preset','veryfast','-crf','25','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-t',f'{sum(durations):.3f}','-movflags','+faststart',output]
        if progress:progress(85,'Finalizing voiceover and MP4')
        run(args)
        if progress:progress(100,'Video ready for review')
        return {'path':str(output),'duration':round(sum(durations),1),'resolution':resolution,'fps':30,'captions_path':str(captions_path) if captions else None}
