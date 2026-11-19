"""Offline ASR phrase-match QA without downloading or loading model weights."""
from real_offline_speech_acceptance import contains_phrase

assert contains_phrase("Hello, world!", "hello world")
assert contains_phrase("The DOG ran", "dog")
assert contains_phrase("One two three", "two three")
assert not contains_phrase("shelloworld", "hello")
assert not contains_phrase("one two three", "one three")
assert not contains_phrase("one two", "two one")
try:
    contains_phrase("anything", "!!!")
except ValueError:
    pass
else:
    raise AssertionError("Empty expected speech phrase accepted")
print("PASS real-ASR phrase matcher boundaries, order and punctuation")
