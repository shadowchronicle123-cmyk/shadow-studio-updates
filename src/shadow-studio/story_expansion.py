"""Free, local premise expansion with authored plot branches."""
import random
import re

GENRES = ("horror", "drama", "romance", "betrayal", "comedy", "mystery", "revenge")
# Each branch supplies a concrete hook, exchange, payoff, and consequence.
WEDDING = {
    "guest-list": (
        "The seating chart was already printed when I spotted her ex next to my dad.",
        "I put the chart between us. Why is he sitting with our families?",
        "Open the email chain. I added him to the list, but I didn't pick that seat.",
        "At the bottom was my dad's reply. He'd promised him a seat years ago, when they worked together.",
        "I should've asked you before I invited him. Want to call your dad together?",
        "We called him. By dinner, the guest list was one chair shorter and Dad had a lunch date instead.",
    ),
    "speech": (
        "Her ex had a place card and a slot on the microphone schedule.",
        "I could deal with a chair. Why was he giving a speech?",
        "Play the voice memo I sent the planner. Listen to the whole thing.",
        "The clip asked him to send a recording of her grandmother reading a letter. He had the only copy.",
        "I wanted to hear Grandma at the wedding. I should've told you before I called him.",
        "We played the recording ourselves. The first person I thanked after the ceremony was her.",
    ),
    "deadline": (
        "I saw the RSVP come in while we were paying the final venue bill.",
        "Before I hit pay, tell me why your ex is on the guest list.",
        "I invited him back when we sent the first cards. I changed my mind and forgot to tell you.",
        "She showed me an unsent message asking him to decline. The cursor was still blinking.",
        "I kept rewriting it because I was scared you'd think I was hiding something. I was making it worse.",
        "We sent the message together. Then we paid the venue, a day late, with the guest list we both wanted.",
    ),
    "name-cards": (
        "Her ex was on the seating chart, right between the cake and the emergency exits.",
        "Why does he have a table number? And why is the table in the kitchen?",
        "I did invite him, but the planner pasted in my old party spreadsheet. Look at the row under his.",
        "The next guest was our old landlord, who'd been assigned a high chair.",
        "Fine. I'll call my ex. You're explaining the high chair to our landlord.",
        "The planner printed a clean guest list. We kept the high-chair card on the fridge.",
    ),
}
GENERAL = {
    "timestamps": (
        "I almost deleted the message. The timestamp was the only reason I kept it.",
        "I called you because I was about to reply in the group chat.",
        "Before you do, open the original. What time was it actually sent?",
        "The copy everyone saw cut off the first reply. The original had a name under it I recognized.",
        "Call the person whose name is on it. Ask what they remember, then let them finish.",
        "I made the call. By the end of it I had an apology and a plan to correct the story where it started.",
    ),
    "callback": (
        "The whole thing clicked when my phone rang again and I heard the same background noise.",
        "I let it ring first. Then I called back using the number I already had saved.",
        "Good. Ask them what was happening in the background. It'll tell you where to start.",
        "It was the kettle in the kitchen. The person I'd been trying to reach was close enough to come over.",
        "Then put your phone down for five minutes. You can ask them face to face.",
        "We talked at the kitchen table. I had the answer before the kettle finished boiling again.",
    ),
    "receipt": (
        "I spent all afternoon arguing about something I could have checked in thirty seconds.",
        "I was sure I had it right, so I pulled up the receipt while we were still talking.",
        "Read the bottom line out loud. That's where they put the name of the person who signed.",
        "My name was printed there. The signature underneath belonged to somebody else.",
        "Save the receipt. Then ask for the correction in writing so everyone sees the same thing.",
        "The corrected copy arrived that night. I forwarded it to everyone and took my name off the argument.",
    ),
    "witness": (
        "I replayed the conversation twice. The important part was the voice in the background.",
        "At first I thought it was a recording glitch, so I asked who else was there.",
        "Send me the last ten seconds. I might know who said that.",
        "You recognized the voice right away. They'd been trying to interrupt before the call cut out.",
        "We can call them together. Ask them to finish the sentence.",
        "They did. I finally had the whole explanation, and we set a time to talk it through in person.",
    ),
    "photo": (
        "A photo I nearly threw away was sitting on the table when my phone lit up.",
        "I turned it over while we were talking. There was a date in pencil.",
        "Can you read it to me? I remember why we were all there that day.",
        "The date put everything a week earlier than I'd been told. I'd been blaming the wrong person.",
        "You've got time to put that right. Start with the person you called first.",
        "I apologized that evening. We kept the picture and wrote the right date below the old one.",
    ),
    "wrong-group": (
        "I sent my big speech to the wrong group chat, and somehow it helped.",
        "Tell me you saw that before I tried to delete it.",
        "Everyone saw it. The group name is nearly identical, but this one is for planning the surprise.",
        "I opened the member list. They had put the one person we were surprising in charge of reminders.",
        "We should probably give them a cake for keeping their own surprise secret.",
        "We did. They blew out the candles and asked if we could retire both group chats.",
    ),
    "extra-copy": (
        "I found our conversation printed out, including the message I had only just typed.",
        "There was a second copy under it. The last line said to call you before I answered.",
        "Hold it up to the light. Is there anything written on the other side?",
        "There was one more line. It said you'd already called, even though my phone hadn't rung.",
        "Turn off the phone. I'm coming to your door, and I'll knock twice.",
        "I heard two knocks and let you in. At sunrise I threw the printouts away. You kept your phone off, too.",
    ),
}
HOUSEHOLD = {
    "meter-reading": (
        "The bill made sense until I compared it with the photo we took when we moved in.",
        "Can you pull up the photo of the meter? The first digit on the statement looks wrong.",
        "I've got it. The photo also shows the number on the meter next door.",
        "The statement listed the next-door meter. We'd been chasing the wrong explanation all week.",
        "Send both pictures to the office. I'll ask our neighbor whether their bill looks strange too.",
        "The office corrected both accounts. We saved the photos with our next month's paperwork.",
    ),
    "written-split": (
        "We were halfway through a fight when I found the note we'd agreed to on move-in day.",
        "Before either of us sends more money, can we read what we wrote down?",
        "You keep the note. I'll pull up the transactions and read out the dates.",
        "One transfer was for our deposit. We'd counted it as rent because the memo just said apartment.",
        "Let's put the amounts in a shared note while we're both looking at them.",
        "We settled the difference that night and added a label to every transfer after that.",
    ),
    "office-copy": (
        "The rental office had two different copies of our balance. Only one had a date.",
        "I can stop by after work. Do you want to meet me there?",
        "Yes. Bring the screenshots and ask who changed the balance last.",
        "It was an old credit from the last tenant. The office had applied it to our account and then removed it.",
        "Can we ask for a corrected statement before paying the difference?",
        "They sent one while we were there. We split the amount we could both verify.",
    ),
}
WORKPLACE = {
    "version-history": (
        "I opened the document while everyone was still on the call. The version history changed the conversation.",
        "Can you share your screen? I want to read the entry before we bring it up.",
        "Start with today's version and work backward. We only need to see when it changed.",
        "The edit had been made before my shift. The original note was still attached to the older version.",
        "Send that version to the supervisor. Ask them to correct the record with everyone who saw the first one.",
        "The correction went out before we left. I saved a copy and moved on with my shift.",
    ),
    "handover": (
        "The note everyone blamed me for was folded under the handover sheet.",
        "Did you sign that sheet or the one they showed us in the meeting?",
        "Get both pages before you answer. There's a copy in the binder near the clock.",
        "The note was for the next shift, and the date at the top had been carried over from yesterday.",
        "Bring it to the handover. I'll be there to explain where we found it.",
        "We cleared it up at the next handover. They put the date beside the signature after that.",
    ),
    "one-on-one": (
        "I thought my supervisor was brushing me off until they showed me what had been forwarded.",
        "Can I show you the message I actually sent? I think there's a line missing.",
        "Look at mine first. This is what I was asked to respond to.",
        "The forwarded message had my first sentence and none of the explanation underneath it.",
        "Let's put the full message in the same thread. I'll tell them why my response changed.",
        "We did it together, right there. The next reply was an apology and a time to talk.",
    ),
}
AFTERMATH = {
    "meter-reading": "We split the corrected bill at the kitchen table. Our neighbor sent us a photo of their meter for next time.",
    "written-split": "My roommate texted me the cleaned-up list the next morning. The first entry said deposit in capital letters.",
    "office-copy": "We waited for the corrected statement, then took care of the balance together. Nobody had to guess at a number.",
    "version-history": "My supervisor put the correction in the original thread. When I checked after work, my name was off the error.",
    "handover": "The next shift read both pages and signed off. I asked for a copy, then finished my shift.",
    "one-on-one": "My supervisor called the person who forwarded it. That afternoon, I got the first reply that actually addressed what I'd asked.",
    "guest-list": "We moved his invitation to a family lunch after the honeymoon. Dad changed the seating chart himself.",
    "speech": "Her ex sent the recording to both of us. We listened to it together before we picked a place for it in the ceremony.",
    "deadline": "She called him, then called the planner. We each read the final guest list before we paid.",
    "name-cards": "We called the planner. He reset the guest list, and my partner asked her ex if he'd mind sitting at an ordinary table.",
    "timestamps": "The missing reply changed who owed whom an apology. We put the original exchange back in the chat and settled it there.",
    "callback": "I walked to the kitchen and let the phone ring on the table. We cleared it up before either cup went cold.",
    "receipt": "We went through the old bill together. I kept both copies and offered to send the correction myself.",
    "witness": "They finished the sentence in one breath. I wrote down the answer, then asked if we could all talk again tomorrow.",
    "photo": "I took the picture to the person I'd blamed. They recognized the room before I told them the date.",
    "wrong-group": "The guest of honor laughed hardest when we read the planning thread out loud. Next year they're officially in charge.",
    "extra-copy": "You spent the night on the couch. At daylight, we opened the door together and checked whether anyone was still waiting outside.",
}

