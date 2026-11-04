import cv2
import os
import numpy as np

# Paths
faces_dir = "faces"
trainer_dir = "trainer"
if not os.path.exists(trainer_dir):
    os.makedirs(trainer_dir)

recognizer = cv2.face.LBPHFaceRecognizer_create()
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

current_id = 0
labels = {}
x_train = []
y_labels = []

# Loop through all faces folders
for root, dirs, files in os.walk(faces_dir):
    for file in files:
        if file.endswith("jpg"):
            path = os.path.join(root, file)
            label = os.path.basename(root)  # folder name = user name
            if label not in labels.values():
                labels[current_id] = label
                id_ = current_id
                current_id += 1
            else:
                id_ = list(labels.keys())[list(labels.values()).index(label)]

            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            faces = face_cascade.detectMultiScale(img, 1.3, 5)
            for (x, y, w, h) in faces:
                roi = img[y:y+h, x:x+w]
                x_train.append(roi)
                y_labels.append(id_)

# Train
recognizer.train(x_train, np.array(y_labels))
recognizer.save(os.path.join(trainer_dir, "lbph.yml"))

# Save labels
with open(os.path.join(trainer_dir, "labels.txt"), "w") as f:
    for k, v in labels.items():
        f.write(f"{k}:{v}\n")

print("[INFO] Training done. Model saved in trainer/lbph.yml")