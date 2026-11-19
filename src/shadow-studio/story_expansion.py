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
        "The clip asked him to read a letter from her grandmother. It was the only recording she had of her voice.",
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
        "The planner imported my old party spreadsheet. Look at the row directly under his.",
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
    else:
        pool = GENERAL
        names = GENRE_ARCS[genre]
        partner = "Friend"
    fresh = [name for name in names if name not in avoid]
    key = rng.choice(fresh or [name for name in names if not avoid or name != avoid[-1]] or names)
    hook, setup, response, twist, reaction, payoff = pool[key]
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
    return {"title": title, "genre": genre, "source": "offline_expansion", "variant": key,
            "lines": [{"speaker": speaker, "text": line, "pause_before": pause} for speaker, line, pause in lines]}