GENRE_ARCS = {
    "horror": ("extra-copy", "witness", "callback"),
    "drama": ("witness", "timestamps", "photo"),
    "romance": ("callback", "photo", "timestamps"),
    "betrayal": ("receipt", "timestamps", "witness"),
    "comedy": ("wrong-group", "receipt", "callback"),
    "mystery": ("photo", "witness", "timestamps"),
    "revenge": ("receipt", "photo", "timestamps"),
}


def expand(idea, genre, target, ending, avoid=(), rng=None):
    rng = rng or random
    if genre in (None, "random"):
        genre = rng.choice(GENRES)
    if genre not in GENRES:
        raise ValueError("Choose a supported story genre")
    wedding = re.search(r"\bwedding\b", idea, re.I) and re.search(r"\bex\b", idea, re.I)
    if wedding:
        pool = WEDDING
        names = tuple(WEDDING) if genre != "comedy" else ("name-cards", "guest-list", "deadline")
        partner = "Partner" if re.search(r"\b(?:girlfriend|boyfriend|fianc[eé]e?|partner|wife|husband)\b", idea, re.I) else "Friend"
    elif genre in ("drama","betrayal","mystery","revenge") and re.search(r"\b(?:roommate|rent|landlord)\b", idea, re.I) and re.search(r"\b(?:bill|rent|deposit|paid|charge)\b", idea, re.I):
        pool, names, partner = HOUSEHOLD, tuple(HOUSEHOLD), "Roommate" if re.search(r"\broommate\b",idea,re.I) else "Friend"
    elif genre in ("drama","betrayal","mystery","revenge") and re.search(r"\b(?:coworker|supervisor|manager|shift|boss)\b", idea, re.I):
        pool, names, partner = WORKPLACE, tuple(WORKPLACE), "Coworker"
    else:
        pool = GENERAL
        names = GENRE_ARCS[genre]
        partner = "Friend"
    used_arcs = {variant.split(':', 1)[0] for variant in avoid}
    remaining_arcs = [name for name in names if name not in used_arcs]
    available = [(name, cut) for name in names for cut in range(2) if f'{name}:{cut}' not in avoid]
    unseen = [item for item in available if item[0] in remaining_arcs]
    fallback = [(name, cut) for name in names for cut in range(2) if not avoid or f'{name}:{cut}' != avoid[-1]]
    key, cut = rng.choice(unseen or available or fallback)
    hook, setup, response, twist, reaction, payoff = pool[key]
    if cut:
        payoff = AFTERMATH[key]
    premise_lines = []
    remaining = idea
    while remaining:
        split = remaining.rfind(" ", 0, 218) if len(remaining) > 218 else len(remaining)
        split = split if split > 0 else min(len(remaining), 218)
        premise_lines.append(("Me", remaining[:split], 0))
        remaining = remaining[split:].strip()
    lines = [("Me", hook, 0), *premise_lines, ("Me", setup, 0), (partner, response, 0),
             ("Me", twist, .65), (partner, reaction, 0), ("Me", payoff, 0)]
    if target <= 30:
        del lines[-2]
    if target >= 60:
        lines.insert(-1, (partner, rng.choice(("Text me when you've had the conversation. I'll keep my phone on.",
                                             "Take a breath before you go. You've got time to do this right.")), 0))
    if ending == "question":
        lines.append(("Me", "What would you have said in my place?", 0))
    title = hook.rstrip(".")[:83] + " #Shorts"
    return {"title": title, "genre": genre, "source": "offline_expansion", "variant": f"{key}:{cut}",
            "lines": [{"speaker": speaker, "text": line, "pause_before": pause} for speaker, line, pause in lines]}
