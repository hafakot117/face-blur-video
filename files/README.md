# Face Blur Video Tool

כלי GUI לטשטוש פנים בסרטונים באמצעות OpenCV.

## התקנה

```bash
git clone https://github.com/hafakot117/face-blur-video.git
cd face-blur-video
bash setup.sh
```

## שימוש

### הפעלה ראשונה
```bash
source venv/bin/activate
python face_blur_gui.py
```

### הפעלות הבאות
בכל פעם שאתה פותח טרמינל חדש:
```bash
cd face-blur-video
source venv/bin/activate
python face_blur_gui.py
```

## דרישות מערכת
- Python 3.8+
- ffmpeg
- macOS או Linux

## תלויות
- opencv-python
- numpy
- pillow
- tkinter (מגיע עם Python)

## פתרון בעיות

### שגיאת "externally-managed-environment"
אם קיבלת שגיאה זו, הרץ:
```bash
python3 -m venv venv
source venv/bin/activate
pip install opencv-python numpy pillow
```

### ffmpeg לא מותקן
**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt-get install ffmpeg
```
