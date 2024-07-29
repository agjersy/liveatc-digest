from liveatc_downloader import liveatc
from pydub import AudioSegment

if __name__ == "__main__":

    icao = 'kbed'
    stations = liveatc.get_stations(icao)

    for station in stations:
        print('Downloading archive from station...')
        print(station['frequencies'])
        filepath = liveatc.download_archive(icao, station['identifier'], 'Jul-28-2024', '0000Z')
        print(filepath)
        break
    
    recording = AudioSegment.from_mp3(filepath)
    # PyDub handles time in milliseconds
    five_minutes = 10 * 60 * 1000

    chunkpath = "/tmp/five_minute_chunk.mp3"
    second_5_minutes = recording[five_minutes:five_minutes+five_minutes]
    second_5_minutes.export(chunkpath, format="mp3")

    print('Transcribing audio...')
    useAPI = True

    if useAPI:
        
        from openai import OpenAI
        client = OpenAI()

        audio_file = open(chunkpath, "rb")
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
        print(transcription.text)

    else:
        
        import whisper
        model = whisper.load_model("small.en")
        result = model.transcribe(filepath, fp16=False, language='English')
        print(result['text'])
