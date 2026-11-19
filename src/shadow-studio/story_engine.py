"""Original templates only. Ollama option stays on local machine."""
import os, json, random, re, requests
from story_presets import STORIES

def make_story(idea, style="messages", target=45):
    idea = re.sub(r'\s+', ' ',idea).strip()[:350]
    if not idea: raise ValueError('Story idea cannot be empty')
    model = os.getenv('SHADOW_OLLAMA_MODEL','').strip()
    if model:
        prompt = f'''Write an ORIGINAL, safe-for-general-audience dramatic fictional short story as JSON ONLY, using the schema {{"title":"...","lines":[{{"speaker":"Me|Friend|Narrator","text":"..."}}]}}. Premise: {idea}. Style: {style}. About {max(5,min(14,target//5))} short lines, total 80-135 spoken words. Strong opening hook, escalation, meaningful twist, and question. No real-world accusations or copyrighted characters.'''
        try:
            r=requests.post('http://127.0.0.1:11434/api/generate',json={'model':model,'prompt':prompt,'stream':False,'format':'json'},timeout=90)
            r.raise_for_status()
            data=json.loads(r.json()['response'])
            lines=[{'speaker':str(x['speaker'])[:24], 'text':str(x['text'])[:240]} for x in data['lines'] if x.get('text')]
            if len(lines)>=3: return {'title':str(data.get('title') or idea)[:95],'lines':lines[:22],'source':'ollama'}
        except Exception as e:
            print('Ollama fallback:',e)
    # Honest template fallback; it is not presented as AI-generated creative writing.
    lines=[
      ('Me',f'Okay, I need advice. {idea.rstrip(".!?")}.'),
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

def random_story(genre="random", target=45, ending="resolved"):
    """Choose and vary a complete outline with a duration-appropriate number of beats."""
    if genre == "random":
        genre = random.choice(tuple(STORIES))
    if genre not in STORIES:
        raise ValueError("Choose a supported story genre")
    title, beats = random.choice(STORIES[genre])
    if target <= 30:
        indices = (0, 1, 2, 5, 7)
    elif target <= 45:
        indices = (0, 1, 2, 4, 5, 7)
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



def metadata(story):
    title=story['title'][:100]
    description=f"{title.replace('#Shorts','').strip()}\n\nWhat would you do? Tell us in the comments! 👇\n\nSubscribe for original dramatic short stories from ShadowChronicles.\n\n#Shorts #Storytime #Drama #TextStory #ShadowChronicles"
    return {'title':title,'description':description,'tags':['shorts','storytime','drama','text story','ShadowChronicles']}
