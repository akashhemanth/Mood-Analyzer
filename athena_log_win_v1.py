import sys
import os
import datetime as dt
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
import subprocess

# ===== CONFIG =====
BASE_DIR = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Athena_Logs")
MIC_DEVICE = 12                 # WASAPI mic
MIC_SAMPLERATE = 48000          # native mic rate
CHANNELS = 1
WHISPER_MODEL = "small"
MAX_RECORD_SECONDS = 600        # 10 minutes
# ==================


def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def create_log_folder():
    today = dt.date.today()
    month_folder = today.strftime("%B_%Y")
    day_folder = today.strftime("%m-%d-%Y")
    full_path = os.path.join(BASE_DIR, month_folder, day_folder)
    ensure_dir(full_path)
    return full_path


def record_audio(wav_path: str) -> bool:
    import msvcrt  # Windows-only

    sd.default.device = MIC_DEVICE
    sd.default.samplerate = MIC_SAMPLERATE
    sd.default.channels = CHANNELS

    frames = []

    def callback(indata, frames_count, time_info, status):
        frames.append(indata.copy())

    print("🎙️ Recording started...")
    print("➡️ Press Q to stop early (auto-stops at 10 minutes)")

    start = dt.datetime.now()
    max_ms = int(MAX_RECORD_SECONDS * 1000)

    try:
        with sd.InputStream(callback=callback):
            elapsed_ms = 0
            while elapsed_ms < max_ms:
                sd.sleep(200)  # check 5x/second
                elapsed_ms = int((dt.datetime.now() - start).total_seconds() * 1000)

                if msvcrt.kbhit():
                    key = msvcrt.getwch().lower()
                    if key == "q":
                        print("\n⏹️ Stopped early (Q pressed)")
                        break
    except Exception as e:
        print(f"❌ Recording error: {e}")
        return False

    if not frames:
        print("⚠️ No audio captured")
        return False

    audio = np.concatenate(frames, axis=0)
    sf.write(wav_path, audio, MIC_SAMPLERATE)
    print("✅ Audio saved:", wav_path)
    return True



def transcribe_whisper(wav_path: str, txt_path: str):
    print("🧠 Transcribing with Whisper...")
    model = whisper.load_model(WHISPER_MODEL)

    result = model.transcribe(
        wav_path,
        language="en",
        temperature=0,
        beam_size=5,
        best_of=5,
        fp16=False,
        condition_on_previous_text=False,
        verbose=False
    )

    text = (result.get("text") or "").strip()
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print("📝 Transcript saved:", txt_path)

def main():
    folder = create_log_folder()
    ts = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    wav_path = os.path.join(folder, f"{ts}_audio.wav")
    txt_path = os.path.join(folder, f"{ts}_audio.txt")

    ok = record_audio(wav_path)
    if ok:
        transcribe_whisper(wav_path, txt_path)
        subprocess.run([sys.executable, "athena_mood_monitor_v2.py", txt_path])


if __name__ == "__main__":
    main()
