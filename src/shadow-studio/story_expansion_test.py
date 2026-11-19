"""Offline API acceptance for custom story creation and regeneration."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
from story_expansion import GENRES


def main():
    client = TestClient(app.app)
    premise = "My girlfriend invited her ex to our wedding. The wedding is on Saturday at the Lake House."
    payload = {'idea': premise, 'mode': 'custom', 'genre': 'drama', 'target': 45}
    avoid, twists, endings = [], set(), set()
    with patch('story_engine._local_json', side_effect=AssertionError('Offline generation called a provider')):
        for _ in range(4):
            response = client.post('/api/story', json={**payload, 'avoid': avoid})
            assert response.status_code == 200, response.text
            story = response.json()['story']
            lines = story['lines']
            assert story['source'] == 'offline_expansion'
            assert any(premise == line['text'] for line in lines)
            assert story['variant'] not in avoid
            assert len({line['speaker'] for line in lines}) == 2
            assert all(0 < len(line['text']) <= 240 and 0 < len(line['speaker']) <= 24 for line in lines)
            twists.add(next(line['text'] for line in lines if line['pause_before'] > 0))
            endings.add(lines[-1]['text'])
            avoid.append(story['variant'])
        assert len(twists) == len(endings) == 4
        for _ in range(4):
            extra = client.post('/api/story',json={**payload,'avoid':avoid}).json()['story']
            assert extra['variant'] not in avoid
            assert extra['lines'][-1]['text'] not in endings
            avoid.append(extra['variant'])
            endings.add(extra['lines'][-1]['text'])
        for genre in GENRES:
            short = client.post('/api/story', json={**payload, 'genre': genre, 'target': 30}).json()['story']
            long = client.post('/api/story', json={**payload, 'genre': genre, 'target': 60}).json()['story']
            assert short['genre'] == genre and len(short['lines']) < len(long['lines'])
        assert client.post('/api/story', json={**payload, 'genre': 'invalid'}).status_code == 400
        assert client.post('/api/story', json={'genre': 'horror'}).status_code == 200
    for idea, expected in [('My roommate overcharged me for our rent in October.', 'Roommate'),
                           ('My coworker blamed me for a mistake during second shift.', 'Coworker')]:
        seen = []
        for _ in range(3):
            story = client.post('/api/story',json={**payload,'idea':idea,'avoid':seen}).json()['story']
            assert any(line['text']==idea for line in story['lines'])
            assert {line['speaker'] for line in story['lines']} == {'Me',expected}
            assert story['variant'].split(':')[0] not in {variant.split(':')[0] for variant in seen}
            seen.append(story['variant'])
    with patch.dict('os.environ', {'SHADOW_OLLAMA_MODEL': ''}):
        assert client.post('/api/story', json={**payload, 'provider': 'ollama'}).status_code == 400
    premium='elevenlabs:fakeIdentifier'
    with patch('app.valid_voice',side_effect=AssertionError('Unapproved request checked a premium voice')):
        assert client.post('/api/voice-preview',json={'voice':premium}).status_code==409
        assert client.post('/api/render',json={'story':{},'second_voice':premium}).status_code==409
        assert client.post('/api/batch-random',json={'voice':premium}).status_code==409
    print('PASS custom premise, four distinct reveals and endings, speakers, durations and provider boundaries')


if __name__ == '__main__':
    main()
