import os
from google.cloud import texttospeech
from google.oauth2 import service_account

def synthesize_text_to_speech(text, output_filename="output.mp3"):
    """
    Synthesizes a text string into a local audio file using Google Cloud Text-to-Speech.
    """
    # 🌟 הקוד יטען את קובץ המפתח ישירות מהתיקייה הנוכחית
    credentials_path = "long-ceiling-459419-j7-f58e85f09acf.json"

    # טעינת ההרשאות מהקובץ
    credentials = service_account.Credentials.from_service_account_file(credentials_path)
    client = texttospeech.TextToSpeechClient(credentials=credentials)

    # הגדרת טקסט הקלט
    input_text = texttospeech.SynthesisInput(text=text)

    # הגדרת הקול לעברית
    voice = texttospeech.VoiceSelectionParams(
        language_code="he-IL",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL,
    )

    # הגדרות שמע (MP3)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    print("Synthesizing speech...")
    response = client.synthesize_speech(
        input=input_text, voice=voice, audio_config=audio_config
    )

    # שמירת קובץ השמע
    with open(output_filename, "wb") as out_file:
        out_file.write(response.audio_content)
        print(f"Audio content written to file '{output_filename}'")

if __name__ == "__main__":
    text_to_synthesize = "שלום, אני הבוט הטלפוני שלך. בבקשה דבר."
    synthesize_text_to_speech(text_to_synthesize)