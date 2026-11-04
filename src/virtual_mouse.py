# ----------------------------------------------------------------
# Virtual Mouse with Face Authentication and Voice Commands (Vosk)
#
# - Face recognition (LBPH) gates access; hand gestures (MediaPipe)
#   control mouse movement, clicking, and scrolling once authorized.
# - Holding index + middle fingers together triggers offline voice
#   command recognition (Vosk) for a small set of OS actions.
#-------------------------------------------------------------------

import cv2
import mediapipe as mp
import pyautogui
import math
import time
import subprocess
import sounddevice as sd
from vosk import Model, KaldiRecognizer
import json


# ---------------------------------------------------------------
# CONFIGURATION / GLOBAL STATE
# ---------------------------------------------------------------
VOSK_MODEL_PATH = "vosk-model-en-in-0.5"   # folder name of vosk model
SESSION_TIMEOUT = 20  # auto-lock after this many seconds with no authorized face seen

# Lock-system-via-voice cooldown
LOCK_COOLDOWN = 4         # seconds to pause face auth right after a voice "lock system"
lock_until_time = 0       # timestamp until which face auth is skipped

# Index + middle finger hold -> triggers voice command listening
wake_requested = False
gesture_hold_start = None
GESTURE_HOLD_TIME = 2  # seconds fingers must stay together to trigger voice mode

# Authentication state
authorized = False
authorized_name = None
last_face_seen_time = None

# Authorized face tracking (keeps the session alive while the same face stays in frame)
AUTH_FACE_CENTER = None   # stored on authorization; not currently read (only AREA is used to match)
AUTH_FACE_AREA = None
FACE_MATCH_TOLERANCE = 0.4   # 40% tolerance when comparing current face area to AUTH_FACE_AREA

# Face authorization confidence (LBPH: LOWER confidence value = better match)
CONF_THRESHOLD = 70
auth_counter = 0
REQUIRED_FRAMES = 2   # consecutive good-confidence frames required before granting access

# ---------------------------------------------------------------
# LIMITED COMMAND VOCABULARY (constrains what Vosk tries to recognize)
# ---------------------------------------------------------------
ALLOWED_COMMANDS = [
    "take screenshot",
    "open camera",
    "close window",
    "turn on bluetooth",
    "turn off bluetooth",
    "open chrome",
    "open finder",
    "open terminal",
    "lock system",
    "VOICE_TYPING",
    "MINIMIZE_WINDOW",
    "WIFI_ON",
    "WIFI_OFF",
    "OPEN NOTES"
]

# Maps each intent to phrases/mishearing variants that should trigger it
# (e.g. "camra", "chimera" catch common Vosk mishearings of "camera")
INTENTS = {
    "OPEN_CAMERA": ["camera", "photo booth", "cam", "camra", "chimera"],
    "CLICK_PICTURE": ["click picture", "take picture", "capture photo", "clear picture"],
    "SCREENSHOT": ["screenshot", "screen shot", "screen"],
    "CLOSE_WINDOW": ["close window", "close", "closure"],
    "MINIMIZE_WINDOW": ["minimize", "minimise", "minimize window"],
    "WIFI_ON": ["wifi on", "turn on wifi", "enable wifi", "enable wi fi", "enable wildfire"],
    "WIFI_OFF": ["wifi off", "turn off wifi", "disable wifi", "disable wildfire"],
    "VOICE_TYPING": ["voice typing", "dictation", "start dictation"],
    "LOCK_SYSTEM": ["lock system", "lock", "system lock"],
    "OPEN NOTES": ["notes", "nodes", "note"]
}


# Returns the first matching intent name for the given recognized text,
# or None if no keyword from INTENTS is found in it.
def detect_intent(text):
    text = text.lower()

    for intent, keywords in INTENTS.items():
        for word in keywords:
            if word in text:
                return intent

    return None


# Vosk recognizer is grammar-constrained to ALLOWED_COMMANDS for more reliable offline recognition
print("[INFO] Loading Vosk model (this may take a few seconds)...")
vosk_model = Model(VOSK_MODEL_PATH)
vosk_recognizer = KaldiRecognizer(
    vosk_model,
    16000,
    json.dumps({"phrases": ALLOWED_COMMANDS})
)

