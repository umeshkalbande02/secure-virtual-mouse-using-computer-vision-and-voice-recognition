import cv2
import os

# Create faces folder if not exists
if not os.path.exists("faces"):
    os.makedirs("faces")

# Ask for user name
name = input("Enter your name: ").strip()
user_dir = os.path.join("faces", name)
if not os.path.exists(user_dir):
    os.makedirs(user_dir)

# Start webcam
cap = cv2.VideoCapture(0)
cap.set(3, 640)
cap.set(4, 480)

count = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break

    # Mirror the image
    frame = cv2.flip(frame, 1)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Detect face
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        roi = gray[y:y+h, x:x+w]
        file_name = os.path.join(user_dir, f"{count}.jpg")
        cv2.imwrite(file_name, roi)
        count += 1
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

    cv2.imshow("Capturing Faces", frame)

    if cv2.waitKey(1) & 0xFF == ord('q') or count >= 30:
        break

cap.release()
cv2.destroyAllWindows()
print(f"[INFO] Saved {count} face images in {user_dir}")