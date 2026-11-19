"""Use real Windows offline narration. Run only on Windows."""
import os
from pathlib import Path
import tempfile

import renderer
from captions import read_captions
from quality_check import analyze


def main():
    assert os.name == 'nt', 'This test requires Windows'
    story = {'title':'Windows offline voice', 'lines':[
        {'speaker':'Riley','text':'The wedding planner printed one extra place card.'},
        {'speaker':'Avery','text':'I found the name on the back of the letter.','pause_before':.5},
    ]}
    with tempfile.TemporaryDirectory(prefix='shadow_windows_voice_') as folder:
        root = Path(folder)
        result = renderer.generate(story,root,voice='windows-default',second_voice='windows-default',resolution='1080p')
        report = analyze(result['path'])
        assert report['passed'] and report['audio_present'], report
        assert (report['width'],report['height'])==(1080,1920), report
        cues = read_captions(root/'captions.srt')
        assert cues[1]['start']>cues[0]['end']+.49, cues
    print('PASS native Windows SAPI narration, non-silent 1080p MP4, and timed dialogue pause')


if __name__=='__main__':
    main()
