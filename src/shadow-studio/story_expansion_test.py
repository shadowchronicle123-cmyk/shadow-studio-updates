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
        for genre in GENRES:
            short = client.post('/api/story', json={**payload, 'genre': genre, 'target': 30}).json()['story']
            long = client.post('/api/story', json={**payload, 'genre': genre, 'target': 60}).json()['story']
            assert short['genre'] == genre and len(short['lines']) < len(long['lines'])
        assert client.post('/api/story', json={**payload, 'genre': 'invalid'}).status_code == 400
        assert client.post('/api/story', json={'genre': 'horror'}).status_code == 200
    with patch.dict('os.environ', {'SHADOW_OLLAMA_MODEL': ''}):
        assert client.post('/api/story', json={**payload, 'provider': 'ollama'}).status_code == 400
    print('PASS custom premise, four distinct reveals and endings, speakers, durations and provider boundaries')


if __name__ == '__main__':
    main()
