"""
Anchor -- ElevenLabs Voice Test
Run this to hear Anchor speak for the first time.
Tests different nudge messages with a warm voice.
"""

import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play

load_dotenv()

# Initialize ElevenLabs
client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"  # Roger - Laid-Back, Casual
# VOICE_ID = "jqcCZkN6Knx8BJ5TBdYR"
VOICE_ID = "XcXEQzuLXRU9RcfWzEJt"

def speak(text: str):
    """Generate and play speech"""
    print(f"\nSpeaking: \"{text}\"")
    audio = client.text_to_speech.convert(
        text=text,
        voice_id=VOICE_ID,
        model_id="eleven_multilingual_v2",
        output_format="mp3_44100_128",
    )
    play(audio)
    print("Done.")


if __name__ == "__main__":
    if not os.getenv("ELEVENLABS_API_KEY"):
        print("ERROR: Add ELEVENLABS_API_KEY to your .env file")
        exit(1)

    print("=" * 60)
    print("ANCHOR -- Voice Test")
    print("=" * 60)
    print("You should hear Anchor speak through your speakers.")
    print("Testing different nudge messages...\n")

    # Test 1: Gentle first nudge
    speak("Hey, you left your application for Reddit. Break or get back?")

    # Test 2: Escalated nudge
    speak("You keep bouncing away. Something blocking you, or do you need a real break?")

    # Test 3: Notification-driven
    speak("Looks like Slack pulled you out. Take a minute, I'll remind you to come back.")

    # Test 4: Task initiation
    speak("Getting started is the hardest part. Want to open the file together? 3, 2, 1, let's go!")

    # Test 5: Silent drift
    speak("Your screen hasn't moved in a while. Still with me?")

    print("\n" + "=" * 60)
    print("If you heard all 5 messages, the voice is working!")
    print("=" * 60)