#!/usr/bin/env python3
"""Generate a SYNTHETIC classroom-style WAV (16 kHz, mono, 16-bit PCM) with TTS.

All voices are adult Google Cloud Text-to-Speech voices reading a fictional
script (fictional names from app/samples). No real person, no student audio.
The output matches the recorder format: 16 kHz mono WAV.

Usage:
  python make_sample_audio.py --out /tmp/20260924_101400_RM12.wav
Needs: google-cloud-texttospeech, ADC, texttospeech API enabled (free tier
covers this: ~2k characters).
"""
from __future__ import annotations

import argparse
import array
import io
import os
import random
import wave

RATE = 16000

# (voice, text, pause_after_seconds). Three distinct adult voices:
#   teacher  = en-US-Neural2-F (female), aide/RBT = en-US-Neural2-D (male),
#   "student" role = en-US-Neural2-J (adult male voice acting the role).
VOICES = {
    "teacher": "en-US-Neural2-F",
    "aide": "en-US-Neural2-D",
    "student": "en-US-Neural2-J",
}

SCRIPT = [
    ("teacher", "Good morning, everyone. Marcus, let's start with our math warm-up. Double-digit addition on page twelve.", 1.2),
    ("student", "Okay. I already did the first two. Thirty-seven plus twenty-eight is sixty-five.", 0.8),
    ("teacher", "Nice work carrying the one. Keep going, you've got eight more on that set.", 1.5),
    ("aide", "Marcus, remember to write the little one above the tens column, like we practiced yesterday.", 1.0),
    ("student", "Forty-six plus nineteen. Six plus nine is fifteen, so I carry the one. Sixty-five again.", 1.2),
    ("aide", "That's right. Two in a row. Try the next one on your own.", 2.0),
    ("teacher", "Everyone, the fire drill test tone is going to sound in a minute. It will be loud, but it's just a test.", 1.5),
    ("student", "It's too loud. I don't like it. I can't do the math when it's loud.", 0.7),
    ("aide", "I hear you. What can you use when it gets loud?", 0.8),
    ("student", "Can I have my headphones please?", 0.6),
    ("aide", "Great asking. Here are your headphones. Take a minute at the calm table if you need it.", 3.0),
    ("student", "Okay. I'm ready now. I'm going back to my seat.", 1.0),
    ("teacher", "Welcome back, Marcus. That was about three minutes. Let's finish problem five.", 1.0),
    ("student", "Fifty-eight plus twenty-four. Eight plus four is twelve, carry the one. Eighty-two.", 1.0),
    ("teacher", "Correct. You got six out of seven right on this probe. That's really strong work.", 1.5),
    ("aide", "I'll note the headphone request and the return time on the data sheet.", 1.0),
    ("teacher", "Thank you. Alright, everyone, put your worksheets in the blue tray and line up for centers.", 0.5),
]


def synth(client, tts, voice_name: str, text: str) -> array.array:
    resp = client.synthesize_speech(
        input=tts.SynthesisInput(text=text),
        voice=tts.VoiceSelectionParams(language_code="en-US", name=voice_name),
        audio_config=tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.LINEAR16, sample_rate_hertz=RATE
        ),
    )
    with wave.open(io.BytesIO(resp.audio_content)) as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1
        pcm = array.array("h", w.readframes(w.getnframes()))
    return pcm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeat", type=int, default=1, help="repeat the script N times (longer test file)")
    ap.add_argument("--noise", type=float, default=120.0, help="room-noise amplitude (0 = off)")
    args = ap.parse_args()

    from google.cloud import texttospeech as tts

    client = tts.TextToSpeechClient()
    rnd = random.Random(7)
    cache: dict[tuple[str, str], array.array] = {}
    out = array.array("h")
    for _ in range(args.repeat):
        for role, text, pause in SCRIPT:
            key = (role, text)
            if key not in cache:
                cache[key] = synth(client, tts, VOICES[role], text)
            out.extend(cache[key])
            out.extend(array.array("h", [0]) * int(pause * RATE))
    if args.noise:
        for i in range(len(out)):
            out[i] = max(-32768, min(32767, int(out[i] * 0.8 + rnd.gauss(0, args.noise))))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(out.tobytes())
    print(f"wrote {args.out}: {len(out) / RATE:.1f} s, 16 kHz mono PCM (synthetic TTS)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
