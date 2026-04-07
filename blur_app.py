#!/usr/bin/env python3
from flask import Flask, request, jsonify, send_file
import os, uuid, base64, cv2
from werkzeug.utils import secure_filename
from face_tracker import FaceTracker

VERSION = "2.0"

app = Flask(__name__)
os.makedirs('uploads', exist_ok=True)
os.makedirs('outputs', exist_ok=True)
sessions = {}

@app.route('/')
def index():
    return """<!DOCTYPE html>
<html dir="rtl"><head><meta charset="UTF-8">
<title>🎭 Pro Face Blur v2.0</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;padding:20px}
.container{background:white;border-radius:20px;box-shadow:0 20px 60px rgba(0,0,0,0.3);padding:40px;max-width:1200px;margin:0 auto}
h1{text-align:center;color:#2d3748;margin-bottom:5px}
.version{text-align:center;color:#667eea;font-size:18px;font-weight:bold;margin-bottom:25px}
.step{display:none}
.step.active{display:block}
.upload-area{border:3px dashed #cbd5e0;border-radius:15px;padding:60px;text-align:center;cursor:pointer;transition:all 0.3s}
.upload-area:hover{border-color:#667eea;background:#f7fafc}
.upload-area.dragover{border-color:#48bb78;background:#edf7f0;transform:scale(1.02)}
input[type=file]{display:none}
button{padding:15px 30px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;border:none;border-radius:10px;font-size:16px;font-weight:bold;cursor:pointer;margin:10px}
button:hover{transform:translateY(-2px)}
.face-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px;margin:20px 0}
.face-card{border:3px solid #e2e8f0;border-radius:15px;padding:20px;text-align:center;cursor:pointer;transition:all 0.3s}
.face-card:hover{transform:translateY(-5px);box-shadow:0 10px 20px rgba(0,0,0,0.15)}
.face-card.selected{border-color:#48bb78;background:#f0fff4}
.face-card img{max-width:100%;height:200px;object-fit:cover;border-radius:10px;margin-bottom:15px}
.face-card h3{margin:10px 0;color:#2d3748}
.face-card p{color:#718096;font-size:14px;margin-bottom:15px}
.blur-controls{background:#f7fafc;border-radius:15px;padding:30px;margin:30px 0}
.control-group{margin:20px 0}
.control-group label{display:block;margin-bottom:10px;font-weight:bold;color:#2d3748;font-size:16px}
.blur-type-selector{display:flex;gap:10px;flex-wrap:wrap}
.blur-type-btn{flex:1;min-width:150px;padding:20px;border:3px solid #e2e8f0;background:white;border-radius:10px;cursor:pointer;transition:all 0.3s;text-align:center}
.blur-type-btn:hover{border-color:#667eea;transform:translateY(-2px)}
.blur-type-btn.active{border-color:#667eea;background:#eef2ff}
.blur-type-btn .icon{font-size:32px;margin-bottom:10px}
.blur-type-btn .name{font-weight:bold;color:#2d3748}
input[type=range]{width:100%;height:8px;border-radius:5px;background:#e2e8f0}
.blur-value{text-align:center;color:#667eea;font-size:32px;font-weight:bold;margin-top:15px}
.msg{padding:15px;border-radius:10px;margin:15px 0;text-align:center}
.success{background:#c8e6c9;color:#2e7d32}
.info{background:#e3f2fd;color:#1976d2}
</style>
</head><body>
<div class="container">
<h1>🎭 Pro Face Blur Studio</h1>
<div class="version">גרסה 2.0 - טשטוש מתקדם</div>

<div id="step1" class="step active">
<div class="upload-area" id="dropZone">
<div style="font-size:60px">📹</div>
<h2>גרור וידאו לכאן</h2>
<p style="margin-top:10px;color:#718096">או לחץ לבחירה</p>
<input type="file" id="file" accept="video/*">
</div>
<div id="msg1"></div>
</div>

<div id="step2" class="step">
<h2 style="text-align:center;margin-bottom:20px">בחר פרצופים לטשטש</h2>
<div id="faceGrid" class="face-grid"></div>
<div class="blur-controls">
<div class="control-group">
<label>סוג טשטוש</label>
<div class="blur-type-selector">
<div class="blur-type-btn active" data-type="gaussian" onclick="selectBlurType('gaussian')">
<div class="icon">🌫️</div>
<div class="name">גאוסיאני</div>
</div>
<div class="blur-type-btn" data-type="oval" onclick="selectBlurType('oval')">
<div class="icon">⭕</div>
<div class="name">אובלי</div>
</div>
<div class="blur-type-btn" data-type="pixelate" onclick="selectBlurType('pixelate')">
<div class="icon">🔲</div>
<div class="name">מוזאיק</div>
</div>
</div>
</div>
<div class="control-group">
<label>עוצמת טשטוש</label>
<input type="range" id="blurSlider" min="51" max="251" step="20" value="151">
<div class="blur-value" id="blurValue">151</div>
</div>
</div>
<div style="text-align:center">
<button onclick="processVideo()">🎬 עבד וידאו</button>
</div>
</div>

<div id="step3" class="step">
<h2 style="text-align:center">מעבד...</h2>
<div id="msg3" class="info">מעבד וידאו...</div>
</div>

<div id="step4" class="step">
<h2 style="text-align:center">✅ מוכן!</h2>
<button onclick="download()">⬇️ הורד</button>
<button onclick="location.reload()">🔄 חדש</button>
</div>

</div>
<script>
let sessionId, selectedFaces = new Set(), downloadUrl, blurType = 'gaussian';
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('file');

['dragenter','dragover','dragleave','drop'].forEach(e => {
  dropZone.addEventListener(e, ev => {ev.preventDefault();ev.stopPropagation();});
});

dropZone.addEventListener('dragenter', () => dropZone.classList.add('dragover'));
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('dragover', () => dropZone.classList.add('dragover'));

dropZone.addEventListener('drop', e => {
  dropZone.classList.remove('dragover');
  if(e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
});

dropZone.addEventListener('click', () => fileInput.click());
fileInput.onchange = e => e.target.files[0] && handleFile(e.target.files[0]);

document.getElementById('blurSlider').oninput = e => {
  document.getElementById('blurValue').textContent = e.target.value;
};

function selectBlurType(type){
  blurType = type;
  document.querySelectorAll('.blur-type-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelector(`[data-type="${type}"]`).classList.add('active');
}

async function handleFile(file){
  document.getElementById('msg1').innerHTML = '<div class="info">מעלה...</div>';
  const fd = new FormData();
  fd.append('video', file);
  try {
    const r = await fetch('/upload', {method:'POST', body:fd});
    const d = await r.json();
    if(d.success){
      sessionId = d.session_id;
      document.getElementById('msg1').innerHTML = '<div class="success">✅ הועלה!</div>';
      setTimeout(analyzeVideo, 1000);
    }
  } catch(e) {
    document.getElementById('msg1').innerHTML = '<div class="info">שגיאה</div>';
  }
}

async function analyzeVideo(){
  document.getElementById('step1').classList.remove('active');
  document.getElementById('step3').classList.add('active');
  try {
    const r = await fetch('/analyze/' + sessionId);
    const d = await r.json();
    if(d.success) showFaceSelection(d);
  } catch(e) {}
}

function showFaceSelection(data){
  document.getElementById('step3').classList.remove('active');
  document.getElementById('step2').classList.add('active');
  const grid = document.getElementById('faceGrid');
  grid.innerHTML = '';
  data.faces.forEach(face => {
    const card = document.createElement('div');
    card.className = 'face-card';
    card.innerHTML = `<img src="data:image/jpeg;base64,${face.thumbnail}">
      <h3>פרצוף ${face.id}</h3>
      <p>${face.appearances} הופעות</p>
      <button onclick="toggleFace(${face.id}, this)">בחר</button>`;
    grid.appendChild(card);
  });
}

function toggleFace(id, btn){
  if(selectedFaces.has(id)){
    selectedFaces.delete(id);
    btn.parentElement.classList.remove('selected');
    btn.textContent = 'בחר';
  } else {
    selectedFaces.add(id);
    btn.parentElement.classList.add('selected');
    btn.textContent = '✓ נבחר';
  }
}

async function processVideo(){
  if(!selectedFaces.size) return alert('בחר פרצוף');
  document.getElementById('step2').classList.remove('active');
  document.getElementById('step3').classList.add('active');
  const fd = new FormData();
  fd.append('faces', JSON.stringify([...selectedFaces]));
  fd.append('blur', document.getElementById('blurSlider').value);
  fd.append('blur_type', blurType);
  try {
    const r = await fetch('/process/' + sessionId, {method:'POST', body:fd});
    const d = await r.json();
    if(d.success){
      downloadUrl = d.download_url;
      document.getElementById('step3').classList.remove('active');
      document.getElementById('step4').classList.add('active');
    }
  } catch(e) {}
}

function download(){
  window.location.href = downloadUrl;
}
</script>
</body></html>"""

