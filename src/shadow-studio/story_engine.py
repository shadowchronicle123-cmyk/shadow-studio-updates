"""Original templates only. Ollama option stays on local machine."""
import os, json, random, re, requests
from story_presets import STORIES

def make_story(idea, style="messages", target=45, ending="resolved"):
    idea = re.sub(r'\s+', ' ',idea).strip()[:350]
    if not idea: raise ValueError('Story idea cannot be empty')
    model = os.getenv('SHADOW_OLLAMA_MODEL','').strip()
    if model:
        seconds = min(60, max(15, target))
        low, high = round(seconds * 1.75), round(seconds * 2.2)
        finish = "Close with a natural audience question." if ending == "question" else "End on a concrete outcome."
        prompt = (
            'Write original short fiction for a general audience as JSON with a title and a lines array. '
            'Each line must have a speaker and text. Keep each line under 240 characters. '
            f'Premise: {idea}. Style: {style}. Use {low} to {high} total spoken words. '
            'Start with an event that creates a clear question. Escalate with specific details. '
            'Pay off a detail that appeared earlier in the story. Use natural dialogue. '
            f'{finish} Avoid copyrighted characters and real-world accusations.'
        )
        try:
            data = _local_json(model, prompt)
            lines=[{'speaker':str(x['speaker'])[:24], 'text':str(x['text'])[:240]} for x in data['lines'] if x.get('text')]
            if len(lines)>=3: return {'title':str(data.get('title') or idea)[:95],'lines':lines[:22],'source':'ollama'}
        except Exception as e:
            print('Ollama fallback:',e)
    # Honest template fallback; it is not presented as AI-generated creative writing.
    lines=[
      ('Me',idea[:238].rstrip('.!?')+'.'),
      ('Friend','Wait. You cannot just drop that and move on.'),
      ('Me','I thought I was overthinking everything at first.'),
      ('Friend','So what happened next?'),
      ('Me','Then I noticed something that completely changed the situation.'),
      ('Friend','Please tell me you asked someone about it.'),
      ('Me','I did. And their answer was NOT what I expected.'),
      ('Friend','There has to be more to this story.'),
      ('Me','Now I have to decide whether to confront them or walk away.'),
      ('Friend','What would you do if you were me?')]
    return {'title':idea[:85].rstrip('.!?')+'... 😳 #Shorts','lines':[{'speaker':a,'text':b} for a,b in lines],'source':'template'}

def random_story(genre="random", target=45, ending="resolved", previous_title=""):
    """Choose and vary a complete outline with a duration-appropriate number of beats."""
    if genre != "random" and genre not in STORIES:
        raise ValueError("Choose a supported story genre")
    genres = tuple(STORIES) if genre == "random" else (genre,)
    candidates = [(name, outline) for name in genres for outline in STORIES[name]]
    fresh = [pair for pair in candidates if pair[1][0] + " #Shorts" != previous_title]
    genre, (title, beats) = random.choice(fresh or candidates)
    if target <= 30:
        indices = (0, 1, 2, 4, 5, 7)
    elif target <= 45:
        indices = (0, 1, 2, 4, 5, 6, 7)
    else:
        indices = range(len(beats))
    lines = []
    for index in indices:
        speaker, *variants = beats[index]
        text, alternate = random.sample(variants, 2)
        lines.append({"speaker": speaker, "text": text, "alternate": alternate,
                      "pause_before": (0.8 if index == 5 else 0)})
    if ending == "question":
        lines.append({"speaker": "Me", "text": "What would you have done in my place?",
                      "alternate": "Be honest. Would you have handled it differently?",
                      "pause_before": 0})
    return {"title": title + " #Shorts", "lines": lines, "source": "original",
            "genre": genre}



def _local_json(model, prompt):
    response = requests.post(
        'http://127.0.0.1:11434/api/generate',
        json={'model': model, 'prompt': prompt, 'stream': False, 'format': 'json'},
        timeout=90,
    )
    response.raise_for_status()
    return json.loads(response.json()['response'])


def rewrite_line(story, index):
    model = os.getenv('SHADOW_OLLAMA_MODEL', '').strip()
    if not model:
        raise ValueError('Configure a local Ollama model to rewrite dialogue')
    lines = story['lines']
    dialogue = [{'speaker': line['speaker'], 'text': line['text']} for line in lines]
    current = dialogue[index]['text']
    prompt = (
        'Rewrite only the indicated line of this original fictional story. '
        'Keep the same speaker, plot facts, and meaning so the surrounding dialogue still makes sense. '
        'Make it conversational and approximately the same length, with a maximum of 240 characters. '
        'Respond as JSON with a single text field. '
        f'Target line number: {index + 1}. Dialogue: {json.dumps(dialogue, ensure_ascii=False)}'
    )
    try:
        result = _local_json(model, prompt)
        text = ' '.join(result['text'].split())
    except (requests.RequestException, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('Ollama could not rewrite the line. Check that the local model is running.') from exc
    if not text or len(text) > 240 or text.casefold() == current.casefold():
        raise RuntimeError('Ollama did not return a usable alternate line. Try again.')
    return text



def metadata(story):
    title=story['title'][:100]
    description=f"{title.replace('#Shorts','').strip()}\n\nWhat would you do? Tell us in the comments! 👇\n\nSubscribe for original dramatic short stories from ShadowChronicles.\n\n#Shorts #Storytime #Drama #TextStory #ShadowChronicles"
    return {'title':title,'description':description,'tags':['shorts','storytime','drama','text story','ShadowChronicles']}