# ---------------------------------------------------------------
# FACE AUTH SETUP
# ---------------------------------------------------------------
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# Pre-trained LBPH face recognizer (trained separately, loaded from disk)
face_recognizer = cv2.face.LBPHFaceRecognizer_create()
face_recognizer.read("trainer/lbph.yml")

# Maps numeric face IDs (from training) to display names, e.g. "0:Nobi"
labels = {}
with open("trainer/labels.txt") as f:
    for line in f:
        k, v = line.strip().split(":")
        labels[int(k)] = v

# ---------------------------------------------------------------
# HAND MOUSE SETUP
# ---------------------------------------------------------------
screen_width, screen_height = pyautogui.size()
cap = cv2.VideoCapture(0)
cap.set(3, 640)   # frame width
cap.set(4, 480)   # frame height

# Mouse position to hold the cursor at while the system is locked
LOCK_MOUSE_X = screen_width // 2
LOCK_MOUSE_Y = screen_height // 2

mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    max_num_hands=1,
    min_detection_confidence=0.9,
    min_tracking_confidence=0.9
)
mp_draw = mp.solutions.drawing_utils

prev_x, prev_y = 0, 0
alpha = 0.12          # smoothing factor for exponential moving average cursor movement
mode = "SMOOTH"

roi_x1, roi_x2 = 100, 540   # hand-tracking region of interest (maps to full screen width)
roi_y1, roi_y2 = 100, 380   # hand-tracking region of interest (maps to full screen height)


def distance(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


# ---------------------------------------------------------------
# VOICE COMMANDS -> OS ACTIONS
# ---------------------------------------------------------------
def execute_command(text: str):
    print(f"[VOICE CMD RAW] {text}")
    intent = detect_intent(text)

    if intent == "LOCK_SYSTEM":
        global authorized, authorized_name, lock_until_time

        authorized = False
        authorized_name = None
        lock_until_time = time.time() + LOCK_COOLDOWN  # pause face-auth briefly after locking

        print("[SECURITY] System locked by voice command")

    # CAMERA
    elif intent == "OPEN_CAMERA":
        subprocess.run(["open", "-a", "Photo Booth"])
        print("[ACTION] Camera opened")

    elif intent == "OPEN NOTES":
        subprocess.run(["open", "-a", "Notes"])
        print("[ACTION] Notes opened")

    elif intent == "CLICK_PICTURE":
        print("[ACTION] Sending Return key to Photo Booth...")
        # This script brings Photo Booth to the front and "presses" the Return key
        script = '''
        tell application "Photo Booth" to activate
        delay 0.5
        tell application "System Events"
            key code 36 -- This is the code for the 'Return' key
        end tell
        '''
        subprocess.run(["osascript", "-e", script])
        print("[ACTION] Photo captured via Return key")

    # WIFI
    elif intent == "WIFI_ON":
        subprocess.run([
            "networksetup",
            "-setairportpower",
            "en0",
            "on"
        ])
        print("[ACTION] Wi-Fi ON")

    elif intent == "WIFI_OFF":
        subprocess.run([
            "networksetup",
            "-setairportpower",
            "en0",
            "off"
        ])
        print("[ACTION] Wi-Fi OFF")

    # WINDOW
    elif intent == "MINIMIZE_WINDOW":
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "m" using command down'
        ])
        print("[ACTION] Window minimized")

    elif intent == "CLOSE_WINDOW":
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "w" using command down'
        ])
        print("[ACTION] Window closed")

    # DICTATION
    elif intent == "VOICE_TYPING":
        applescript = '''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            tell process frontApp
                click menu item "Start Dictation" of menu "Edit" of menu bar 1
            end tell
        end tell
        '''
        subprocess.run(["osascript", "-e", applescript])
        print("[ACTION] Dictation toggled")

    # FALLBACK
    else:
        print("[INFO] No valid intent detected")