def get_thumbnails(tracker, faces):
    thumbs = {}
    for uid, data in faces.items():
        tracker.cap.set(cv2.CAP_PROP_POS_FRAMES, data['best_frame'])
        ret, frame = tracker.cap.read()
        if ret:
            x, y, w, h = data['best_bbox']
            pad = int(h * 0.4)
            y1, y2 = max(0, y - pad), min(frame.shape[0], y + h + pad)
            x1, x2 = max(0, x - pad), min(frame.shape[1], x + w + pad)
            crop = frame[y1:y2, x1:x2]
            if crop.shape[0] > 250 or crop.shape[1] > 250:
                scale = 250 / max(crop.shape[:2])
                crop = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)))
            _, buf = cv2.imencode('.jpg', crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            thumbs[uid] = base64.b64encode(buf).decode()
    tracker.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return thumbs

@app.route('/upload', methods=['POST'])
def upload():
    try:
        f = request.files['video']
        sid = str(uuid.uuid4())
        inp = f'uploads/{sid}.{f.filename.rsplit(".", 1)[1]}'
        f.save(inp)
        sessions[sid] = {'input': inp}
        return jsonify({'success': True, 'session_id': sid})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/analyze/<sid>')
def analyze(sid):
    try:
        sess = sessions[sid]
        tracker = FaceTracker(sess['input'])
        sampled = tracker.sample_video_frames(sample_interval=10)
        unique = tracker.cluster_faces(sampled)
        thumbs = get_thumbnails(tracker, unique)
        sess['tracker'] = tracker
        tracker.unique_faces = unique
        faces = [{'id': uid, 'appearances': len(d['frames']), 'thumbnail': thumbs[uid]} for uid, d in unique.items()]
        return jsonify({'success': True, 'faces': faces})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/process/<sid>', methods=['POST'])
def process(sid):
    try:
        import json
        faces = json.loads(request.form['faces'])
        blur = int(request.form.get('blur', 151))
        btype = request.form.get('blur_type', 'gaussian')
        sess = sessions[sid]
        out = f'outputs/{sid}_blurred.mp4'
        sess['tracker'].process_video(out, faces, blur_type=btype, blur_strength=blur)
        return jsonify({'success': True, 'download_url': f'/download/{sid}_blurred.mp4'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/download/<fn>')
def download(fn):
    return send_file(f'outputs/{secure_filename(fn)}', as_attachment=True)

if __name__ == '__main__':
    print("\n" + "="*60)
    print(f"🎭 Pro Face Blur Studio v{VERSION}")
    print("="*60)
    print("\n📱 http://localhost:5000\n")
    app.run(host='0.0.0.0', port=5000)
