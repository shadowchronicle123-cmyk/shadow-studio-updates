"""Create vertical ranked countdowns from owner-supplied clips."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import subprocess, tempfile, uuid


def _font(size):
    for p in ('C:/Windows/Fonts/arialbd.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        if Path(p).is_file(): return ImageFont.truetype(str(p),size)
    return ImageFont.load_default()


def _label(dest,rank,title):
    image=Image.new('RGBA',(720,1280),(0,0,0,0))
    d=ImageDraw.Draw(image)
    d.rounded_rectangle((30,68,690,226),radius=24,fill=(7,13,31,230),outline=(100,177,253,255),width=3)
    d.rounded_rectangle((48,88,148,207),radius=18,fill=(28,113,232,255))
    d.text((98,145),str(rank),font=_font(65),anchor='mm',fill='white')
    label=title[:38] if title else f'Clip {rank}'
    if len(label)>23: label=label[:22]+'…'
    d.text((170,145),label,font=_font(30),anchor='lm',fill='white')
    d.rounded_rectangle((30,1130,690,1207),radius=18,fill=(7,13,31,210))
    d.text((360,1168),'SHADOW STUDIO  •  RANKING',font=_font(22),anchor='mm',fill=(201,224,250,255))
    image.save(dest)


def rank_videos(entries,output_dir:Path,seconds_each=4,reverse=True):
    if not 2<=len(entries)<=5: raise ValueError('Choose 2 to 5 clips')
    if not 2<=seconds_each<=12: raise ValueError('Clip length must be 2 to 12 seconds')
    output_dir.mkdir(parents=True,exist_ok=True)
    result=output_dir/(uuid.uuid4().hex[:12]+'.mp4')
    ordered=list(entries)
    if reverse: ordered=list(reversed(ordered))
    with tempfile.TemporaryDirectory(prefix='shadow-rank-') as tmp:
        tmp=Path(tmp);segments=[]
        try:
            for idx,(path,title) in enumerate(ordered):
                if not path.is_file():raise ValueError('Uploaded clip missing')
                rank=(len(entries)-idx if reverse else idx+1)
                overlay=tmp/f'label_{idx}.png';_label(overlay,rank,title)
                segment=tmp/f'part_{idx}.mp4'
                cmd=['ffmpeg','-nostdin','-y','-hide_banner','-loglevel','error','-stream_loop','-1','-i',str(path),'-loop','1','-i',str(overlay),'-filter_complex',
                     '[0:v]fps=30,scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[bg];[bg][1:v]overlay=0:0:shortest=1,format=yuv420p[v]',
                     '-map','[v]','-t',str(seconds_each),'-an','-c:v','libx264','-preset','veryfast','-crf','25','-pix_fmt','yuv420p','-movflags','+faststart',str(segment)]
                run=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
                if run.returncode:raise RuntimeError((run.stderr or 'Rank rendering failed')[-600:])
                segments.append(segment)
            concat=tmp/'list.txt'
            concat.write_text(''.join('file '+repr(str(p))+'\n' for p in segments))
            run=subprocess.run(['ffmpeg','-nostdin','-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(result)],capture_output=True,text=True,timeout=90)
            if run.returncode:raise RuntimeError((run.stderr or 'Could not join clips')[-600:])
            if not result.is_file() or result.stat().st_size<1000:raise RuntimeError('No video created')
            return result
        except Exception:
            result.unlink(missing_ok=True)
            raise