# ---------------------------------------------------------------
# Records a short audio clip and runs it through Vosk; if authorized,
# executes any recognized command via execute_command().
# ---------------------------------------------------------------
def listen_command_vosk(duration_sec: int = 4, sample_rate: int = 16000):
    if not authorized:
        print("[SECURITY] Voice command blocked (not authorized)")
        return
    """
    Record audio using sounddevice and recognize using Vosk (offline).
    """
    print("[INFO] Listening for command (Vosk)...")

    try:
        audio = sd.rec(
            int(duration_sec * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype='int16'
        )
        sd.wait()

        # Vosk expects raw PCM bytes
        audio_bytes = audio.tobytes()

        vosk_recognizer.Reset()
        if vosk_recognizer.AcceptWaveform(audio_bytes):
            result = vosk_recognizer.Result()
        else:
            result = vosk_recognizer.FinalResult()

        data = json.loads(result)
        text = data.get("text", "").strip()
        if text:
            print(f"[VOSK] Recognized: {text}")
            execute_command(text)
        else:
            print("[VOSK] No speech recognized.")
    except Exception as e:
        print(f"[ERROR] Vosk listen failed: {e}")


scroll_last_time = 0   # throttles scroll events so they don't fire every single frame

# ---------------------------------------------------------------
# MAIN LOOP (FACE AUTH + HAND GESTURES + VOICE)
# ---------------------------------------------------------------
print("[SYSTEM] Virtual Mouse + Face Auth + Voice (Vosk) started.")

while True:
    # SESSION TIMEOUT: auto-lock if the authorized face hasn't been seen in a while
    if authorized and last_face_seen_time is not None:
        if time.time() - last_face_seen_time > SESSION_TIMEOUT:
            authorized = False
            authorized_name = None
            last_face_seen_time = None
            print("[SECURITY] Session expired. System locked.")

    # Voice-mode gesture was held long enough - play a cue and listen for a command
    if wake_requested and authorized:
        try:
            subprocess.Popen(["afplay", "yes_sound.mp3"])
        except:
            pass

        listen_command_vosk(duration_sec=4, sample_rate=16000)
        wake_requested = False

    success, img = cap.read()
    if not success:
        break

    img = cv2.flip(img, 1)   # mirror for a natural "selfie view"
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # FACE AUTH (respect lock cooldown after a voice "lock system")
    if not authorized:
        if time.time() < lock_until_time:
            # Still in lock cooldown -> skip face auth this frame
            pass
        else:
            faces = face_cascade.detectMultiScale(gray, 1.3, 5)
            for (x, y, w, h) in faces:
                roi = gray[y:y+h, x:x+w]
                roi = cv2.resize(roi, (200, 200))
                roi = cv2.equalizeHist(roi)
                id_, conf = face_recognizer.predict(roi)
                print(f"[DEBUG] Face id={id_}, confidence={conf}")

                # LBPH confidence: LOWER = more confident match. Very high values mean "no match".
                if conf > 100:
                    auth_counter = 0
                    continue

                if conf < CONF_THRESHOLD:
                    auth_counter += 1
                    if auth_counter >= REQUIRED_FRAMES:
                        authorized = True
                        authorized_name = labels.get(id_, "User")
                        last_face_seen_time = time.time()

                        # Save authorized face position & size for later frame-to-frame matching
                        cx = x + w // 2
                        cy = y + h // 2
                        AUTH_FACE_CENTER = (cx, cy)
                        AUTH_FACE_AREA = w * h

                        print(f"[INFO] Authorized: {authorized_name}")
                        auth_counter = 0
                        break
                else:
                    auth_counter = 0

    # If the authorized face is still visible, keep the session alive;
    # if it disappears, last_face_seen_time stops updating and the session timeout above will fire.
    if authorized:
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)
        matched_authorized_face = False

        for (x, y, w, h) in faces:
            cx = x + w // 2
            cy = y + h // 2
            area = w * h

            # Size similarity check (assumes a face of similar size = the same authorized person)
            if AUTH_FACE_AREA is not None:
                size_ratio = area / AUTH_FACE_AREA
                if 1 - FACE_MATCH_TOLERANCE <= size_ratio <= 1 + FACE_MATCH_TOLERANCE:
                    matched_authorized_face = True
                    break

        if matched_authorized_face:
            last_face_seen_time = time.time()

    # On-screen authorization status
    if authorized:
        cv2.putText(img, f"Authorized: {authorized_name}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    else:
        cv2.putText(img, "Unauthorized - Gestures Disabled", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # PHYSICAL MOUSE BLOCK: pin the cursor to the center while locked out
    if not authorized:
        pyautogui.moveTo(LOCK_MOUSE_X, LOCK_MOUSE_Y)
        time.sleep(0.01)

    # HAND GESTURES (only processed while authorized)
    if authorized:
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        results = hands.process(img_rgb)
        gesture_text = ""

        if results.multi_hand_landmarks:
            hand_landmarks = results.multi_hand_landmarks[0]
            lm_list = []
            h, w, c = img.shape
            for lm in hand_landmarks.landmark:
                lm_list.append((int(lm.x * w), int(lm.y * h)))

            mp_draw.draw_landmarks(img, hand_landmarks, mp_hands.HAND_CONNECTIONS)

            # MediaPipe landmark indices: 4=thumb tip, 8=index tip, 12=middle tip, 16=ring tip, 20=pinky tip
            x_index, y_index = lm_list[8]
            x_thumb, y_thumb = lm_list[4]
            x_middle, y_middle = lm_list[12]
            x_ring, y_ring = lm_list[16]
            x_pinky, y_pinky = lm_list[20]

            # Map index fingertip position inside the ROI box to full screen coordinates
            x_ratio = (x_index - roi_x1) / (roi_x2 - roi_x1)
            y_ratio = (y_index - roi_y1) / (roi_y2 - roi_y1)
            target_x = screen_width * min(max(x_ratio, 0), 1)
            target_y = screen_height * min(max(y_ratio, 0), 1)

            dx, dy = target_x - prev_x, target_y - prev_y

            # GESTURE WAKE (hold index + middle fingers together for GESTURE_HOLD_TIME seconds)
            hold_distance = distance((x_index, y_index), (x_middle, y_middle))

            if hold_distance < 22:  # fingers touching
                if gesture_hold_start is None:
                    gesture_hold_start = time.time()
                elif time.time() - gesture_hold_start >= GESTURE_HOLD_TIME:
                    wake_requested = True
                    gesture_hold_start = None
                    gesture_text = "Voice Mode"
            else:
                gesture_hold_start = None

            # Move the cursor using exponential smoothing (alpha), only if the move is significant
            if abs(dx) > 2 or abs(dy) > 2:
                smooth_x = prev_x + alpha * dx
                smooth_y = prev_y + alpha * dy
                pyautogui.moveTo(smooth_x, smooth_y)
                prev_x, prev_y = smooth_x, smooth_y

            # Click gestures: thumb+index = left click, index+ring = right click
            if distance((x_index, y_index), (x_thumb, y_thumb)) < 22:
                pyautogui.click()
                gesture_text = "Left Click"

            elif distance((x_index, y_index), (x_ring, y_ring)) < 18:
                pyautogui.click(button='right')
                gesture_text = "Right Click"

            # Scroll gestures: thumb+ring = scroll up, thumb+pinky = scroll down (throttled)
            elif distance((x_thumb, y_thumb), (x_ring, y_ring)) < 22:
                now = time.time()
                if now - scroll_last_time > 0.05:
                    pyautogui.scroll(2)
                    scroll_last_time = now
                gesture_text = "Scrolling Up..."

            elif distance((x_thumb, y_thumb), (x_pinky, y_pinky)) < 22:
                now = time.time()
                if now - scroll_last_time > 0.05:
                    pyautogui.scroll(-2)
                    scroll_last_time = now
                gesture_text = "Scrolling Down..."

            if gesture_text:
                cv2.putText(img, gesture_text, (50, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

    cv2.putText(img, f"Mode: {mode}", (10, 70),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

    # Blinking red border + "SYSTEM LOCKED" overlay while unauthorized
    if not authorized:
        t = int(time.time() * 2) % 2
        color = (0, 0, 255) if t == 0 else (0, 0, 180)

        cv2.rectangle(img, (0, 0), (640, 480), color, 10)
        cv2.putText(img, "SYSTEM LOCKED", (160, 240),
            cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)

    cv2.imshow("Virtual Mouse with Face Auth & Voice (Vosk)", img)

    # Keyboard controls: q = quit, s = smooth cursor mode, f = fast cursor mode
    key = cv2.waitKey(1) & 0xFF
    if key == ord('q'):
        break
    elif key == ord('s'):
        alpha = 0.12
        mode = "SMOOTH"
    elif key == ord('f'):
        alpha = 0.25
        mode = "FAST"

cap.release()
cv2.destroyAllWindows()